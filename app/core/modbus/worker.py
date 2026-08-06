"""Dedicated blocking Modbus/TCP worker; no UI, camera, or inference work runs here."""

from __future__ import annotations

from queue import Queue
import threading
import time
from typing import Callable

from app.core.logger import jlog

from .client import ModbusTransport, ModbusTransportError, PymodbusTcpTransport
from .config import ModbusConfig
from .protocol import ProtocolEngine, ProtocolEventType
from .register_map import PcStatusBits, VisionErrorCode, VisionWarningCode
from .state import ModbusSharedState, decode_plc_block


class ModbusWorker(threading.Thread):
    """Own the one Modbus connection and exchange snapshots with the application."""

    def __init__(
        self,
        config: ModbusConfig,
        state: ModbusSharedState,
        event_queue: Queue[tuple[str, object]],
        transport_factory: Callable[[], ModbusTransport] | None = None,
    ):
        super().__init__(name="ModbusTcpWorker", daemon=False)
        self._config = config
        self._state = state
        self._events = event_queue
        self._transport_factory = transport_factory or (lambda: PymodbusTcpTransport(config.connection))
        self._stop_event = threading.Event()
        self._protocol = ProtocolEngine()
        self._transport: ModbusTransport | None = None
        self._consecutive_failures = 0
        self._last_plc_heartbeat: int | None = None
        self._last_plc_heartbeat_change = 0.0
        self._last_pc_heartbeat = 0.0
        self._last_failure_log = 0.0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if not self._config.enabled:
            self._state.set_connection(False, heartbeat_valid=False)
            return
        try:
            self._transport = self._transport_factory()
            self._run_loop()
        finally:
            if self._transport is not None:
                try:
                    self._transport.close()
                except ModbusTransportError as exc:
                    jlog("modbus_close_error", error=str(exc))
            self._state.set_connection(False, self._consecutive_failures, heartbeat_valid=False)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._ensure_connected()
                self._poll_once(started)
                self._consecutive_failures = 0
            except ModbusTransportError as exc:
                self._handle_failure(exc)
                self._stop_event.wait(self._config.connection.reconnect_interval_ms / 1000.0)
                continue
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.0, self._config.connection.poll_interval_ms / 1000.0 - elapsed))

    def _ensure_connected(self) -> None:
        assert self._transport is not None
        if not self._transport.connect():
            raise ModbusTransportError("Modbus TCP connection unavailable")

    def _poll_once(self, now: float) -> None:
        assert self._transport is not None
        registers = self._transport.read_holding_registers(
            self._config.registers.plc_to_pc_start,
            self._config.registers.plc_to_pc_count,
        )
        plc = decode_plc_block(registers)
        self._state.update_plc_input(plc)
        if plc.save_training_images:
            self._state.set_warning(VisionWarningCode.TRAINING_IMAGE_COLLECTION_ENABLED)
        else:
            self._state.clear_warning_if(VisionWarningCode.TRAINING_IMAGE_COLLECTION_ENABLED)
        heartbeat_valid = self._observe_plc_heartbeat(plc.heartbeat, now)
        became_changed = self._state.set_connection(True, 0, heartbeat_valid)
        if became_changed and heartbeat_valid:
            self._state.set_warning(VisionWarningCode.COMMUNICATION_RECOVERED)
            self._events.put(("health_recovered", None))
        if not heartbeat_valid:
            self._state.set_error(VisionErrorCode.PLC_HEARTBEAT_TIMEOUT)
            self._events.put(("health_degraded", VisionErrorCode.PLC_HEARTBEAT_TIMEOUT))
        for event in self._protocol.observe(plc):
            if event.type is ProtocolEventType.RECIPE_CHANGE:
                self._state.begin_recipe_change()
                self._events.put(("recipe_change", event))
            elif event.type is ProtocolEventType.COMMAND:
                self._events.put(("command", event))
            elif event.type is ProtocolEventType.RESULT_ACK:
                self._state.acknowledge_result(event.sequence)

        if now - self._last_pc_heartbeat >= self._config.connection.heartbeat_interval_ms / 1000.0:
            self._state.increment_pc_heartbeat()
            self._last_pc_heartbeat = now
        self._publish_pc_snapshot()
        if self._state.is_result_ack_delayed(self._config.connection.heartbeat_timeout_ms / 1000.0):
            self._state.set_warning(VisionWarningCode.RESULT_ACK_DELAYED)
            self._state.set_error(VisionErrorCode.RESULT_ACK_TIMEOUT)

    def _observe_plc_heartbeat(self, heartbeat: int, now: float) -> bool:
        if self._last_plc_heartbeat is None or heartbeat != self._last_plc_heartbeat:
            self._last_plc_heartbeat = heartbeat
            self._last_plc_heartbeat_change = now
            return True
        return now - self._last_plc_heartbeat_change < self._config.connection.heartbeat_timeout_ms / 1000.0

    def _publish_pc_snapshot(self) -> None:
        assert self._transport is not None
        snapshot = self._state.pc_snapshot()
        start = self._config.registers.pc_to_plc_start
        if snapshot.result_needs_publication and snapshot.current_result_sequence is not None:
            # Data first; RESULT_SEQ is committed only after all data has been written.
            data = list(snapshot.registers)
            no_pending_status = data[0] & ~int(PcStatusBits.RESULT_PENDING)
            self._transport.write_registers(start, [no_pending_status, *data[1:5]])
            self._transport.write_registers(start + 7, data[7:])
            self._transport.write_registers(start + 5, data[5:7])
            self._state.mark_result_published(snapshot.current_result_sequence)
            committed = self._state.pc_snapshot()
            self._transport.write_registers(start, [committed.status_word])
        else:
            self._transport.write_registers(start, list(snapshot.registers))

    def _handle_failure(self, exc: ModbusTransportError) -> None:
        self._consecutive_failures += 1
        if self._transport is not None:
            try:
                self._transport.close()
            except ModbusTransportError:
                pass
        self._state.set_connection(False, self._consecutive_failures, heartbeat_valid=False)
        if self._consecutive_failures >= self._config.connection.max_consecutive_failures:
            self._state.set_error(VisionErrorCode.MODBUS_CONNECTION_UNAVAILABLE)
            self._events.put(("health_degraded", VisionErrorCode.MODBUS_CONNECTION_UNAVAILABLE))
        now = time.monotonic()
        if now - self._last_failure_log >= self._config.connection.reconnect_interval_ms / 1000.0:
            jlog("modbus_failure", failures=self._consecutive_failures, error=str(exc))
            self._last_failure_log = now