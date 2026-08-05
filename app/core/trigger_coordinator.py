# Align 4 camera frames by TriggerIndex

from __future__ import annotations
from collections import defaultdict
import time
from PyQt5.QtCore import QObject, QTimer, pyqtSignal


class TriggerCoordinator(QObject):
    batch_ready = pyqtSignal(int, list)  # (trigger_idx, [CameraFrame in cam_id order])

    def __init__(self, num_cams=4, max_hold_ms=8, max_buffer_age_ms=1000):
        super().__init__()
        self.num_cams = num_cams
        self.buffers = defaultdict(dict)  # trig_idx -> {cam_id: frame}
        self._created_at: dict[int, float] = {}
        self._max_buffer_age_s = max_buffer_age_ms / 1000.0
        self.timer = QTimer()
        self.timer.setInterval(max(1, max_hold_ms // 2))
        self.timer.timeout.connect(self._purge_stale)
        self.timer.start()
        self._latest_complete = -1

    def on_frame(self, frame):
        ti = frame.trigger_index
        self._created_at.setdefault(ti, time.monotonic())
        self.buffers[ti][frame.cam_id] = frame
        if len(self.buffers[ti]) == self.num_cams:
            frames = [self.buffers[ti][cid] for cid in sorted(self.buffers[ti].keys())]
            del self.buffers[ti]
            del self._created_at[ti]
            self._latest_complete = ti
            self.batch_ready.emit(ti, frames)

    def _purge_stale(self):
        # Keep synchronization tolerant of camera jitter, but never retain partial frames forever.
        now = time.monotonic()
        to_del = [
            ti for ti in self.buffers
            if ti < self._latest_complete - 1
            or now - self._created_at[ti] >= self._max_buffer_age_s
        ]
        for ti in to_del:
            del self.buffers[ti]
            del self._created_at[ti]
