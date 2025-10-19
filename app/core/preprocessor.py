import cv2
import numpy as np


def to_chw_tensor(img: np.ndarray, size=(512, 512)) -> np.ndarray:
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32) / 255.0
    img = np.transpose(img, (2, 0, 1))  # CHW
    return img


def preprocess_batch(frames, size=(512, 512)) -> np.ndarray:
    arr = [to_chw_tensor(f.image, size) for f in frames]
    return np.stack(arr, axis=0)  # (B,C,H,W) =========================
# Resize/normalize and optional unwrapping hook

import cv2
import numpy as np



def to_chw_tensor(img: np.ndarray, size=(512, 512)) -> np.ndarray:
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32) / 255.0
    img = np.transpose(img, (2, 0, 1))  # CHW
    return img



def preprocess_batch(frames, size=(512, 512)) -> np.ndarray:
    arr = [to_chw_tensor(f.image, size) for f in frames]
    return np.stack(arr, axis=0)  # (B,C,H,W) =========================
# Resize/normalize and optional unwrapping hook

import cv2
import numpy as np


def to_chw_tensor(img: np.ndarray, size=(512, 512)) -> np.ndarray:
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32) / 255.0
    img = np.transpose(img, (2, 0, 1))  # CHW
    return img


def preprocess_batch(frames, size=(512, 512)) -> np.ndarray:
    arr = [to_chw_tensor(f.image, size) for f in frames]
    return np.stack(arr, axis=0)  # (B,C,H,W)