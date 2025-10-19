# Anomalib/Torch inferencer wrapper supporting .ckpt (anomalib), .pt (TorchScript),
# and a safe Mock mode when no model file is present.

from __future__ import annotations
import os
import numpy as np
import torch
from dataclasses import dataclass
from typing import Dict

try:
    from anomalib.deploy import TorchInferencer as AnomTorchInferencer
except Exception:
    AnomTorchInferencer = None


@dataclass
class ModelConfig:
    path: str = "checkpoints/model.ckpt"  # e.g., checkpoints/model.ckpt or checkpoints/model.pt
    type: str = "auto"                    # "auto" | "anomalib" | "torchscript" | "mock"


class InferenceBackend:
    def __init__(self, cfg: ModelConfig | dict, device: str = "cuda"):
        if isinstance(cfg, dict):
            cfg = ModelConfig(**cfg)
        self.cfg = cfg
        self.device = device if torch.cuda.is_available() else "cpu"
        self._mode = None
        self._runner = None
        self._load()

    def _load(self):
        # Mock mode if requested or file missing
        if self.cfg.type == "mock" or not (self.cfg.path and os.path.exists(self.cfg.path)):
            self._mode = "mock"
            return

        path = self.cfg.path
        typ = (self.cfg.type or "auto").lower()
        ext = os.path.splitext(path)[1].lower()

        candidates = []
        if typ == "anomalib" or (typ == "auto" and ext in {".ckpt", ".pth"}):
            candidates.append("anomalib")
        if typ == "torchscript" or (typ == "auto" and ext in {".pt", ".ts"}):
            candidates.append("torchscript")
        if not candidates:
            candidates = ["anomalib", "torchscript"]

        last_err = None
        for mode in candidates:
            try:
                if mode == "anomalib" and AnomTorchInferencer is not None:
                    self._runner = AnomTorchInferencer(path=path, device=self.device)
                    self._mode = "anomalib"
                    return
                if mode == "torchscript":
                    self._runner = torch.jit.load(path, map_location=self.device)
                    self._mode = "torchscript"
                    return
            except Exception as e:
                last_err = e
                continue
        # If all loaders failed, fall back to mock instead of crashing
        self._mode = "mock"

    @torch.inference_mode()
    def predict(self, batch: np.ndarray) -> Dict:
        # batch: (B,C,H,W) float32 [0..1]
        if self._mode == "mock":
            # Deterministic pseudo-scores per image (mean intensity)
            # shape: (B,)
            scores = batch.mean(axis=(2,3)).mean(axis=1)  # average over H,W then channels
            return {"scores": scores.astype(np.float32)}

        t = torch.from_numpy(batch).to(self.device, non_blocking=True)
        if self._mode == "anomalib":
            return self._runner.predict(t)
        # torchscript forward should return either scores or dict
        out = self._runner(t)
        if isinstance(out, dict):
            return {k: (v.detach().cpu().numpy() if torch.is_tensor(v) else v) for k, v in out.items()}
        if torch.is_tensor(out):
            return {"scores": out.detach().cpu().numpy()}
        return {"scores": np.array(out)}