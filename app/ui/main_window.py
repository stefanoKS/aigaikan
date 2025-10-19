# Qt UI loader with safe fallback. If app/ui/mainWidget.ui exists, we load it;
# otherwise we create a simple 2x2 grid at runtime.

from PyQt5.QtWidgets import QWidget, QLabel, QGridLayout, QVBoxLayout, QApplication
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt
from PyQt5 import uic
import os
import numpy as np


def np_to_qimage(img: np.ndarray) -> QImage:
    if img.ndim == 2:
        h, w = img.shape
        return QImage(img.data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w, c = img.shape
    return QImage(img.data, w, h, w * c, QImage.Format_RGB888).copy()


class MainWindow(QWidget):
    def __init__(self, bus, ui_path: str = "app/ui/mainWidget.ui"):
        super().__init__()
        self.setWindowTitle("AI Extrusion Inspection")
        self.views = []

        if os.path.exists(ui_path):
            # Load user-provided UI
            uic.loadUi(ui_path, self)
            # Try to discover 4 label placeholders by naming convention
            # Prefer: cam0..cam3; fallback to any QLabel children
            for name in ("cam0", "cam1", "cam2", "cam3"):
                w = self.findChild(QLabel, name)
                if w is not None:
                    self.views.append(w)
            if len(self.views) < 4:
                # pick first 4 QLabel children
                for w in self.findChildren(QLabel):
                    if w not in self.views:
                        self.views.append(w)
                        if len(self.views) == 4:
                            break
            # If still fewer than 4, pad with invisible holders
            while len(self.views) < 4:
                ph = QLabel("cam")
                ph.setAlignment(Qt.AlignCenter)
                self.views.append(ph)
        else:
            # Fallback programmatic layout
            self.views = [QLabel("cam0"), QLabel("cam1"), QLabel("cam2"), QLabel("cam3")]
            for v in self.views:
                v.setAlignment(Qt.AlignCenter)
                v.setMinimumSize(320, 240)
            grid = QGridLayout()
            grid.addWidget(self.views[0], 0, 0)
            grid.addWidget(self.views[1], 0, 1)
            grid.addWidget(self.views[2], 1, 0)
            grid.addWidget(self.views[3], 1, 1)
            self.status = QLabel("Ready")
            layout = QVBoxLayout()
            layout.addLayout(grid)
            layout.addWidget(self.status)
            self.setLayout(layout)

        # Status label (create or find)
        if not hasattr(self, "status"):
            s = self.findChild(QLabel, "status")
            if s is None:
                s = QLabel("Ready")
                s.setObjectName("status")
                if self.layout() is not None:
                    self.layout().addWidget(s)
            self.status = s

        bus.frame_preview.connect(self.on_preview)
        bus.inference_result.connect(self.on_result)

    def on_preview(self, trigger_idx: int, cam_id: int, qimg: QImage):
        if cam_id < len(self.views):
            pix = QPixmap.fromImage(qimg).scaled(480, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.views[cam_id].setPixmap(pix)

    def on_result(self, trigger_idx: int, result: dict):
        fused = result.get("fused_score", 0.0)
        decision = result.get("ok", True)
        if hasattr(self, "status"):
            self.status.setText(f"TI {trigger_idx} | score={fused:.3f} | {'OK' if decision else 'NG'}")
