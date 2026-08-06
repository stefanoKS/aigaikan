# Updated multi-format inferencer with correct Anomalib .pt handling

from __future__ import annotations
import os
import numpy as np
import torch
from dataclasses import dataclass
from typing import Any, Dict

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from app.core.postprocess import decide, fuse_scores
from app.core.preprocessor import preprocess_batch
from app.core.recipes import RecipeRuntime

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
        if device == "cuda" and self.device != "cuda":
            print("[InferenceBackend] CUDA requested but unavailable; using CPU inference")
        self._mode = None
        self._runner = None

        if self.device == "cuda":
            torch.backends.cudnn.benchmark = True
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.set_float32_matmul_precision("high")

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
                self._runner.model.eval()
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
                    self._runner.model.eval()
                    self._mode = "anomalib"
                    return
                except Exception as e:
                    print(f"[InferenceBackend] AUTO Anomalib failed: {e}")

            # Then try TorchScript if .pt
            if ext in {".pt", ".ts"}:
                try:
                    print(f"[InferenceBackend] AUTO: Trying TorchScript JIT for {path}")
                    self._runner = torch.jit.load(path, map_location=self.device)
                    self._runner.eval()
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
                self._runner.eval()
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


def extract_score(output: Any) -> float:
    """Return the one-image anomaly score or fail instead of silently accepting bad output."""
    for name in ("pred_score", "pred_scores", "scores"):
        value = output.get(name) if isinstance(output, dict) else getattr(output, name, None)
        if value is None:
            continue
        if isinstance(value, torch.Tensor):
            value = value.detach().cpu().numpy()
        return float(np.asarray(value).reshape(-1)[0])
    raise ValueError(f"Inference output does not contain a supported score: {type(output).__name__}")


class BatchInferenceWorker(QObject):
    """Run CPU/GPU work outside the Qt UI thread while keeping model use serialized."""

    completed = pyqtSignal(int, list, list, float, bool, float)
    failed = pyqtSignal(int, str)
    recipe_loaded = pyqtSignal(int, int, int, int)
    recipe_failed = pyqtSignal(int, int, int, str)

    def __init__(
        self,
        backends: dict[int, InferenceBackend],
        input_size: tuple[int, int],
        threshold: float,
        camera_rois: dict[int, tuple[int, int, int, int]] | None = None,
        allow_mock_models: bool = False,
    ):
        super().__init__()
        self._backends = backends
        self._input_size = input_size
        self._threshold = threshold
        self._camera_rois = camera_rois or {}
        self._allow_mock_models = allow_mock_models

    @pyqtSlot(int, list)
    def process(self, trigger_idx: int, frames: list) -> None:
        import time

        start_time = time.perf_counter()
        try:
            if QThread.currentThread().isInterruptionRequested():
                return
            batch = preprocess_batch(frames, size=self._input_size, camera_rois=self._camera_rois)
            scores: list[float] = []
            for index, frame in enumerate(frames):
                if QThread.currentThread().isInterruptionRequested():
                    return
                backend = self._backends.get(frame.cam_id)
                if backend is None:
                    raise RuntimeError(f"No inference backend configured for camera {frame.cam_id}")
                output = backend.predict(batch[index:index + 1])
                scores.append(extract_score(output))

            fused = fuse_scores(scores)
            ok = decide(self._threshold, fused)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            self.completed.emit(trigger_idx, frames, scores, fused, ok, elapsed_ms)
        except Exception as exc:
            self.failed.emit(trigger_idx, f"{type(exc).__name__}: {exc}")

    @pyqtSlot(object, int)
    def reconfigure_recipe(self, runtime: RecipeRuntime, request_sequence: int) -> None:
        """Load recipe-specific models in the inference thread, behind queued predictions."""
        try:
            backends: dict[int, InferenceBackend] = {}
            for cam_id in range(len(self._backends)):
                raw = runtime.models.get(f"cam{cam_id + 1}", runtime.models.get(str(cam_id)))
                if raw is None:
                    raise ValueError(f"Recipe lacks a model configuration for camera {cam_id}")
                backend = InferenceBackend(ModelConfig(**raw), device="cuda")
                if backend._mode == "mock" and not self._allow_mock_models:
                    raise RuntimeError(f"Model for camera {cam_id} did not load")
                backends[cam_id] = backend
            self._backends = backends
            self._input_size = runtime.input_size
            self._threshold = runtime.ok_threshold
            self._camera_rois = runtime.definition.camera_rois
            mask = sum(1 << cam_id for cam_id in backends)
            self.recipe_loaded.emit(
                request_sequence,
                runtime.definition.recipe_id,
                runtime.definition.revision,
                mask,
            )
        except Exception as exc:
            self.recipe_failed.emit(
                request_sequence,
                runtime.definition.recipe_id,
                runtime.definition.revision,
                f"{type(exc).__name__}: {exc}",
            )
