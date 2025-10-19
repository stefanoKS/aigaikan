# Glue everything together; start 4 cameras, coordinator, inference and UI

from __future__ import annotations
import sys, yaml
from PyQt5.QtWidgets import QApplication
from app.core.logger import setup_logging, jlog, tb
from app.core.results_bus import ResultsBus
from app.core.camera_manager import CameraWorker, CameraConfig
from app.core.trigger_coordinator import TriggerCoordinator
from app.core.dio_client import DIOConfig, make_dio
from app.core.preprocessor import preprocess_batch
from app.core.infer_worker import InferenceBackend
from app.core.postprocess import fuse_scores, decide
from app.ui.main_window import MainWindow, np_to_qimage


def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    setup_logging()
    cams_cfg = load_yaml("configs/cameras.yaml")
    th_cfg = load_yaml("configs/thresholds.yaml")
    # DIO config may be absent on dev machines; handle gracefully
    try:
        dio_cfg = load_yaml("configs/dio.yaml")
    except FileNotFoundError:
        dio_cfg = None

    app = QApplication(sys.argv)
    bus = ResultsBus()

    # DIO layer (real if available, otherwise mock)
    dio = make_dio(DIOConfig(**dio_cfg) if dio_cfg else None)
    dio.start()

    # Helper to read current trigger index
    read_ti = dio.read_trigger_index

    # Coordinator
    coordinator = TriggerCoordinator(num_cams=len(cams_cfg["cameras"]), max_hold_ms=8)

    # Camera workers
    cam_workers = []
    try:
        for cam_id, c in enumerate(cams_cfg["cameras"]):
            cw = CameraWorker(cam_id, CameraConfig(**c), dio.read_trigger_index)
            cw.frame_signal.connect(coordinator.on_frame)
            cw.start()
            cam_workers.append(cw)
    except Exception:
        # Fallback: create 4 mock cameras if config missing or IC4 not installed
        for cam_id in range(4):
            cw = CameraWorker(cam_id, CameraConfig(serial=f"mock-{cam_id}"), dio.read_trigger_index)
            cw.frame_signal.connect(coordinator.on_frame)
            cw.start()
            cam_workers.append(cw)

    backend = InferenceBackend(ckpt_path="checkpoints/model.ckpt", device="cuda")(ckpt_path="checkpoints/model.ckpt", device="cuda")

    def on_batch(trigger_idx: int, frames: list):
        # Preview thumbnails
        for f in frames:
            bus.frame_preview.emit(trigger_idx, f.cam_id, np_to_qimage(f.image))
        # Preprocess + infer
        with tb("preprocess", {"ti": trigger_idx}):
            batch = preprocess_batch(frames, size=tuple(th_cfg.get("input_size", [512, 512])))
        with tb("inference", {"ti": trigger_idx}):
            out = backend.predict(batch)
        # Extract per-cam scores (adjust to your model's output schema)
        per_cam_scores = []
        if isinstance(out, dict) and "pred_scores" in out:
            per_cam_scores = [float(s) for s in out["pred_scores"]]
        elif isinstance(out, dict) and "scores" in out:
            per_cam_scores = [float(x) for x in out["scores"].reshape(-1)]
        else:
            per_cam_scores = [0.0] * len(frames)
        fused = fuse_scores(per_cam_scores)
        ok = decide(th_cfg.get("ok_threshold", 0.5), fused)
        bus.inference_result.emit(trigger_idx, {
            "per_cam_scores": per_cam_scores,
            "fused_score": fused,
            "ok": ok,
        })
        # Optional: write OK/NG bit back
        dio.set_ok_ng(ok)

    coordinator.batch_ready.connect(on_batch)

    # UI
    win = MainWindow(bus, ui_path="app/ui/mainWidget.ui")
    win.resize(1024, 768)
    win.show()

    jlog("app_start")
    rc = app.exec_()

    for cw in cam_workers:
        cw.stop(); cw.wait(500)
    dio.stop()

    sys.exit(rc)


if __name__ == "__main__":
    main()
