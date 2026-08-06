"""One publication path for inspection outcomes."""

from __future__ import annotations

from typing import Any

from app.core.logger import jlog
from app.core.modbus.state import ModbusSharedState

from .inspection_result import InspectionResult


class ResultPublisher:
    """Publish one normalized outcome to DIO, Modbus state, Qt UI, and logs."""

    def __init__(self, dio: Any, modbus_state: ModbusSharedState, results_bus: Any):
        self._dio = dio
        self._modbus_state = modbus_state
        self._results_bus = results_bus

    def publish(self, result: InspectionResult) -> bool:
        """Publish once; Modbus queues results rather than overwriting an unacknowledged one."""
        queued_result = self._modbus_state.enqueue_result(result)
        if queued_result is not None:
            result = queued_result
        # The hardwired DIO output remains the timing-critical reject interface.
        self._dio.set_ok_ng(result.ok)
        queued = queued_result is not None
        self._modbus_state.increment_processed_count()
        self._results_bus.inference_result.emit(result.trigger_index, {
            "per_cam_scores": list(result.per_camera_scores),
            "fused_score": result.fused_score,
            "ok": result.ok,
            "inference_ms": result.inference_time_ms,
            "result_code": int(result.result_code),
            "ng_camera_mask": result.ng_camera_mask,
            "error_code": int(result.error_code),
            "warning_code": int(result.warning_code),
            "bypass": result.bypass_active,
            "result_sequence": result.sequence,
        })
        jlog(
            "inspection_result",
            trigger_idx=result.trigger_index,
            result_sequence=result.sequence,
            recipe_id=result.recipe_id,
            recipe_revision=result.recipe_revision,
            result_code=int(result.result_code),
            fused_score=result.fused_score,
            ng_camera_mask=result.ng_camera_mask,
            queued=queued,
        )
        return queued

    def fail_safe(self) -> None:
        """Force existing reject output to its safe (NG) state."""
        self._dio.set_ok_ng(False)