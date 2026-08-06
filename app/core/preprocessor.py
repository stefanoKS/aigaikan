"""Fast image conversion used before model inference."""

from __future__ import annotations

import cv2
import numpy as np


def to_chw_tensor(
    img: np.ndarray,
    size: tuple[int, int] = (280, 280),
    mean: tuple[float, float, float] = (0.485, 0.456, 0.406),
    std: tuple[float, float, float] = (0.229, 0.224, 0.225),
    roi: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Convert a camera image to a contiguous, normalized CHW float32 tensor."""
    if roi is not None:
        x, y, width, height = roi
        if x + width > img.shape[1] or y + height > img.shape[0]:
            raise ValueError(f"ROI {roi} is outside image shape {img.shape[:2]}")
        img = img[y:y + height, x:x + width]
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32, copy=False) / 255.0
    img = (img - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return np.ascontiguousarray(np.moveaxis(img, -1, 0))


def preprocess_batch(
    frames: list,
    size: tuple[int, int] = (280, 280),
    mean: tuple[float, float, float] = (0.485, 0.456, 0.406),
    std: tuple[float, float, float] = (0.229, 0.224, 0.225),
    camera_rois: dict[int, tuple[int, int, int, int]] | None = None,
) -> np.ndarray:
    """Preprocess all synchronized frames once into a BxCxHxW float32 batch."""
    rois = camera_rois or {}
    return np.stack([to_chw_tensor(frame.image, size, mean, std, rois.get(frame.cam_id)) for frame in frames])