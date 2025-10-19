# IC4 multi-camera capture workers for DFK 33UX287

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional
from PyQt5.QtCore import QThread, pyqtSignal
import numpy as np

try:
    import imagingcontrol4 as ic4
except Exception:
    ic4 = None  # Allow import on dev machines without IC4


@dataclass
class CameraConfig:
    serial: str
    model: str = "DFK 33UX287"
    resolution: tuple[int, int] = (1920, 1200)
    pixel_format: str = "Mono8"  # or "RGB8" if your model supports
    exposure_us: int = 2000
    gain_db: float = 0.0
    trigger_selector: str = "FrameStart"
    trigger_mode: str = "On"


class CameraFrame:
    __slots__ = ("cam_id", "trigger_index", "ts_hw", "ts_host", "image")

    def __init__(self, cam_id: int, trigger_index: int, ts_hw: float, ts_host: float, image: np.ndarray):
        self.cam_id = cam_id
        self.trigger_index = trigger_index
        self.ts_hw = ts_hw
        self.ts_host = ts_host
        self.image = image


class CameraWorker(QThread):
    frame_signal = pyqtSignal(object)  # emits CameraFrame
    connected = pyqtSignal(bool)

    def __init__(self, cam_id: int, cfg: CameraConfig, shared_trigger_counter):
        super().__init__()
        self.cam_id = cam_id
        self.cfg = cfg
        self._stop = False
        # shared_trigger_counter: callable -> int (reads last seen TriggerIndex from DIO layer)
        self._read_trigger_index = shared_trigger_counter

    def stop(self):
        self._stop = True

    def run(self):
        if ic4 is None:
            # Dev/mock mode: emit blank frames at ~20 FPS for plumbing tests
            self.connected.emit(False)
            while not self._stop:
                img = (np.random.rand(480, 640) * 255).astype(np.uint8)
                ti = self._read_trigger_index()
                self.frame_signal.emit(CameraFrame(self.cam_id, ti, 0.0, time.perf_counter(), img))
                self.msleep(50)
            return

        with ic4.Library.init_context(api_log_level=ic4.LogLevel.WARN):
            # Open specific device by serial
            devs = ic4.DeviceEnum.devices()
            dev_info = next((d for d in devs if d.serial == self.cfg.serial), None)
            if dev_info is None:
                self.connected.emit(False)
                return

            grabber = ic4.Grabber(dev_info)
            m = grabber.device_property_map

            # Reset to defaults where possible
            m.try_set_value(ic4.PropId.USER_SET_SELECTOR, "Default")
            m.try_set_value(ic4.PropId.USER_SET_LOAD, 1)

            # Resolution and pixel format (adjust as supported by your camera)
            try:
                m.try_set_value(ic4.PropId.VIDEO_FORMAT, f"{self.cfg.pixel_format} {self.cfg.resolution[0]}x{self.cfg.resolution[1]}")
            except Exception:
                pass

            # Exposure/Gain
            m.try_set_value(ic4.PropId.EXPOSURE_AUTO, "Off")
            m.try_set_value(ic4.PropId.EXPOSURE_TIME, self.cfg.exposure_us)
            m.try_set_value(ic4.PropId.GAIN_AUTO, "Off")
            m.try_set_value(ic4.PropId.GAIN, self.cfg.gain_db)

            # Trigger
            m.try_set_value(ic4.PropId.TRIGGER_SELECTOR, self.cfg.trigger_selector)
            m.set_value(ic4.PropId.TRIGGER_MODE, self.cfg.trigger_mode)

            parent = self

            class Listener(ic4.QueueSinkListener):
                def sink_connected(self, sink: ic4.QueueSink, image_type: ic4.ImageType, min_buffers_required: int) -> bool:
                    sink.set_min_num_buffers(max(min_buffers_required, 12))
                    return True

                def frames_queued(self, sink: ic4.QueueSink):
                    buf = sink.pop_output_buffer()
                    # Obtain numpy image (method may differ by IC4 version)
                    try:
                        img = buf.as_numpy()
                    except Exception:
                        # Fallback copy
                        w, h = buf.image_width, buf.image_height
                        img = np.frombuffer(buf.get_image_data(), dtype=np.uint8).reshape(h, w)

                    ts_hw = getattr(buf, "timestamp", 0.0)
                    ti = parent._read_trigger_index()
                    parent.frame_signal.emit(CameraFrame(parent.cam_id, ti, ts_hw, time.perf_counter(), img))

            listener = Listener()
            sink = ic4.QueueSink(listener)
            grabber.stream_setup(sink)
            self.connected.emit(True)

            while not self._stop:
                self.msleep(1)

            grabber.stream_stop()
            m.set_value(ic4.PropId.TRIGGER_MODE, "Off")
            grabber.device_close()
