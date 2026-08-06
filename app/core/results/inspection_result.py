"""The single normalized representation of an inspection outcome."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.modbus.register_map import ResultCode, VisionErrorCode, VisionWarningCode


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Result data shared consistently by DIO, Modbus, UI, and logging."""

    trigger_index: int
    recipe_id: int
    recipe_revision: int
    result_code: ResultCode
    ok: bool
    per_camera_scores: tuple[float, ...] = ()
    fused_score: float = 0.0
    ng_camera_mask: int = 0
    inference_time_ms: float = 0.0
    error_code: VisionErrorCode = VisionErrorCode.NONE
    warning_code: VisionWarningCode = VisionWarningCode.NONE
    missing_camera_mask: int = 0
    bypass_active: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = 0

    def with_sequence(self, sequence: int) -> "InspectionResult":
        return InspectionResult(
            trigger_index=self.trigger_index,
            recipe_id=self.recipe_id,
            recipe_revision=self.recipe_revision,
            result_code=self.result_code,
            ok=self.ok,
            per_camera_scores=self.per_camera_scores,
            fused_score=self.fused_score,
            ng_camera_mask=self.ng_camera_mask,
            inference_time_ms=self.inference_time_ms,
            error_code=self.error_code,
            warning_code=self.warning_code,
            missing_camera_mask=self.missing_camera_mask,
            bypass_active=self.bypass_active,
            timestamp=self.timestamp,
            sequence=sequence,
        )


__all__ = ["InspectionResult", "ResultCode"]