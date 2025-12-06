# app/core/dio_client.py
# CONTEC DIO-1616LN-USB integration via ctypes, with safe fallback to a mock.
# Uses official API-DIO(WDM) signatures:
#   short DioInit    (char* DeviceName, short* Id)
#   short DioExit    (short Id)
#   short DioInpBit  (short Id, short BitNo, short* Data)
#   short DioOutBit  (short Id, short BitNo, unsigned char Data)

from __future__ import annotations
import ctypes as C
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class DIOConfig:
    dll_paths: list[str]
    device_name: str = "DIO000"   # CONTEC logical name, e.g. "DIO000"
    device_index: int = 0         # kept for backwards compat (not used)
    input_port: int = 0
    trigger_bit: int = 0
    output_port: Optional[int] = None
    ok_bit: Optional[int] = None
    poll_hz: int = 2000


class _ContecDLL:
    def __init__(self, dll_paths: list[str]):
        last_err = None
        self.lib = None

        print("[DIO] Trying to load DLL from paths:", dll_paths)
        for p in dll_paths:
            try:
                # If it's just "cdio.dll", let Windows resolve from PATH
                if os.path.basename(p).lower().endswith(".dll"):
                    print(f"[DIO] Trying to load DLL: {p}")
                    self.lib = C.WinDLL(p)
                else:
                    # Accept bare names too
                    print(f"[DIO] Trying to load DLL: {p}")
                    self.lib = C.WinDLL(p)
                print(f"[DIO] Loaded DLL: {p}")
                break
            except Exception as e:
                print(f"[DIO] Failed to load {p}: {e}")
                last_err = e

        if not self.lib:
            raise RuntimeError(f"Failed to load CONTEC DIO DLL from {dll_paths}: {last_err}")

        self._bind_api()

    def _bind_api(self):
        lib = self.lib

        # Get functions (may be None if not exported)
        self.DioInit   = getattr(lib, "DioInit",   None)
        self.DioExit   = getattr(lib, "DioExit",   None)
        self.DioInpBit = getattr(lib, "DioInpBit", None)
        self.DioOutBit = getattr(lib, "DioOutBit", None)

        # Set signatures based on CONTEC API-DIO(WDM) docs
        def set_sig(f, restype, *argtypes):
            if f is None:
                return
            f.restype = restype
            f.argtypes = argtypes

        # short DioInit(char* DeviceName, short* Id)
        set_sig(self.DioInit, C.c_short, C.c_char_p, C.POINTER(C.c_short))
        # short DioExit(short Id)
        set_sig(self.DioExit, C.c_short, C.c_short)
        # short DioInpBit(short Id, short BitNo, short* Data)
        set_sig(self.DioInpBit, C.c_short, C.c_short, C.c_short, C.POINTER(C.c_short))
        # short DioOutBit(short Id, short BitNo, unsigned char Data)
        set_sig(self.DioOutBit, C.c_short, C.c_short, C.c_short, C.c_ubyte)


class BaseDIO:
    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def read_trigger_index(self) -> int:
        raise NotImplementedError

    def set_ok_ng(self, ok: bool):
        raise NotImplementedError

    def set_cam_ok(self, per_cam_ok: list[bool]):
        """Optional: per-camera OK mapping. Default: not implemented."""
        raise NotImplementedError

class RealDIO(BaseDIO):
    """Real CONTEC DIO using API-DIO(WDM)."""

    def __init__(self, cfg: DIOConfig):
        print("[DIO] Initializing RealDIO with config:", cfg)
        self.cfg = cfg
        self._dll = _ContecDLL(cfg.dll_paths)
        self._stop = False
        self._lock = threading.Lock()
        self._trigger_index = 0
        self._last_bit = 0
        self._id = C.c_short(-1)

        # Initialize device
        if self._dll.DioInit is None:
            raise RuntimeError("DioInit not found in cdio.dll")

        dev_name = cfg.device_name.encode("ascii")
        ret = self._dll.DioInit(dev_name, C.byref(self._id))
        print(f"[DIO] DioInit('{cfg.device_name}') ret={ret}, Id={self._id.value}")
        if ret != 0:
            raise RuntimeError(f"DioInit failed rc={ret}")

        self._t = threading.Thread(target=self._run_poll_edges, daemon=True)

    def start(self):
        print("[DIO] RealDIO starting poll thread")
        self._stop = False
        self._t.start()

    def stop(self):
        print("[DIO] RealDIO stopping")
        self._stop = True
        self._t.join(timeout=1)
        if self._dll.DioExit is not None and self._id.value >= 0:
            ret = self._dll.DioExit(self._id)
            print(f"[DIO] DioExit ret={ret}")

    # --- Helpers for bit index mapping (port, bit) -> logical BitNo ----
    def _bit_no(self, port: int, bit: int) -> int:
        # API docs define "logical bit number" as 0..N; for 16-bit ports this is port*16 + bit
        return port * 16 + bit

    def _read_input_bit(self, port: int, bit: int) -> int:
        if self._dll.DioInpBit is None:
            # Fall back: no direct bit function available
            # (could extend to use DioInpByte/DioInp if needed)
            print("[DIO] DioInpBit is None; returning 0")
            return 0

        bit_no = self._bit_no(port, bit)
        data = C.c_short(0)
        ret = self._dll.DioInpBit(self._id, C.c_short(bit_no), C.byref(data))
        if ret != 0:
            # On error, just treat as 0
            # print(f"[DIO] DioInpBit error ret={ret} for bitNo={bit_no}")
            return 0
        return int(data.value & 1)

    def _write_ok_ng(self, ok: bool):
        """Write overall OK/NG to configured output bit."""
        if self.cfg.output_port is None or self.cfg.ok_bit is None:
            return
        if self._dll.DioOutBit is None:
            print("[DIO] DioOutBit not available; cannot drive DO line")
            return

        bit_no = self._bit_no(self.cfg.output_port, self.cfg.ok_bit)
        val = 1 if ok else 0
        ret = self._dll.DioOutBit(self._id, C.c_short(bit_no), C.c_ubyte(val))
        # Uncomment for verbose logging:
        # print(f"[DIO] DioOutBit(Id={self._id.value}, BitNo={bit_no}, Data={val}) ret={ret}")

    def _run_poll_edges(self):
        interval = max(1.0 / float(self.cfg.poll_hz), 0.0005)
        print(f"[DIO] Polling DI at ~{self.cfg.poll_hz} Hz (interval {interval*1000:.3f} ms)")
        while not self._stop:
            b = self._read_input_bit(self.cfg.input_port, self.cfg.trigger_bit)
            if b and not self._last_bit:
                with self._lock:
                    self._trigger_index += 1
            self._last_bit = b
            time.sleep(interval)

    def read_trigger_index(self) -> int:
        with self._lock:
            return self._trigger_index

    def set_ok_ng(self, ok: bool):
        self._write_ok_ng(ok)

    def set_cam_ok(self, per_cam_ok: list[bool]):
        """
        Drive one DO bit per camera.
        Camera i -> bit (ok_bit + i) on output_port.

        Example:
            output_port = 0, ok_bit = 0, 4 cameras
            cam0 -> DO0, cam1 -> DO1, cam2 -> DO2, cam3 -> DO3
        """
        if self.cfg.output_port is None or self.cfg.ok_bit is None:
            return
        if self._dll.DioOutBit is None:
            print("[DIO] DioOutBit not available; cannot drive DO lines")
            return

        base_bit = self.cfg.ok_bit
        for cam_id, ok in enumerate(per_cam_ok):
            bit_no = self._bit_no(self.cfg.output_port, base_bit + cam_id)
            val = 1 if ok else 0
            ret = self._dll.DioOutBit(self._id, C.c_short(bit_no), C.c_ubyte(val))
            # Uncomment for debugging:
            # print(f"[DIO] set_cam_ok cam{cam_id}: bitNo={bit_no}, val={val}, ret={ret}")


class MockDIO(BaseDIO):
    """Mock DIO when DLL/device is not available. Generates trigger index and ignores DO."""

    def __init__(self, hz: int = 20):
        print("[DIO] Using MockDIO (no real hardware)")
        self._stop = False
        self._ti = 0
        self._hz = hz
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._stop = False
        self._t.start()

    def stop(self):
        self._stop = True
        self._t.join(timeout=1)

    def _run(self):
        interval = 1.0 / float(self._hz)
        while not self._stop:
            time.sleep(interval)
            self._ti += 1

    def read_trigger_index(self) -> int:
        return self._ti

    def set_ok_ng(self, ok: bool):
        # You could print here for debugging if you want
        # print(f"[MockDIO] set_ok_ng({ok})")
        pass

    def set_cam_ok(self, per_cam_ok: list[bool]):
        # For debug only; real hardware is not driven in mock mode
        # print(f"[MockDIO] set_cam_ok({per_cam_ok})")
        pass
    
def make_dio(cfg: DIOConfig | None) -> BaseDIO:
    """Factory that prefers RealDIO but falls back to MockDIO with debug prints."""
    try:
        if cfg is None:
            print("[DIO] No config provided; using MockDIO")
            return MockDIO()
        dio = RealDIO(cfg)
        print("[DIO] RealDIO created successfully")
        return dio
    except Exception as e:
        print(f"[DIO] RealDIO init failed, falling back to MockDIO: {e!r}")
        return MockDIO()
