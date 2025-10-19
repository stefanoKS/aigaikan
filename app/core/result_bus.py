from PyQt5.QtCore import QObject, pyqtSignal


class ResultsBus(QObject):
    frame_preview = pyqtSignal(int, int, object)  # (trigger_idx, cam_id, qimage)
    inference_result = pyqtSignal(int, dict)     # (trigger_idx, {per_cam, fused})
    status = pyqtSignal(dict)                    # generic status updates
