# CONTEC DIO-1616LN-USB integration via ctypes, with safe fallback to a mock.
# Provides rising-edge counting on a chosen DI bit (TriggerIndex) and OK/NG DO.

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
    device_index: int = 0
    input_port: int = 0
    trigger_bit: int = 0
    output_port: Optional[int] = None
    ok_bit: Optional[int] = None
    poll_hz: int = 2000


class _ContecDLL:
    def __init__(self, dll_paths: list[str]):
        last_err = None
        self.lib = None
        for p in dll_paths:
            try:
                if os.path.exists(p) or (".dll" not in p):
                    self.lib = C.WinDLL(p)
                    break
            except Exception as e:
                last_err = e
        if not self.lib:
            raise RuntimeError(f"Failed to load CONTEC DIO DLL from {dll_paths}: {last_err}")
        self._bind_api()

    def _bind_api(self):
        lib = self.lib
        self.DioInit = getattr(lib, "DioInit", None)
        self.DioExit = getattr(lib, "DioExit", None)
        self.DioInp = getattr(lib, "DioInp", None) or getattr(lib, "DioReadPort", None)
        self.DioOut = getattr(lib, "DioOut", None) or getattr(lib, "DioWritePort", None)
        self.DioInpBit = getattr(lib, "DioInpBit", None) or getattr(lib, "DioReadBit", None)
        self.DioOutBit = getattr(lib, "DioOutBit", None) or getattr(lib, "DioWriteBit", None)

        def set_sig(f, restype, *argtypes):
            if f is None:
                return
            f.restype = restype
            f.argtypes = argtypes

        set_sig(self.DioInit, C.c_int)
        set_sig(self.DioExit, C.c_int)
        set_sig(self.DioInp, C.c_int, C.c_int, C.POINTER(C.c_ushort))
        set_sig(self.DioOut, C.c_int, C.c_int, C.c_ushort)
        set_sig(self.DioInpBit, C.c_int, C.c_int, C.c_int, C.POINTER(C.c_ushort))
        set_sig(self.DioOutBit, C.c_int, C.c_int, C.c_int, C.c_ushort)


class BaseDIO:
    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def read_trigger_index(self) -> int:
        raise NotImplementedError

    def set_ok_ng(self, ok: bool):
        raise NotImplementedError


class RealDIO(BaseDIO):
    def __init__(self, cfg: DIOConfig):
        self.cfg = cfg
        self._dll = _ContecDLL(cfg.dll_paths)
        self._stop = False
        self._lock = threading.Lock()
        self._trigger_index = 0
        self._last_bit = 0
        self._t = threading.Thread(target=self._run_poll_edges, daemon=True)
        if self._dll.DioInit:
            rc = self._dll.DioInit()
            if rc != 0:
                raise RuntimeError(f"DioInit failed rc={rc}")

    def start(self):
        self._stop = False
        self._t.start()

    def stop(self):
        self._stop = True
        self._t.join(timeout=1)
        if self._dll.DioExit:
            self._dll.DioExit()

    def _read_input_port(self) -> int:
        val = C.c_ushort(0)
        if self._dll.DioInp is None:
            bit = self._read_input_bit(self.cfg.input_port, self.cfg.trigger_bit)
            return (1 << self.cfg.trigger_bit) if bit else 0
        rc = self._dll.DioInp(self.cfg.device_index, self.cfg.input_port, C.byref(val))
        if rc != 0:
            return 0
        return int(val.value)

    def _read_input_bit(self, port: int, bit: int) -> int:
        if self._dll.DioInpBit is None:
            port_val = self._read_input_port()
            return 1 if (port_val >> bit) & 1 else 0
        out = C.c_ushort(0)
        rc = self._dll.DioInpBit(self.cfg.device_index, port, bit, C.byref(out))
        if rc != 0:
            return 0
        return int(out.value & 1)

    def _write_ok_ng(self, ok: bool):
        if self.cfg.output_port is None or self.cfg.ok_bit is None:
            return
        val = 1 if ok else 0
        if self._dll.DioOutBit is not None:
            self._dll.DioOutBit(self.cfg.device_index, self.cfg.output_port, self.cfg.ok_bit, C.c_ushort(val))
        elif self._dll.DioOut is not None:
            current = self._read_input_port()  # may need separate DO read in some SDKs
            mask = 1 << self.cfg.ok_bit
            new_val = (current | mask) if val else (current & ~mask)
            self._dll.DioOut(self.cfg.device_index, self.cfg.output_port, C.c_ushort(new_val))

    def _run_poll_edges(self):
        interval = max(1.0 / float(self.cfg.poll_hz), 0.0005)
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


class MockDIO(BaseDIO):
    """Mock DIO when DLL/device is not available. Generates trigger index and logs writes."""
    def __init__(self, hz: int = 20):
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
        # no-op; could print/log
        pass


def make_dio(cfg: DIOConfig | None) -> BaseDIO:
    try:
        if cfg is None:
            return MockDIO()
        return RealDIO(cfg)
    except Exception:
        # Fallback to mock if DLL/device missing
        return MockDIO()
