from __future__ import annotations
import sys
import os
import ctypes
from datetime import datetime
from pathlib import Path

import yaml
import cv2

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, Qt
from PyQt5.QtWidgets import QApplication

from app.core.logger import jlog, setup_logging
from app.core.results_bus import ResultsBus
from app.core.camera_manager import CameraWorker, CameraConfig
from app.core.trigger_coordinator import TriggerCoordinator
from app.core.dio_client import DIOConfig, make_dio
from app.ui.main_window import MainWindow, np_to_qimage
from app.core.infer_worker import BatchInferenceWorker, InferenceBackend, ModelConfig

# ---- Process priority (Windows) ----
if os.name == "nt":
    try:
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(),
            0x00000080,  # HIGH_PRIORITY_CLASS
        )
    except OSError:
        pass

# ---- Where to save logged images when checkbox is ON ----
LOG_IMAGE_DIR = Path("logs") / "captures"


class BatchInferenceController(QObject):
    """Main-thread bridge between synchronized frames and the inference QThread."""

    requested = pyqtSignal(int, list)

    def __init__(self, on_completed, on_failed):
        super().__init__()
        self._on_completed = on_completed
        self._on_failed = on_failed

    @pyqtSlot(int, list, list, float, bool, float)
    def completed(self, trigger_idx, frames, scores, fused, ok, elapsed_ms):
        self._on_completed(trigger_idx, frames, scores, fused, ok, elapsed_ms)

    @pyqtSlot(int, str)
    def failed(self, trigger_idx, message):
        self._on_failed(trigger_idx, message)


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

    def on_inference_completed(
        trigger_idx: int,
        frames: list,
        per_cam_scores: list[float],
        fused: float,
        ok: bool,
        elapsed_ms: float,
    ):
        nonlocal write_config_enabled
        nonlocal write_collect_data_enabled

        # One aggregate log avoids per-camera synchronous file writes in the hot path.
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

            for f, score in zip(frames, per_cam_scores):
                img = f.image
                cam_id = f.cam_id
                fname = LOG_IMAGE_DIR / f"{ts_for_name}_ti{trigger_idx:06d}_cam{cam_id}_s{score:.3f}.png"
                cv2.imwrite(str(fname), img)

    def on_inference_failed(trigger_idx: int, message: str):
        jlog("batch_inference_failed", trigger_idx=trigger_idx, error=message)
        print(f"[Inference] TI {trigger_idx} failed: {message}")
        bus.inference_result.emit(trigger_idx, {
            "per_cam_scores": [],
            "fused_score": 1.0,
            "ok": False,
            "error": message,
        })

    inference_thread = QThread()
    inference_worker = BatchInferenceWorker(
        backends=backends,
        input_size=tuple(th_cfg.get("input_size", [280, 280])),
        threshold=float(th_cfg.get("ok_threshold", 0.5)),
        dio=dio,
    )
    inference_worker.moveToThread(inference_thread)
    inference_controller = BatchInferenceController(on_inference_completed, on_inference_failed)
    inference_controller.requested.connect(inference_worker.process, Qt.QueuedConnection)
    inference_worker.completed.connect(inference_controller.completed, Qt.QueuedConnection)
    inference_worker.failed.connect(inference_controller.failed, Qt.QueuedConnection)
    inference_thread.start()

    def on_batch(trigger_idx: int, frames: list):
        # Submit first so image display work cannot delay the decision path.
        inference_controller.requested.emit(trigger_idx, frames)
        for frame in frames:
            bus.frame_preview.emit(trigger_idx, frame.cam_id, np_to_qimage(frame.image))

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

        # 2) Finish/cancel inference before closing the DIO handle it may write to.
        inference_thread.requestInterruption()
        inference_thread.quit()
        inference_thread.wait(3000)

        # 3) Stop DIO
        try:
            dio.stop()
        except Exception as e:
            print("[DIO] stop error:", e)

        # 4) Close the window and exit the event loop
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
    inference_thread.requestInterruption()
    inference_thread.quit()
    inference_thread.wait(3000)
    dio.stop()

    sys.exit(rc)


if __name__ == "__main__":
    main()
