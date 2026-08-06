from __future__ import annotations

from pathlib import Path
from queue import Queue
import tempfile
import time
import unittest

import yaml

from app.core.modbus.config import ModbusConfig
from app.core.modbus.data_types import (
    decode_bits,
    encode_bits,
    scaled_uint16_to_score,
    score_to_scaled_uint16,
    uint32_to_words,
    words_to_uint32,
)
from app.core.modbus.protocol import ProtocolEngine, ProtocolEventType
from app.core.modbus.register_map import PcStatusBits, ResultCode, VisionErrorCode
from app.core.modbus.state import ModbusSharedState, decode_plc_block
from app.core.modbus.worker import ModbusWorker
from app.core.recipes import RecipeNotFoundError, RecipeRepository, RecipeRevisionError
from app.core.results.inspection_result import InspectionResult


def valid_config(*, enabled: bool = True) -> ModbusConfig:
    return ModbusConfig.from_mapping({
        "connection": {
            "enabled": enabled, "host": "127.0.0.1", "port": 502, "device_id": 1,
            "poll_interval_ms": 10, "request_timeout_ms": 20, "reconnect_interval_ms": 10,
            "heartbeat_interval_ms": 10, "heartbeat_timeout_ms": 30, "max_consecutive_failures": 3,
        },
        "registers": {
            "plc_to_pc": {"start": 100, "count": 20},
            "pc_to_plc": {"start": 120, "count": 30},
        },
        "data_format": {"uint32_word_order": "low_high", "score_scale": 10000, "line_speed_scale": 100},
        "behavior": {"require_modbus_for_inspection": True, "retain_unacknowledged_result": True, "simulation_mode": True},
    })


class FakeTransport:
    def __init__(self, registers: list[int] | None = None, *, connect_result: bool = True):
        self.registers = registers or [0] * 20
        self.connect_result = connect_result
        self.writes: list[tuple[int, list[int]]] = []
        self.closed = False

    def connect(self) -> bool:
        return self.connect_result

    def close(self) -> None:
        self.closed = True

    def read_holding_registers(self, _address: int, count: int) -> list[int]:
        del _address
        return list(self.registers[:count])

    def write_registers(self, address: int, values: list[int]) -> None:
        self.writes.append((address, list(values)))


class DataTypeTests(unittest.TestCase):
    def test_uint32_low_high_round_trip(self):
        low, high = uint32_to_words(0xDEADBEEF)
        self.assertEqual((low, high), (0xBEEF, 0xDEAD))
        self.assertEqual(words_to_uint32(low, high), 0xDEADBEEF)

    def test_bits_and_scores_saturate(self):
        self.assertEqual(decode_bits(encode_bits([0, 3, 15])), frozenset({0, 3, 15}))
        self.assertEqual(score_to_scaled_uint16(-1.0), 0)
        self.assertEqual(score_to_scaled_uint16(99.0), 0xFFFF)
        self.assertAlmostEqual(scaled_uint16_to_score(1234), 0.1234)


class ProtocolTests(unittest.TestCase):
    def test_plc_block_decode_and_sequence_events(self):
        block = [0] * 20
        block[0] = 0b111
        block[1:5] = [12, 3, 0x4321, 0x1234]
        block[9:11] = [9, 0]
        block[13:15] = [1, 7]
        plc = decode_plc_block(block)
        self.assertTrue(plc.inspection_enabled)
        self.assertTrue(plc.bypass_requested)
        self.assertEqual(plc.recipe_change_sequence, 0x12344321)
        engine = ProtocolEngine()
        first = engine.observe(plc)
        self.assertIn(ProtocolEventType.RECIPE_CHANGE, [event.type for event in first])
        self.assertIn(ProtocolEventType.COMMAND, [event.type for event in first])
        self.assertEqual(len([event for event in first if event.type is ProtocolEventType.RECIPE_CHANGE]), 1)
        self.assertEqual(engine.observe(plc), [])
        changed = decode_plc_block(block[:14] + [8] + block[15:])
        self.assertEqual([event.type for event in engine.observe(changed)], [ProtocolEventType.COMMAND])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = ModbusSharedState(enabled=True)
        self.state.set_camera_ready(0, True)
        self.state.set_camera_ready(1, True)
        self.state.set_camera_ready(2, True)
        self.state.set_camera_ready(3, True)
        self.state.set_model_ready_mask(0xF)
        self.state.set_recipe_loaded(12, 3, 0x10002)
        self.state.set_inspection_ready(True)

    def test_pc_block_encoding_and_ready_masks(self):
        registers = self.state.pc_snapshot().registers
        self.assertTrue(registers[0] & PcStatusBits.ALL_CAMERAS_READY)
        self.assertTrue(registers[0] & PcStatusBits.ALL_MODELS_READY)
        self.assertEqual(registers[1:5], (12, 3, 2, 1))

    def test_result_pending_requires_matching_ack(self):
        self.state.queue_result(InspectionResult(1, 12, 3, ResultCode.OK, True, (0.1,) * 4, 0.1))
        snapshot = self.state.pc_snapshot()
        self.assertTrue(snapshot.result_needs_publication)
        sequence = snapshot.current_result_sequence
        assert sequence is not None
        self.assertTrue(self.state.mark_result_published(sequence))
        self.assertFalse(self.state.acknowledge_result(sequence + 1))
        self.assertTrue(self.state.pc_snapshot().status_word & PcStatusBits.RESULT_PENDING)
        self.assertTrue(self.state.acknowledge_result(sequence))
        self.assertFalse(self.state.pc_snapshot().status_word & PcStatusBits.RESULT_PENDING)

    def test_result_gets_a_sequence_before_publication(self):
        result = InspectionResult(1, 0, 0, ResultCode.OK, True)
        assigned = self.state.enqueue_result(result)
        assert assigned is not None
        self.assertEqual(assigned.sequence, 1)
        self.assertEqual(self.state.current_result().sequence, 1)

    def test_result_queue_does_not_overwrite_unacknowledged_result(self):
        self.assertTrue(self.state.queue_result(InspectionResult(1, 0, 0, ResultCode.OK, True)))
        self.assertTrue(self.state.queue_result(InspectionResult(2, 0, 0, ResultCode.OK, True)))
        self.assertFalse(self.state.queue_result(InspectionResult(3, 0, 0, ResultCode.OK, True)))
        self.assertEqual(self.state.pc_snapshot().registers[18], VisionErrorCode.RESULT_QUEUE_FULL)

    def test_modbus_disabled_mode(self):
        disabled = ModbusSharedState(enabled=False)
        disabled.set_inspection_ready(True)
        self.assertTrue(disabled.inspection_allowed(require_modbus=False))
        self.assertFalse(disabled.health_snapshot().degraded)


class WorkerTests(unittest.TestCase):
    def test_result_sequence_is_written_after_data(self):
        state = ModbusSharedState(enabled=True)
        state.queue_result(InspectionResult(1, 0, 0, ResultCode.NG, False, (0.4,) * 4, 0.4))
        fake = FakeTransport()
        worker = ModbusWorker(valid_config(), state, Queue(), transport_factory=lambda: fake)
        worker._transport = fake
        worker._publish_pc_snapshot()
        self.assertEqual([address for address, _ in fake.writes], [120, 127, 125, 120])
        self.assertTrue(state.pc_snapshot().status_word & PcStatusBits.RESULT_PENDING)

    def test_heartbeat_timeout_and_wraparound_changes(self):
        worker = ModbusWorker(valid_config(), ModbusSharedState(enabled=True), Queue(), transport_factory=FakeTransport)
        now = time.monotonic()
        self.assertTrue(worker._observe_plc_heartbeat(65535, now))
        self.assertTrue(worker._observe_plc_heartbeat(0, now + 0.01))
        self.assertFalse(worker._observe_plc_heartbeat(0, now + 1.0))

    def test_worker_stops_cleanly(self):
        fake = FakeTransport([0] * 20, connect_result=False)
        worker = ModbusWorker(valid_config(), ModbusSharedState(enabled=True), Queue(), transport_factory=lambda: fake)
        worker.start()
        time.sleep(0.02)
        worker.stop()
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(fake.closed)

    def test_three_failures_enter_degraded_state(self):
        state = ModbusSharedState(enabled=True)
        worker = ModbusWorker(valid_config(), state, Queue(), transport_factory=FakeTransport)
        worker._transport = FakeTransport()
        from app.core.modbus.client import ModbusTransportError
        worker._handle_failure(ModbusTransportError("offline"))
        worker._handle_failure(ModbusTransportError("offline"))
        worker._handle_failure(ModbusTransportError("offline"))
        self.assertTrue(state.health_snapshot().degraded)
        self.assertEqual(state.pc_snapshot().registers[18], VisionErrorCode.MODBUS_CONNECTION_UNAVAILABLE)


class RecipeTests(unittest.TestCase):
    def test_recipe_mapping_and_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configs").mkdir()
            (root / "configs" / "model.yaml").write_text("models:\n  cam1: {path: fake, type: mock}\n", encoding="utf-8")
            (root / "configs" / "thresholds.yaml").write_text("ok_threshold: 0.5\ninput_size: [280, 280]\n", encoding="utf-8")
            recipes = {"recipes": {"default": {"id": 0, "revision": 2}}}
            recipe_path = root / "configs" / "recipes.yaml"
            recipe_path.write_text(yaml.safe_dump(recipes), encoding="utf-8")
            repository = RecipeRepository.from_yaml(recipe_path, root)
            self.assertEqual(repository.load(0, 2).definition.revision, 2)
            with self.assertRaises(RecipeNotFoundError):
                repository.load(7, 0)
            with self.assertRaises(RecipeRevisionError):
                repository.load(0, 3)

    def test_recipe_loading_failure_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configs").mkdir()
            recipe_path = root / "configs" / "recipes.yaml"
            recipe_path.write_text("recipes:\n  default: {id: 0, revision: 0}\n", encoding="utf-8")
            repository = RecipeRepository.from_yaml(recipe_path, root)
            with self.assertRaises(ValueError):
                repository.load(0, 0)

    def test_invalid_modbus_config(self):
        raw = {"connection": {"enabled": True}}
        with self.assertRaises(ValueError):
            ModbusConfig.from_mapping(raw)


if __name__ == "__main__":
    unittest.main()