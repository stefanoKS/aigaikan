import os
from PyQt5 import uic
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QMainWindow, QLabel, QPushButton, QApplication
import numpy as np

def np_to_qimage(img: np.ndarray) -> QImage:
    if img.ndim == 2:
        h, w = img.shape
        return QImage(img.data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w, c = img.shape
    return QImage(img.data, w, h, w * c, QImage.Format_RGB888).copy()

class MainWindow(QMainWindow):
    """
    Loads app/ui/mainWidget.ui (top-level QMainWindow) and exposes `_setup_ui()`
    to bind widgets and connect signals. No dynamic fallbacks.
    Expect preview QLabel objectNames: cam0, cam1, cam2, cam3.
    Optional status QLabel objectName: status (or use the QStatusBar).
    """

    def __init__(self, ui_path: str = "app/ui/mainWidget.ui"):
        super().__init__()
        if not os.path.exists(ui_path):
            raise FileNotFoundError(f"UI file not found: {ui_path}")
        uic.loadUi(ui_path, self)  # ui root is QMainWindow per provided .ui
        self.views = []
        for name in ("cam0", "cam1", "cam2", "cam3"):
            w = self.findChild(QLabel, name)
            if w is None:
                raise RuntimeError(f"Missing QLabel '{name}' in UI. Please add 4 labels named cam0..cam3.")
            self.views.append(w)
        # Optional status label
        self.status_label = self.findChild(QLabel, "status")
        # else: you can use self.statusBar().showMessage(...)
        # Quit button
        self.quit_button = self.findChild(QPushButton, "quit_button")
        if self.quit_button:
            self.quit_button.clicked.connect(QApplication.instance().quit)

    # ---- Call this once from run.py after creating the window ----
    def _setup_ui(self, bus):
        # Connect bus signals to UI slots
        bus.frame_preview.connect(self.on_preview)
        bus.inference_result.connect(self.on_result)

    # ---- Slots ----
    def on_preview(self, trigger_idx: int, cam_id: int, qimg: QImage):
        if 0 <= cam_id < len(self.views):
            pix = QPixmap.fromImage(qimg).scaled(480, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.views[cam_id].setPixmap(pix)

    def on_result(self, trigger_idx: int, result: dict):
        fused = result.get("fused_score", 0.0)
        decision = result.get("ok", True)
        msg = f"TI {trigger_idx} | score={fused:.3f} | {'OK' if decision else 'NG'}"
        if self.status_label is not None:
            self.status_label.setText(msg)
        else:
            self.statusBar().showMessage(msg)