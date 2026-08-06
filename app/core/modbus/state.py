"""Thread-safe PLC input and PC output state with no network operations."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import TYPE_CHECKING

from .data_types import score_to_scaled_uint16, uint32_to_words, words_to_uint32
from .register_map import (
    PC_TO_PLC_COUNT,
    PC_TO_PLC_START,
    PLC_TO_PC_COUNT,
    PLC_TO_PC_START,
    PcStatusBits,
    ResultCode,
    VisionErrorCode,
    VisionWarningCode,
)

if TYPE_CHECKING:
    from app.core.results.inspection_result import InspectionResult


@dataclass(frozen=True, slots=True)
class PlcInput:
    control_word: int = 0
    requested_recipe_id: int = 0
    requested_recipe_revision: int = 0
    recipe_change_sequence: int = 0
    line_speed_x100: int = 0
    line_state: int = 0
    trigger_sequence: int = 0
    result_ack_sequence: int = 0
    heartbeat: int = 0
    fault_code: int = 0
    command_code: int = 0
    command_sequence: int = 0
    product_length_mm: int = 0

    @property
    def inspection_enabled(self) -> bool:
        return bool(self.control_word & (1 << 0))

    @property
    def bypass_requested(self) -> bool:
        return bool(self.control_word & (1 << 1))

    @property
    def save_training_images(self) -> bool:
        return bool(self.control_word & (1 << 2))


@dataclass(frozen=True, slots=True)
class ModbusHealth:
    enabled: bool = False
    connected: bool = False
    heartbeat_valid: bool = False
    consecutive_failures: int = 0
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class PcSnapshot:
    registers: tuple[int, ...]
    status_word: int
    current_result_sequence: int | None
    result_needs_publication: bool


def decode_plc_block(registers: list[int] | tuple[int, ...]) -> PlcInput:
    """Decode the configured 100–119 zero-based PLC input block."""
    if len(registers) != PLC_TO_PC_COUNT:
        raise ValueError(f"Expected {PLC_TO_PC_COUNT} PLC registers, got {len(registers)}")
    r = [int(value) & 0xFFFF for value in registers]
    return PlcInput(
        control_word=r[0],
        requested_recipe_id=r[1],
        requested_recipe_revision=r[2],
        recipe_change_sequence=words_to_uint32(r[3], r[4]),
        line_speed_x100=r[5],
        line_state=r[6],
        trigger_sequence=words_to_uint32(r[7], r[8]),
        result_ack_sequence=words_to_uint32(r[9], r[10]),
        heartbeat=r[11],
        fault_code=r[12],
        command_code=r[13],
        command_sequence=r[14],
        product_length_mm=r[15],
    )


class ModbusSharedState:
    """Lock-protected state exchanged between app threads and Modbus worker.

    The lock only protects in-memory snapshots.  No socket or DIO operation is
    performed while it is held.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        score_scale: int = 10_000,
        max_result_queue: int = 1,
        required_camera_mask: int = 0x000F,
        required_model_mask: int = 0x000F,
    ):
        self._lock = threading.RLock()
        self._enabled = enabled
        self._score_scale = score_scale
        self._max_result_queue = max_result_queue
        self._required_camera_mask = required_camera_mask & 0xFFFF
        self._required_model_mask = required_model_mask & 0xFFFF
        self._plc = PlcInput()
        self._health = ModbusHealth(enabled=enabled, degraded=enabled)
        self._app_alive = True
        self._camera_ready_mask = 0
        self._model_ready_mask = 0
        self._active_recipe_id = 0
        self._active_recipe_revision = 0
        self._recipe_ack_sequence = 0
        self._recipe_loaded = False
        self._inspection_ready = False
        self._inspection_busy = False
        self._pc_heartbeat = 0
        self._error_code = VisionErrorCode.NONE
        self._warning_code = VisionWarningCode.NONE
        self._command_ack_sequence = 0
        self._dropped_trigger_count = 0
        self._missing_frame_count = 0
        self._processed_count = 0
        self._current_result: InspectionResult | None = None
        self._queued_results: deque[InspectionResult] = deque()
        self._result_published = False
        self._result_published_at: float | None = None
        self._next_result_sequence = 0

    def plc_snapshot(self) -> PlcInput:
        with self._lock:
            return self._plc

    def health_snapshot(self) -> ModbusHealth:
        with self._lock:
            return self._health

    def update_plc_input(self, plc: PlcInput) -> None:
        with self._lock:
            self._plc = plc

    def set_connection(self, connected: bool, consecutive_failures: int = 0, heartbeat_valid: bool = False) -> bool:
        """Update health and return whether the degraded state changed."""
        with self._lock:
            degraded = self._enabled and (not connected or not heartbeat_valid)
            previous = self._health.degraded
            if degraded:
                self._inspection_ready = False
            self._health = ModbusHealth(
                enabled=self._enabled,
                connected=connected,
                heartbeat_valid=heartbeat_valid,
                consecutive_failures=consecutive_failures,
                degraded=degraded,
            )
            return degraded != previous

    def set_application_alive(self, alive: bool) -> None:
        with self._lock:
            self._app_alive = alive

    def set_camera_ready(self, camera_id: int, ready: bool) -> None:
        if not 0 <= camera_id < 16:
            raise ValueError("camera ID must fit in a readiness mask")
        with self._lock:
            if ready:
                self._camera_ready_mask |= 1 << camera_id
            else:
                self._camera_ready_mask &= ~(1 << camera_id)

    def set_model_ready_mask(self, mask: int) -> None:
        with self._lock:
            self._model_ready_mask = mask & 0xFFFF

    def set_recipe_loaded(self, recipe_id: int, revision: int, request_sequence: int) -> None:
        with self._lock:
            self._active_recipe_id = recipe_id & 0xFFFF
            self._active_recipe_revision = revision & 0xFFFF
            self._recipe_ack_sequence = request_sequence & 0xFFFFFFFF
            self._recipe_loaded = True
            self._error_code = VisionErrorCode.NONE

    def active_recipe(self) -> tuple[int, int]:
        with self._lock:
            return self._active_recipe_id, self._active_recipe_revision

    def begin_recipe_change(self) -> None:
        with self._lock:
            self._recipe_loaded = False
            self._inspection_ready = False

    def set_inspection_ready(self, ready: bool) -> None:
        with self._lock:
            self._inspection_ready = ready

    def set_inspection_busy(self, busy: bool) -> None:
        with self._lock:
            self._inspection_busy = busy

    def increment_pc_heartbeat(self) -> int:
        with self._lock:
            self._pc_heartbeat = (self._pc_heartbeat + 1) & 0xFFFF
            return self._pc_heartbeat

    def set_error(self, code: VisionErrorCode) -> None:
        with self._lock:
            self._error_code = code

    def set_warning(self, code: VisionWarningCode) -> None:
        with self._lock:
            self._warning_code = code

    def clear_warning_if(self, code: VisionWarningCode) -> None:
        with self._lock:
            if self._warning_code == code:
                self._warning_code = VisionWarningCode.NONE

    def clear_error(self) -> None:
        self.set_error(VisionErrorCode.NONE)

    def reset_counters(self) -> None:
        with self._lock:
            self._dropped_trigger_count = 0
            self._missing_frame_count = 0
            self._processed_count = 0

    def acknowledge_command(self, command_sequence: int) -> None:
        with self._lock:
            self._command_ack_sequence = command_sequence & 0xFFFF

    def increment_dropped_trigger_count(self) -> None:
        with self._lock:
            self._dropped_trigger_count = (self._dropped_trigger_count + 1) & 0xFFFFFFFF

    def increment_missing_frame_count(self) -> None:
        with self._lock:
            self._missing_frame_count = (self._missing_frame_count + 1) & 0xFFFFFFFF

    def increment_processed_count(self) -> None:
        with self._lock:
            self._processed_count = (self._processed_count + 1) & 0xFFFFFFFF

    def enqueue_result(self, result: InspectionResult) -> InspectionResult | None:
        """Assign a sequence and queue a result without overwriting an unacknowledged one."""
        with self._lock:
            self._next_result_sequence = (self._next_result_sequence + 1) & 0xFFFFFFFF
            result = result.with_sequence(self._next_result_sequence)
            if self._current_result is None:
                self._current_result = result
                self._result_published = False
                self._result_published_at = None
                return result
            if len(self._queued_results) >= self._max_result_queue:
                self._error_code = VisionErrorCode.RESULT_QUEUE_FULL
                self._dropped_trigger_count = (self._dropped_trigger_count + 1) & 0xFFFFFFFF
                return None
            self._queued_results.append(result)
            return result

    def queue_result(self, result: InspectionResult) -> bool:
        """Compatibility helper returning whether a result was accepted into the bounded queue."""
        return self.enqueue_result(result) is not None

    def current_result(self) -> InspectionResult | None:
        with self._lock:
            return self._current_result

    def mark_result_published(self, sequence: int) -> bool:
        with self._lock:
            if self._current_result is None or self._current_result.sequence != sequence:
                return False
            self._result_published = True
            self._result_published_at = time.monotonic()
            return True

    def acknowledge_result(self, sequence: int) -> bool:
        """Clear pending only for the exact result sequence acknowledged by PLC."""
        with self._lock:
            if not self._result_published or self._current_result is None:
                return False
            if self._current_result.sequence != sequence:
                return False
            self._current_result = self._queued_results.popleft() if self._queued_results else None
            self._result_published = False
            self._result_published_at = None
            return True

    def is_result_ack_delayed(self, timeout_s: float) -> bool:
        with self._lock:
            return bool(
                self._result_published_at is not None
                and time.monotonic() - self._result_published_at >= timeout_s
            )

    def inspection_allowed(self, require_modbus: bool) -> bool:
        with self._lock:
            communication_ok = not require_modbus or (self._health.connected and self._health.heartbeat_valid)
            return self._app_alive and self._inspection_ready and communication_ok

    def pc_snapshot(self) -> PcSnapshot:
        """Create an immutable PC register image without any network operation."""
        with self._lock:
            status = PcStatusBits(0)
            if self._app_alive:
                status |= PcStatusBits.APPLICATION_ALIVE
            if (self._camera_ready_mask & self._required_camera_mask) == self._required_camera_mask:
                status |= PcStatusBits.ALL_CAMERAS_READY
            if (self._model_ready_mask & self._required_model_mask) == self._required_model_mask:
                status |= PcStatusBits.ALL_MODELS_READY
            if self._recipe_loaded:
                status |= PcStatusBits.REQUESTED_RECIPE_LOADED
            if self._inspection_ready:
                status |= PcStatusBits.INSPECTION_READY
            if self._inspection_busy:
                status |= PcStatusBits.INSPECTION_BUSY
            if self._result_published:
                status |= PcStatusBits.RESULT_PENDING
            if self._warning_code != VisionWarningCode.NONE:
                status |= PcStatusBits.WARNING_ACTIVE
            if self._error_code != VisionErrorCode.NONE:
                status |= PcStatusBits.FAULT_ACTIVE
            if self._plc.bypass_requested:
                status |= PcStatusBits.BYPASS_ACTIVE
            if self._plc.save_training_images:
                status |= PcStatusBits.TRAINING_IMAGE_COLLECTION_ACTIVE
            if self._health.degraded:
                status |= PcStatusBits.COMMUNICATION_DEGRADED

            result = self._current_result
            result_sequence = result.sequence if result else 0
            recipe_lo, recipe_hi = uint32_to_words(self._recipe_ack_sequence)
            result_lo, result_hi = uint32_to_words(result_sequence)
            dropped_lo, dropped_hi = uint32_to_words(self._dropped_trigger_count)
            missing_lo, missing_hi = uint32_to_words(self._missing_frame_count)
            processed_lo, processed_hi = uint32_to_words(self._processed_count)
            scores = list(result.per_camera_scores if result else ())[:4]
            scores += [0.0] * (4 - len(scores))
            registers = [0] * PC_TO_PLC_COUNT
            registers[0] = int(status)
            registers[1] = self._active_recipe_id
            registers[2] = self._active_recipe_revision
            registers[3], registers[4] = recipe_lo, recipe_hi
            registers[5], registers[6] = result_lo, result_hi
            registers[7] = int(result.result_code) if result else int(ResultCode.NO_VALID_RESULT)
            registers[8] = result.ng_camera_mask if result else 0
            registers[9] = score_to_scaled_uint16(result.fused_score if result else 0.0, self._score_scale)
            for index, score in enumerate(scores):
                registers[10 + index] = score_to_scaled_uint16(score, self._score_scale)
            registers[14] = min(0xFFFF, int(round(result.inference_time_ms))) if result else 0
            registers[15] = self._camera_ready_mask
            registers[16] = self._model_ready_mask
            registers[17] = self._pc_heartbeat
            registers[18] = int(self._error_code)
            registers[19] = int(self._warning_code)
            registers[20] = len(self._queued_results) + (1 if result else 0)
            registers[21], registers[22] = dropped_lo, dropped_hi
            registers[23], registers[24] = missing_lo, missing_hi
            registers[25], registers[26] = processed_lo, processed_hi
            registers[27] = self._command_ack_sequence
            return PcSnapshot(
                registers=tuple(registers),
                status_word=int(status),
                current_result_sequence=result_sequence if result else None,
                result_needs_publication=bool(result and not self._result_published),
            )


def encode_pc_block(state: ModbusSharedState) -> list[int]:
    """Encode the current PC output block for tests and transports."""
    return list(state.pc_snapshot().registers)


__all__ = [
    "PC_TO_PLC_START",
    "PLC_TO_PC_START",
    "PcSnapshot",
    "PlcInput",
    "ModbusHealth",
    "ModbusSharedState",
    "decode_plc_block",
    "encode_pc_block",
]