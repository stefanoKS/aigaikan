import os
from PyQt5 import uic
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QMainWindow, QLabel, QPushButton, QApplication, QCheckBox
import numpy as np


def np_to_qimage(img: np.ndarray) -> QImage:
    if img.ndim == 2:
        h, w = img.shape
        return QImage(img.data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w, c = img.shape
    return QImage(img.data, w, h, w * c, QImage.Format_RGB888).copy()


class MainWindow(QMainWindow):
    # 🔹 declare the signals on the class
    quitRequested = pyqtSignal()
    writeConfig = pyqtSignal(bool)   # <--- this is what self.writeConfig refers to
    writeCollectData = pyqtSignal(bool)

    def __init__(self, ui_path: str = "app/ui/mainWidget.ui"):
        super().__init__()
        if not os.path.exists(ui_path):
            raise FileNotFoundError(f"UI file not found: {ui_path}")
        uic.loadUi(ui_path, self)

        # Camera preview QLabel widgets
        self.views = []
        for name in ("cam0", "cam1", "cam2", "cam3"):
            w = self.findChild(QLabel, name)
            if w is None:
                raise RuntimeError(f"Missing QLabel '{name}' in UI. Add cam0..cam3.")
            self.views.append(w)

        # Status label
        self.status_label = self.findChild(QLabel, "status")

        # ✅ Write Config/Log checkbox
        self.write_config_button = self.findChild(QCheckBox, "checkBox_writeConfig")
        if self.write_config_button:
            # use your existing handler
            self.write_config_button.stateChanged.connect(self._on_write_config_changed)
        
        self.write_collectData_button = self.findChild(QCheckBox, "checkBox_collectData")
        if self.write_collectData_button:
            self.write_collectData_button.stateChanged.connect(self._on_collect_data_changed)

        # Quit button hooked to quitRequested signal
        self.quit_button = self.findChild(QPushButton, "quit_button")
        if self.quit_button:
            self.quit_button.clicked.connect(self._emit_quit)

    def _emit_quit(self):
        self.quitRequested.emit()  # main program handles actual shutdown

    def _on_write_config_changed(self, state: int):
        enabled = (state == Qt.Checked)
        self.writeConfig.emit(enabled)

    def _on_collect_data_changed(self, state: int):
        enabled = (state == Qt.Checked)
        self.writeCollectData.emit(enabled)

    def _setup_ui(self, bus):
        bus.frame_preview.connect(self.on_preview)
        bus.inference_result.connect(self.on_result)
        bus.status.connect(self.on_status)

    def on_preview(self, trigger_idx: int, cam_id: int, qimg: QImage):
        if 0 <= cam_id < len(self.views):
            pix = QPixmap.fromImage(qimg).scaled(
                286, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.views[cam_id].setPixmap(pix)

    def on_result(self, trigger_idx: int, result: dict):
        fused = result.get("fused_score", 0.0)
        decision = result.get("ok", True)
        msg = f"TI {trigger_idx} | score={fused:.3f} | {'OK' if decision else 'NG'}"
        if self.status_label is not None:
            self.status_label.setText(msg)
        else:
            self.statusBar().showMessage(msg)

    def on_status(self, status: dict):
        """Show compact PLC connectivity diagnostics without redesigning the UI."""
        connected = "PLC connected" if status.get("modbus_connected") else "PLC disconnected"
        heartbeat = "heartbeat OK" if status.get("plc_heartbeat_valid") else "heartbeat waiting"
        message = (
            f"{connected} | {heartbeat} | recipe {status.get('active_recipe_id', 0)} "
            f"| ready={status.get('inspection_ready', False)} "
            f"| pending={status.get('result_pending', False)} "
            f"| error={status.get('error_code', 0)}"
        )
        self.statusBar().showMessage(message)
