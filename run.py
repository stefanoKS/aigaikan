from __future__ import annotations

import ctypes
from datetime import datetime
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from typing import Any

import cv2
import yaml
from PyQt5.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication

from app.core.camera_manager import CameraConfig, CameraWorker
from app.core.dio_client import DIOConfig, make_dio
from app.core.infer_worker import BatchInferenceWorker, InferenceBackend, ModelConfig
from app.core.logger import jlog, setup_logging
from app.core.modbus.config import ModbusConfig
from app.core.modbus.protocol import ProtocolEvent
from app.core.modbus.register_map import (
    PlcCommand,
    ResultCode,
    VisionErrorCode,
)
from app.core.modbus.state import ModbusSharedState
from app.core.modbus.worker import ModbusWorker
from app.core.recipes import RecipeError, RecipeNotFoundError, RecipeRepository, RecipeRevisionError, RecipeRuntime
from app.core.results.inspection_result import InspectionResult
from app.core.results.result_publisher import ResultPublisher
from app.core.results_bus import ResultsBus
from app.core.trigger_coordinator import TriggerCoordinator
from app.ui.main_window import MainWindow, np_to_qimage


if os.name == "nt":
    try:
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(),
            0x00000080,  # HIGH_PRIORITY_CLASS
        )
    except OSError:
        pass


LOG_IMAGE_DIR = Path("logs") / "captures"
MAX_PENDING_INFERENCES = 2


class BatchInferenceController(QObject):
    """Main-thread bridge to the existing dedicated inference QThread."""

    requested = pyqtSignal(int, list)
    recipe_requested = pyqtSignal(object, int)

    def __init__(self, on_completed, on_failed, on_recipe_loaded, on_recipe_failed):
        super().__init__()
        self._on_completed = on_completed
        self._on_failed = on_failed
        self._on_recipe_loaded = on_recipe_loaded
        self._on_recipe_failed = on_recipe_failed

    @pyqtSlot(int, list, list, float, bool, float)
    def completed(self, trigger_idx, frames, scores, fused, ok, elapsed_ms):
        self._on_completed(trigger_idx, frames, scores, fused, ok, elapsed_ms)

    @pyqtSlot(int, str)
    def failed(self, trigger_idx, message):
        self._on_failed(trigger_idx, message)

    @pyqtSlot(int, int, int, int)
    def recipe_loaded(self, sequence, recipe_id, revision, model_mask):
        self._on_recipe_loaded(sequence, recipe_id, revision, model_mask)

    @pyqtSlot(int, int, int, str)
    def recipe_failed(self, sequence, recipe_id, revision, message):
        self._on_recipe_failed(sequence, recipe_id, revision, message)


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        value = yaml.safe_load(file)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration {path} must contain a YAML mapping")
    return value


def make_backends(
    runtime: RecipeRuntime,
    camera_count: int,
    *,
    allow_mock_models: bool,
) -> tuple[dict[int, InferenceBackend], int]:
    """Create initial backends; failed model loads remain visible through readiness masks."""
    backends: dict[int, InferenceBackend] = {}
    ready_mask = 0
    for cam_id in range(camera_count):
        raw = runtime.models.get(f"cam{cam_id + 1}", runtime.models.get(str(cam_id)))
        if raw is None:
            raise RecipeError(f"Recipe {runtime.definition.name!r} lacks a model for camera {cam_id}")
        backend = InferenceBackend(ModelConfig(**raw), device="cuda")
        backends[cam_id] = backend
        if backend._mode != "mock" or allow_mock_models:
            ready_mask |= 1 << cam_id
        print(f"[Model] cam {cam_id}: mode={backend._mode}, path={backend.cfg.path}")
    return backends, ready_mask


def main() -> None:
    setup_logging()
    project_root = Path(__file__).resolve().parent
    cams_cfg = load_yaml(project_root / "configs" / "cameras.yaml")
    camera_configs = cams_cfg.get("cameras")
    if not isinstance(camera_configs, list) or not camera_configs:
        raise ValueError("configs/cameras.yaml requires a non-empty cameras list")
    num_cams = len(camera_configs)

    modbus_cfg = ModbusConfig.from_mapping(load_yaml(project_root / "configs" / "modbus.yaml"))
    recipe_repository = RecipeRepository.from_yaml(project_root / "configs" / "recipes.yaml", project_root)
    initial_runtime = recipe_repository.load(0, 0)
    allow_mock_models = not modbus_cfg.enabled or modbus_cfg.behavior.simulation_mode
    backends, model_ready_mask = make_backends(
        initial_runtime,
        num_cams,
        allow_mock_models=allow_mock_models,
    )

    try:
        dio_cfg = load_yaml(project_root / "configs" / "dio.yaml")
    except FileNotFoundError:
        dio_cfg = None

    app = QApplication(sys.argv)
    bus = ResultsBus()
    dio = make_dio(DIOConfig(**dio_cfg) if dio_cfg else None)
    dio.start()

    required_mask = (1 << num_cams) - 1
    modbus_state = ModbusSharedState(
        enabled=modbus_cfg.enabled,
        score_scale=modbus_cfg.score_scale,
        required_camera_mask=required_mask,
        required_model_mask=required_mask,
    )
    modbus_state.set_model_ready_mask(model_ready_mask)
    modbus_state.set_recipe_loaded(initial_runtime.definition.recipe_id, initial_runtime.definition.revision, 0)
    modbus_events: Queue[tuple[str, object]] = Queue()
    modbus_worker = ModbusWorker(modbus_cfg, modbus_state, modbus_events)
    if modbus_cfg.enabled:
        modbus_worker.start()

    publisher = ResultPublisher(dio, modbus_state, bus)
    accepting_inspections = True
    pending_batches = 0
    write_config_enabled = False
    write_collect_data_enabled = False
    force_save_diagnostics = False
    shutdown_started = False

    def modbus_required() -> bool:
        return modbus_cfg.enabled and modbus_cfg.behavior.require_modbus_for_inspection

    def publish_status() -> None:
        plc = modbus_state.plc_snapshot()
        health = modbus_state.health_snapshot()
        recipe_id, recipe_revision = modbus_state.active_recipe()
        snapshot = modbus_state.pc_snapshot()
        bus.status.emit({
            "modbus_connected": health.connected,
            "plc_heartbeat_valid": health.heartbeat_valid,
            "requested_recipe_id": plc.requested_recipe_id,
            "active_recipe_id": recipe_id,
            "active_recipe_revision": recipe_revision,
            "inspection_ready": bool(snapshot.status_word & (1 << 4)),
            "result_pending": bool(snapshot.status_word & (1 << 6)),
            "last_result_sequence": snapshot.current_result_sequence or 0,
            "error_code": int(snapshot.registers[18]),
        })

    def refresh_inspection_ready() -> None:
        health = modbus_state.health_snapshot()
        snapshot = modbus_state.pc_snapshot()
        application_ready = all(
            snapshot.status_word & bit
            for bit in ((1 << 1), (1 << 2), (1 << 3))
        )
        ready = (
            accepting_inspections
            and application_ready
            and (not modbus_required() or (health.connected and health.heartbeat_valid))
        )
        modbus_state.set_inspection_ready(ready)
        publish_status()

    refresh_inspection_ready()

    read_ti = dio.read_trigger_index  # Camera hardware trigger correlation remains DIO-based.
    coordinator = TriggerCoordinator(num_cams=num_cams, max_hold_ms=8)
    cam_workers: list[CameraWorker] = []

    def on_camera_connected(camera_id: int, connected: bool) -> None:
        # IC4 mock workers report connected=False but remain operational for development.
        operational = connected or not modbus_cfg.enabled or modbus_cfg.behavior.simulation_mode
        modbus_state.set_camera_ready(camera_id, operational)
        if not operational:
            modbus_state.set_error(VisionErrorCode.CAMERA_UNAVAILABLE)
        refresh_inspection_ready()

    try:
        for cam_id, raw_camera in enumerate(camera_configs):
            worker = CameraWorker(cam_id, CameraConfig(**raw_camera), read_ti)
            worker.frame_signal.connect(coordinator.on_frame)
            worker.connected.connect(lambda connected, camera_id=cam_id: on_camera_connected(camera_id, connected))
            worker.start()
            cam_workers.append(worker)
    except Exception as exc:
        jlog("camera_start_failure", error=str(exc))
        modbus_state.set_error(VisionErrorCode.CAMERA_UNAVAILABLE)
        raise

    def current_recipe_result_fields() -> tuple[int, int]:
        return modbus_state.active_recipe()

    def publish_inspection_result(result: InspectionResult) -> None:
        publisher.publish(result)
        publish_status()

    def on_inference_completed(
        trigger_idx: int,
        frames: list,
        per_cam_scores: list[float],
        fused: float,
        ok: bool,
        elapsed_ms: float,
    ) -> None:
        nonlocal pending_batches, force_save_diagnostics
        pending_batches = max(0, pending_batches - 1)
        modbus_state.set_inspection_busy(pending_batches > 0)
        recipe_id, revision = current_recipe_result_fields()
        threshold = inference_worker._threshold
        ng_mask = sum(1 << frame.cam_id for frame, score in zip(frames, per_cam_scores) if score >= threshold)
        result = InspectionResult(
            trigger_index=trigger_idx,
            recipe_id=recipe_id,
            recipe_revision=revision,
            result_code=ResultCode.OK if ok else ResultCode.NG,
            ok=ok,
            per_camera_scores=tuple(per_cam_scores),
            fused_score=fused,
            ng_camera_mask=ng_mask,
            inference_time_ms=elapsed_ms,
        )
        publish_inspection_result(result)
        jlog("batch_inference", ms=elapsed_ms, trigger_idx=trigger_idx)

        plc = modbus_state.plc_snapshot()
        if write_config_enabled:
            jlog(
                "batch_detail",
                timestamp=datetime.now().isoformat(timespec="milliseconds"),
                trigger_idx=trigger_idx,
                per_cam_scores=per_cam_scores,
                fused_score=fused,
                ok=ok,
                inference_ms=elapsed_ms,
            )
        if write_collect_data_enabled or plc.save_training_images or force_save_diagnostics:
            LOG_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            for frame, score in zip(frames, per_cam_scores):
                filename = LOG_IMAGE_DIR / f"{timestamp}_ti{trigger_idx:06d}_cam{frame.cam_id}_s{score:.3f}.png"
                cv2.imwrite(str(filename), frame.image)
            force_save_diagnostics = False

    def on_inference_failed(trigger_idx: int, message: str) -> None:
        nonlocal pending_batches
        pending_batches = max(0, pending_batches - 1)
        modbus_state.set_inspection_busy(pending_batches > 0)
        modbus_state.set_error(VisionErrorCode.INTERNAL_INSPECTION_EXCEPTION)
        recipe_id, revision = current_recipe_result_fields()
        publish_inspection_result(InspectionResult(
            trigger_index=trigger_idx,
            recipe_id=recipe_id,
            recipe_revision=revision,
            result_code=ResultCode.INSPECTION_SYSTEM_ERROR,
            ok=False,
            error_code=VisionErrorCode.INTERNAL_INSPECTION_EXCEPTION,
        ))
        jlog("batch_inference_failed", trigger_idx=trigger_idx, error=message)

    def on_recipe_loaded(sequence: int, recipe_id: int, revision: int, model_mask: int) -> None:
        modbus_state.set_model_ready_mask(model_mask)
        modbus_state.set_recipe_loaded(recipe_id, revision, sequence)
        refresh_inspection_ready()
        jlog("recipe_loaded", recipe_id=recipe_id, revision=revision, sequence=sequence)

    def on_recipe_failed(sequence: int, recipe_id: int, revision: int, message: str) -> None:
        del sequence, recipe_id, revision
        modbus_state.set_error(VisionErrorCode.MODEL_LOADING_FAILED)
        modbus_state.set_inspection_ready(False)
        publisher.fail_safe()
        publish_status()
        jlog("recipe_load_failed", error=message)

    inference_thread = QThread()
    inference_worker = BatchInferenceWorker(
        backends=backends,
        input_size=initial_runtime.input_size,
        threshold=initial_runtime.ok_threshold,
        camera_rois=initial_runtime.definition.camera_rois,
        allow_mock_models=allow_mock_models,
    )
    inference_worker.moveToThread(inference_thread)
    inference_controller = BatchInferenceController(
        on_inference_completed,
        on_inference_failed,
        on_recipe_loaded,
        on_recipe_failed,
    )
    inference_controller.requested.connect(inference_worker.process, Qt.QueuedConnection)
    inference_controller.recipe_requested.connect(inference_worker.reconfigure_recipe, Qt.QueuedConnection)
    inference_worker.completed.connect(inference_controller.completed, Qt.QueuedConnection)
    inference_worker.failed.connect(inference_controller.failed, Qt.QueuedConnection)
    inference_worker.recipe_loaded.connect(inference_controller.recipe_loaded, Qt.QueuedConnection)
    inference_worker.recipe_failed.connect(inference_controller.recipe_failed, Qt.QueuedConnection)
    inference_thread.start()

    def request_recipe_load(recipe_id: int, revision: int, sequence: int) -> None:
        try:
            runtime = recipe_repository.load(recipe_id, revision)
        except RecipeNotFoundError as exc:
            modbus_state.set_error(VisionErrorCode.REQUESTED_RECIPE_NOT_FOUND)
            error_message = str(exc)
        except RecipeRevisionError as exc:
            modbus_state.set_error(VisionErrorCode.RECIPE_REVISION_MISMATCH)
            error_message = str(exc)
        except RecipeError as exc:
            modbus_state.set_error(VisionErrorCode.RECIPE_CONFIGURATION_INVALID)
            error_message = str(exc)
        else:
            inference_controller.recipe_requested.emit(runtime, sequence)
            return
        modbus_state.set_inspection_ready(False)
        publisher.fail_safe()
        jlog("recipe_request_rejected", error=error_message)
        publish_status()

    def execute_command(event: ProtocolEvent) -> None:
        nonlocal force_save_diagnostics
        command = PlcCommand(event.plc.command_code)
        if command is PlcCommand.RESET_VISION_ERROR:
            modbus_state.clear_error()
        elif command is PlcCommand.RESET_INSPECTION_COUNTERS:
            modbus_state.reset_counters()
        elif command in (PlcCommand.RELOAD_ACTIVE_RECIPE, PlcCommand.RELOAD_MODELS):
            recipe_id, revision = current_recipe_result_fields()
            request_recipe_load(recipe_id, revision, event.plc.recipe_change_sequence)
        elif command is PlcCommand.SAVE_DIAGNOSTIC_IMAGES:
            force_save_diagnostics = True
        elif command is PlcCommand.RESTART_CAMERA_ACQUISITION:
            for camera in cam_workers:
                camera.stop()
            for camera in cam_workers:
                camera.wait(1500)
                camera.start()
        modbus_state.acknowledge_command(event.sequence)
        publish_status()
        jlog("plc_command_completed", command=int(command), sequence=event.sequence)

    def dispatch_modbus_events() -> None:
        while True:
            try:
                event_name, payload = modbus_events.get_nowait()
            except Empty:
                break
            if event_name == "recipe_change" and isinstance(payload, ProtocolEvent):
                request_recipe_load(payload.plc.requested_recipe_id, payload.plc.requested_recipe_revision, payload.sequence)
            elif event_name == "command" and isinstance(payload, ProtocolEvent):
                try:
                    execute_command(payload)
                except (ValueError, RuntimeError) as exc:
                    modbus_state.set_error(VisionErrorCode.INTERNAL_INSPECTION_EXCEPTION)
                    jlog("plc_command_failed", error=str(exc), sequence=payload.sequence)
            elif event_name == "health_degraded":
                modbus_state.set_inspection_ready(False)
                publisher.fail_safe()
                publish_status()
            elif event_name == "health_recovered":
                refresh_inspection_ready()
        publish_status()

    modbus_timer = QTimer()
    modbus_timer.setInterval(50)
    modbus_timer.timeout.connect(dispatch_modbus_events)
    modbus_timer.start()

    def on_batch(trigger_idx: int, frames: list) -> None:
        nonlocal pending_batches
        for frame in frames:
            bus.frame_preview.emit(trigger_idx, frame.cam_id, np_to_qimage(frame.image))
        plc = modbus_state.plc_snapshot()
        recipe_id, revision = current_recipe_result_fields()
        if not accepting_inspections:
            return
        if modbus_cfg.enabled and (plc.bypass_requested or not plc.inspection_enabled):
            publish_inspection_result(InspectionResult(
                trigger_index=trigger_idx,
                recipe_id=recipe_id,
                recipe_revision=revision,
                result_code=ResultCode.INSPECTION_BYPASSED,
                ok=True,
                bypass_active=True,
            ))
            return
        if not modbus_state.inspection_allowed(modbus_required()):
            modbus_state.set_error(VisionErrorCode.MODBUS_CONNECTION_UNAVAILABLE)
            publish_inspection_result(InspectionResult(
                trigger_index=trigger_idx,
                recipe_id=recipe_id,
                recipe_revision=revision,
                result_code=ResultCode.INSPECTION_SYSTEM_ERROR,
                ok=False,
                error_code=VisionErrorCode.MODBUS_CONNECTION_UNAVAILABLE,
            ))
            return
        if pending_batches >= MAX_PENDING_INFERENCES:
            modbus_state.increment_dropped_trigger_count()
            modbus_state.set_error(VisionErrorCode.RESULT_QUEUE_FULL)
            publish_inspection_result(InspectionResult(
                trigger_index=trigger_idx,
                recipe_id=recipe_id,
                recipe_revision=revision,
                result_code=ResultCode.INSPECTION_TIMEOUT,
                ok=False,
                error_code=VisionErrorCode.RESULT_QUEUE_FULL,
            ))
            return
        pending_batches += 1
        modbus_state.set_inspection_busy(True)
        inference_controller.requested.emit(trigger_idx, frames)

    coordinator.batch_ready.connect(on_batch)

    def on_partial_batch_dropped(trigger_idx: int, received_camera_mask: int) -> None:
        modbus_state.increment_missing_frame_count()
        modbus_state.increment_dropped_trigger_count()
        modbus_state.set_error(VisionErrorCode.MISSING_CAMERA_FRAME)
        recipe_id, revision = current_recipe_result_fields()
        publish_inspection_result(InspectionResult(
            trigger_index=trigger_idx,
            recipe_id=recipe_id,
            recipe_revision=revision,
            result_code=ResultCode.MISSING_CAMERA_FRAME,
            ok=False,
            ng_camera_mask=required_mask & ~received_camera_mask,
            missing_camera_mask=required_mask & ~received_camera_mask,
            error_code=VisionErrorCode.MISSING_CAMERA_FRAME,
        ))

    coordinator.partial_batch_dropped.connect(on_partial_batch_dropped)

    win = MainWindow(ui_path="app/ui/mainWidget.ui")
    win._setup_ui(bus)
    win.resize(1920, 1080)

    def handle_write_config(payload: object) -> None:
        nonlocal write_config_enabled
        write_config_enabled = bool(payload.get("write_config", False)) if isinstance(payload, dict) else bool(payload)

    def handle_collect_data(payload: object) -> None:
        nonlocal write_collect_data_enabled
        write_collect_data_enabled = bool(payload.get("write_collect_data", False)) if isinstance(payload, dict) else bool(payload)

    win.writeConfig.connect(handle_write_config)
    win.writeCollectData.connect(handle_collect_data)

    def shutdown() -> None:
        nonlocal accepting_inspections, shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        accepting_inspections = False
        modbus_timer.stop()
        modbus_state.set_application_alive(False)
        modbus_state.set_inspection_ready(False)
        if modbus_cfg.enabled:
            modbus_worker.stop()
            modbus_worker.join(timeout=3.0)
        inference_thread.requestInterruption()
        inference_thread.quit()
        inference_thread.wait(3000)
        for camera in cam_workers:
            camera.stop()
        for camera in cam_workers:
            camera.wait(1500)
        try:
            dio.stop()
        except Exception as exc:
            jlog("dio_stop_error", error=str(exc))

    def handle_quit() -> None:
        shutdown()
        win.close()
        QApplication.instance().quit()

    win.quitRequested.connect(handle_quit)
    win.show()
    publish_status()
    jlog("app_start", modbus_enabled=modbus_cfg.enabled)
    return_code = app.exec_()
    shutdown()
    sys.exit(return_code)


if __name__ == "__main__":
    main()
