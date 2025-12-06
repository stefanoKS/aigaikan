# IC4 multi-camera capture workers for DFK 33UX287

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Callable

from PyQt5.QtCore import QThread, pyqtSignal
import numpy as np

# ---- Global IC4 initialization ----

try:
    import imagingcontrol4 as ic4
    try:
        ic4.Library.init(
            api_log_level=ic4.LogLevel.WARNING,
            log_targets=ic4.LogTarget.STDERR,
        )
        print("[IC4] Library.init() succeeded")
        _IC4_INITIALIZED = True
    except Exception as e:
        print("[IC4] Library.init() failed:", e)
        _IC4_INITIALIZED = False
except Exception:
    ic4 = None  # Allow import on dev machines without IC4
    _IC4_INITIALIZED = False


# ---- Configuration dataclass ----

@dataclass
class CameraConfig:
    serial: str
    model: str = "DFK 33UX287"
    resolution: tuple[int, int] = (640, 480)
    pixel_format: str = "Mono8"  # or "RGB8" if your model supports it
    exposure_us: int = 2000
    gain_db: float = 0.0
    trigger_selector: str = "FrameStart"
    trigger_mode: str = "On"
    trigger_source: str = "Any"  # e.g., "Line1", "Software", etc.
    auto_exposure: str = "Off"
    auto_gain: str = "Off"
    auto_whiteBalance: str = "Off"


class CameraFrame:
    __slots__ = ("cam_id", "trigger_index", "ts_hw", "ts_host", "image")

    def __init__(self, cam_id: int, trigger_index: int, ts_hw: float, ts_host: float, image: np.ndarray):
        self.cam_id = cam_id
        self.trigger_index = trigger_index
        self.ts_hw = ts_hw
        self.ts_host = ts_host
        self.image = image


class CameraWorker(QThread):
    """
    QThread that owns one IC4 Grabber and emits CameraFrame on each triggered image.
    """
    frame_signal = pyqtSignal(object)  # emits CameraFrame
    connected = pyqtSignal(bool)

    def __init__(self, cam_id: int, cfg: CameraConfig, shared_trigger_counter: Callable[[], int]):
        super().__init__()
        self.cam_id = cam_id
        self.cfg = cfg
        self._stop = False
        # shared_trigger_counter: callable -> int (reads last seen TriggerIndex from DIO layer)
        self._read_trigger_index = shared_trigger_counter

    def stop(self):
        self._stop = True

    def run(self):
        # Mock mode when IC4 is not available or not initialized
        if ic4 is None or not _IC4_INITIALIZED:
            print(f"[IC4] Mock mode for cam_id={self.cam_id}")
            self.connected.emit(False)
            while not self._stop:
                img = (np.random.rand(self.cfg.resolution[1], self.cfg.resolution[0]) * 255).astype(np.uint8)
                ti = self._read_trigger_index()
                self.frame_signal.emit(
                    CameraFrame(self.cam_id, ti, 0.0, time.perf_counter(), img)
                )
                self.msleep(50)
            return

        grabber = None
        m = None
        sink = None

        try:
            # Enumerate and find camera by serial
            devs = ic4.DeviceEnum.devices()
            dev_info = next((d for d in devs if d.serial == self.cfg.serial), None)
            if dev_info is None:
                print(f"[IC4] Camera with serial {self.cfg.serial} not found.")
                self.connected.emit(False)
                return

            grabber = ic4.Grabber(dev_info)
            m = grabber.device_property_map

            # Reset to defaults where possible
            m.try_set_value(ic4.PropId.USER_SET_SELECTOR, "Default")
            m.try_set_value(ic4.PropId.USER_SET_LOAD, 1)

            # Resolution & pixel format
            width, height = self.cfg.resolution
            m.try_set_value(ic4.PropId.PIXEL_FORMAT, self.cfg.pixel_format)
            m.try_set_value(ic4.PropId.WIDTH, width)
            m.try_set_value(ic4.PropId.HEIGHT, height)
            m.try_set_value(ic4.PropId.OFFSET_AUTO_CENTER, True)

            # Exposure / Gain
            m.try_set_value(ic4.PropId.EXPOSURE_AUTO, self.cfg.auto_exposure)
            m.try_set_value(ic4.PropId.EXPOSURE_TIME, self.cfg.exposure_us)
            m.try_set_value(ic4.PropId.GAIN_AUTO, self.cfg.auto_gain)
            m.try_set_value(ic4.PropId.GAIN, self.cfg.gain_db)
            m.try_set_value(ic4.PropId.BALANCE_WHITE_AUTO, self.cfg.auto_whiteBalance)

            # Trigger
            m.try_set_value(ic4.PropId.TRIGGER_SELECTOR, self.cfg.trigger_selector)
            m.set_value(ic4.PropId.TRIGGER_MODE, self.cfg.trigger_mode)
            m.set_value(ic4.PropId.TRIGGER_SOURCE, self.cfg.trigger_source)

            # Extra trigger config from your working IC4 setup
            m.try_set_value(ic4.PropId.TRIGGER_ACTIVATION, "FallingEdge")
            m.try_set_value(ic4.PropId.TRIGGER_DELAY, 3.1)
            m.try_set_value(ic4.PropId.TRIGGER_DEBOUNCER, 0.0)
            m.try_set_value(ic4.PropId.TRIGGER_DENOISE, 0.0)
            m.try_set_value(ic4.PropId.TRIGGER_MASK, 0.0)

            # (Optional strobe settings if you ever move strobe control into camera)
            # m.try_set_value(ic4.PropId.STROBE_OPERATION, "Exposure")
            # m.try_set_value(ic4.PropId.STROBE_POLARITY, "ActiveLow")
            # m.try_set_value(ic4.PropId.STROBE_DELAY, 0)

            listener = _IC4QueueListener(self)
            sink = ic4.QueueSink(listener)
            grabber.stream_setup(sink)

            self.connected.emit(True)
            print(f"[IC4] CameraWorker started for cam_id={self.cam_id}")

            while not self._stop:
                self.msleep(1)

        finally:
            # Clean shutdown of camera in THIS thread (not in GC)
            if grabber is not None:
                try:
                    grabber.stream_stop()
                except Exception as e:
                    print("[IC4] stream_stop error:", e)
                try:
                    # Switch trigger mode off before closing
                    if m is not None:
                        m.set_value(ic4.PropId.TRIGGER_MODE, "Off")
                except Exception as e:
                    print("[IC4] trigger off error:", e)
                try:
                    grabber.device_close()
                except Exception as e:
                    print("[IC4] device_close error:", e)

            # Drop references so __del__ won't try to touch Library after shutdown
            m = None
            sink = None
            grabber = None


# ---- Processing the IC4 image buffer to handle different formats ----

def ic4_buffer_to_numpy(buf) -> np.ndarray | None:
    """
    Convert IC4 ImageBuffer to a numpy array using the SDK's numpy_copy().
    Returns:
        (H, W) for Mono8
        (H, W, C) for color formats
    """
    try:
        arr = buf.numpy_copy()  # uses ImageBuffer.numpy_copy from IC4
    except Exception as e:
        print("[IC4] numpy_copy() failed:", e)
        return None

    # For Mono8, shape is (H, W, 1) -> squeeze the last axis
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]

    return arr


# ---- IC4 listener at module level (no Pylance “variable in type expression”) ----

if ic4 is not None:

    class _IC4QueueListener(ic4.QueueSinkListener):
        def __init__(self, parent: CameraWorker):
            super().__init__()
            self._parent = parent

        def sink_connected(self, sink, image_type, min_buffers_required: int) -> bool:
            # We could inspect image_type.width/height/pixel_format here if needed
            return True

        def frames_queued(self, sink):
            # If we are stopping, drain and drop frames quietly
            if self._parent._stop:
                try:
                    buf = sink.pop_output_buffer()
                    # Just discard
                    _ = buf
                except Exception:
                    pass
                return

            try:
                buf = sink.pop_output_buffer()
                img = ic4_buffer_to_numpy(buf)
                if img is None:
                    return

                ts_meta = buf.meta_data  # has device_frame_number, device_timestamp_ns, etc.
                ts_hw = ts_meta.device_timestamp_ns / 1e9 if ts_meta else 0.0
                ti = self._parent._read_trigger_index()

                self._parent.frame_signal.emit(
                    CameraFrame(self._parent.cam_id, ti, ts_hw, time.perf_counter(), img)
                )

                # Optional debug:
                # print(f"[IC4] cam {self._parent.cam_id} frame shape={img.shape}, dtype={img.dtype}")

            except Exception as e:
                print("[IC4] frames_queued error:", e)
                return

else:
    # When ic4 is missing, we never instantiate this anyway
    _IC4QueueListener = None
