# Updated multi-format inferencer with correct Anomalib .pt handling

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
    path: str = ""
    type: str = "auto"      # "auto" | "anomalib" | "torchscript" | "mock"


class InferenceBackend:
    def __init__(self, cfg: ModelConfig | dict, device: str = "cuda"):
        if isinstance(cfg, dict):
            cfg = ModelConfig(**cfg)

        self.cfg = cfg
        self.device = device if torch.cuda.is_available() else "cpu"
        self._mode = None
        self._runner = None
        self._load()

    # --------------------------
    # Loader Logic
    # --------------------------
    def _load(self):
        path = self.cfg.path
        typ = (self.cfg.type or "auto").lower()

        if typ == "mock" or not os.path.exists(path):
            print(f"[InferenceBackend] File missing → mock mode. path={path}")
            self._mode = "mock"
            return

        ext = os.path.splitext(path)[1].lower()

        # --------------------------
        # 1. FORCE Anomalib for .pt WHEN type="anomalib"
        # --------------------------
        if typ == "anomalib":
            if AnomTorchInferencer is None:
                print("[InferenceBackend] Anomalib not installed → mock")
                self._mode = "mock"
                return

            try:
                print(f"[InferenceBackend] Loading Anomalib TorchInferencer: {path}")
                self._runner = AnomTorchInferencer(path=path, device=self.device)
                print(f"Load model from path: {path}")
                self._mode = "anomalib"
                return
            except Exception as e:
                print(f"[InferenceBackend] FAILED Anomalib load: {e}")
                self._mode = "mock"
                return

        # --------------------------
        # 2. AUTO MODE – decide based on extension
        # --------------------------
        if typ == "auto":

            # Try Anomalib FIRST for .pt, .ckpt, .pth
            if ext in {".pt", ".ckpt", ".pth"} and AnomTorchInferencer is not None:
                try:
                    print(f"[InferenceBackend] AUTO: Trying Anomalib for {path}")
                    self._runner = AnomTorchInferencer(path=path, device=self.device)
                    self._mode = "anomalib"
                    return
                except Exception as e:
                    print(f"[InferenceBackend] AUTO Anomalib failed: {e}")

            # Then try TorchScript if .pt
            if ext in {".pt", ".ts"}:
                try:
                    print(f"[InferenceBackend] AUTO: Trying TorchScript JIT for {path}")
                    self._runner = torch.jit.load(path, map_location=self.device)
                    self._mode = "torchscript"
                    return
                except Exception as e:
                    print(f"[InferenceBackend] AUTO TorchScript failed: {e}")

            # If everything fails
            print("[InferenceBackend] AUTO: All loaders failed → mock")
            self._mode = "mock"
            return

        # --------------------------
        # 3. FORCED TorchScript
        # --------------------------
        if typ == "torchscript":
            try:
                self._runner = torch.jit.load(path, map_location=self.device)
                self._mode = "torchscript"
                return
            except Exception as e:
                print(f"[InferenceBackend] TorchScript load failed: {e}")
                self._mode = "mock"
                return

        # --------------------------
        # 4. Final fallback
        # --------------------------
        print("[InferenceBackend] Unknown type → mock")
        self._mode = "mock"

    # --------------------------
    # Inference
    # --------------------------
    @torch.inference_mode()
    def predict(self, batch: np.ndarray) -> Dict:
        # batch: (B,C,H,W) float32
        print(f"[InferenceBackend] Running mode={self._mode}")

        if self._mode == "mock":
            scores = batch.mean(axis=(2, 3)).mean(axis=1)
            return {"scores": scores.astype(np.float32)}

        t = torch.from_numpy(batch).to(self.device, non_blocking=True)

        if self._mode == "anomalib":
            return self._runner.predict(t)

        if self._mode == "torchscript":
            out = self._runner(t)
            if isinstance(out, dict):
                return {k: (v.detach().cpu().numpy() if torch.is_tensor(v) else v)
                        for k, v in out.items()}
            if torch.is_tensor(out):
                return {"scores": out.detach().cpu().numpy()}
            return {"scores": np.array(out)}

        return {"scores": np.zeros(batch.shape[0], dtype=np.float32)}
