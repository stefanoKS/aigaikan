import numpy as np


def fuse_scores(per_cam_scores: list[float]) -> float:
    # Simple max fusion for anomalies
    return float(np.max(per_cam_scores))


def decide(ok_threshold: float, score: float) -> bool:
    return score < ok_threshold  # True = OK
