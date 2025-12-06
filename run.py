from __future__ import annotations
import sys
import os
import time
from datetime import datetime
from pathlib import Path

import yaml
import numpy as np
import psutil
import torch
import cv2

from PyQt5.QtWidgets import QApplication

from app.core.logger import jlog, tb, setup_logging
from app.core.results_bus import ResultsBus
from app.core.camera_manager import CameraWorker, CameraConfig
from app.core.trigger_coordinator import TriggerCoordinator
from app.core.dio_client import DIOConfig, make_dio
from app.core.preprocessor import preprocess_batch
from app.core.postprocess import fuse_scores, decide
from app.ui.main_window import MainWindow, np_to_qimage
from app.core.infer_worker import InferenceBackend, ModelConfig

# ---- Process priority (Windows) ----
p = psutil.Process(os.getpid())
p.nice(psutil.HIGH_PRIORITY_CLASS)

# ---- Where to save logged images when checkbox is ON ----
LOG_IMAGE_DIR = Path("logs") / "captures"


def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    setup_logging()

    # ---- Load configs ----
    cams_cfg = load_yaml("configs/cameras.yaml")
    th_cfg = load_yaml("configs/thresholds.yaml")

    try:
        dio_cfg = load_yaml("configs/dio.yaml")
    except FileNotFoundError:
        dio_cfg = None

    app = QApplication(sys.argv)
    bus = ResultsBus()

    # ---- DIO layer (real if available, otherwise mock) ----
    dio = make_dio(DIOConfig(**dio_cfg) if dio_cfg else None)
    dio.start()

    # Helper to read current trigger index
    read_ti = dio.read_trigger_index

    # ---- Trigger coordinator ----
    num_cams = len(cams_cfg["cameras"])
    coordinator = TriggerCoordinator(num_cams=num_cams, max_hold_ms=8)

    # ---- Camera workers ----
    cam_workers: list[CameraWorker] = []
    try:
        for cam_id, c in enumerate(cams_cfg["cameras"]):
            cw = CameraWorker(cam_id, CameraConfig(**c), read_ti)
            cw.frame_signal.connect(coordinator.on_frame)
            cw.start()
            cam_workers.append(cw)
    except Exception:
        # Fallback: create 4 mock cameras if config missing or IC4 not installed
        for cam_id in range(4):
            cw = CameraWorker(cam_id, CameraConfig(serial=f"mock-{cam_id}"), read_ti)
            cw.frame_signal.connect(coordinator.on_frame)
            cw.start()
            cam_workers.append(cw)

    # ---- Multi-model setup: one backend per camera ----
    backends: dict[int, InferenceBackend] = {}

    try:
        models_cfg = load_yaml("configs/models.yaml")
        models_map = models_cfg.get("models", {})
    except FileNotFoundError:
        models_map = {}

    for cam_id in range(num_cams):
        # Try keys in this order: "cam1", "cam2", ... then "0", "1", ...
        key_cam = f"cam{cam_id + 1}"
        cfg_dict = models_map.get(key_cam)

        if cfg_dict is None:
            cfg_dict = models_map.get(str(cam_id))

        # Fallback default: checkpoints/camX/model.pt, using Anomalib TorchInferencer
        if cfg_dict is None:
            cfg_dict = {
                "path": os.path.join("checkpoints", f"cam{cam_id + 1}", "model.pt"),
                "type": "anomalib",
            }

        model_cfg = ModelConfig(**cfg_dict)
        backend = InferenceBackend(model_cfg, device="cuda")  # change to "cpu" if needed
        print(f"[Model] cam {cam_id}: mode={backend._mode}, path={model_cfg.path}")
        backends[cam_id] = backend

    # ---- Checkbox state for extra logging + image saving ----
    write_config_enabled = False
    write_collect_data_enabled = False

    def to_scalar(val) -> float:
        """Convert torch.Tensor / numpy / list / scalar to a single float."""
        if isinstance(val, torch.Tensor):
            val = val.detach().cpu().numpy()
        return float(np.array(val).reshape(-1)[0])

    # ---- Batch callback: per-camera model inference ----
    def on_batch(trigger_idx: int, frames: list):
        nonlocal write_config_enabled
        nonlocal write_collect_data_enabled

        # Preview thumbnails in UI
        for f in frames:
            bus.frame_preview.emit(trigger_idx, f.cam_id, np_to_qimage(f.image))

        per_cam_scores: list[float] = []
        frame_scores: list[tuple] = []  # list of (CameraFrame, score)
        start_ts = time.perf_counter()

        # Process each camera's frame with its own model
        for f in frames:
            cam_id = f.cam_id
            backend = backends.get(cam_id)
            if backend is None:
                print(f"[Inference] No backend for cam_id={cam_id}, assigning score 0.0")
                per_cam_scores.append(0.0)
                frame_scores.append((f, 0.0))
                continue

            with tb("preprocess", {"ti": trigger_idx, "cam_id": cam_id}):
                batch = preprocess_batch(
                    [f],
                    size=tuple(th_cfg.get("input_size", [512, 512]))
                )

            with tb("inference", {"ti": trigger_idx, "cam_id": cam_id}):
                out = backend.predict(batch)

            # ---- Extract scalar score robustly (dict or ImageBatch) ----
            score = 0.0
            if isinstance(out, dict):
                if "pred_score" in out:
                    score = to_scalar(out["pred_score"])
                elif "pred_scores" in out:
                    score = to_scalar(out["pred_scores"])
                elif "scores" in out:
                    score = to_scalar(out["scores"])
            else:
                if hasattr(out, "pred_score"):
                    score = to_scalar(out.pred_score)
                elif hasattr(out, "pred_scores"):
                    score = to_scalar(out.pred_scores)
                elif hasattr(out, "scores"):
                    score = to_scalar(out.scores)

            per_cam_scores.append(score)
            frame_scores.append((f, score))

        # ---- Fuse + OK/NG logic ----
        fused = fuse_scores(per_cam_scores)
        ok = decide(th_cfg.get("ok_threshold", 0.5), fused)

        end_ts = time.perf_counter()
        elapsed_ms = (end_ts - start_ts) * 1000.0

        # Basic timing log (always)
        jlog("batch_inference", ms=elapsed_ms)
        print(f"batch_inference_time={elapsed_ms:.2f} ms")

        # Emit to UI
        bus.inference_result.emit(trigger_idx, {
            "per_cam_scores": per_cam_scores,
            "fused_score": fused,
            "ok": ok,
            "inference_ms": elapsed_ms,
        })

        # Optional: write OK/NG bit back
        dio.set_ok_ng(ok)

        # ---- Extra logging controlled by checkbox ----
        if write_config_enabled:
            ts_iso = datetime.now().isoformat(timespec="milliseconds")
            jlog(
                "batch_detail",
                timestamp=ts_iso,
                trigger_idx=trigger_idx,
                per_cam_scores=per_cam_scores,
                fused_score=fused,
                ok=ok,
                inference_ms=elapsed_ms,
            )

        # ---- Image saving controlled by separate checkbox ----
        if write_collect_data_enabled:
            LOG_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            ts_for_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

            for f, score in frame_scores:
                img = f.image
                cam_id = f.cam_id
                fname = LOG_IMAGE_DIR / f"{ts_for_name}_ti{trigger_idx:06d}_cam{cam_id}_s{score:.3f}.png"
                cv2.imwrite(str(fname), img)



    coordinator.batch_ready.connect(on_batch)

    # ---- UI ----
    win = MainWindow(ui_path="app/ui/mainWidget.ui")
    win._setup_ui(bus)
    win.resize(1920, 1080)

    # Handle checkbox toggling (writeConfig signal from MainWindow)
    def handle_write_config(payload):
        nonlocal write_config_enabled
        if isinstance(payload, dict):
            write_config_enabled = bool(payload.get("write_config", False))
        else:
            write_config_enabled = bool(payload)
        print(f"[CFG] write_config_enabled={write_config_enabled}")

    def handle_collect_data(payload):
        nonlocal write_collect_data_enabled
        if isinstance(payload, dict):
            write_collect_data_enabled = bool(payload.get("write_collect_data", False))
        else:
            write_collect_data_enabled = bool(payload)
        print(f"[CFG] write_collect_data_enabled={write_collect_data_enabled}")


    win.writeConfig.connect(handle_write_config)
    win.writeCollectData.connect(handle_collect_data)

    # Graceful quit handling
    def handle_quit():
        print("[APP] Quit requested")

        # 1) Stop camera workers first (so IC4 shuts down cleanly)
        for cw in cam_workers:
            cw.stop()
        for cw in cam_workers:
            cw.wait(1500)

        # 2) Stop DIO
        try:
            dio.stop()
        except Exception as e:
            print("[DIO] stop error:", e)

        # 3) Close the window and exit the event loop
        win.close()
        QApplication.instance().quit()

    win.quitRequested.connect(handle_quit)

    win.show()

    jlog("app_start")
    rc = app.exec_()

    # Safety net: if window closed via X/Alt+F4, still ensure clean shutdown
    for cw in cam_workers:
        cw.stop()
        cw.wait(500)
    dio.stop()

    sys.exit(rc)


if __name__ == "__main__":
    main()
