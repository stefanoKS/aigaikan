"""The sole synchronous pymodbus connection owner used by ModbusWorker."""

from __future__ import annotations

import importlib
from typing import Protocol

from .config import ModbusConnectionConfig


class ModbusTransportError(RuntimeError):
    """Connection, transport, or protocol response failure."""


class ModbusTransport(Protocol):
    def connect(self) -> bool: ...
    def close(self) -> None: ...
    def read_holding_registers(self, address: int, count: int) -> list[int]: ...
    def write_registers(self, address: int, values: list[int]) -> None: ...


class PymodbusTcpTransport:
    """Small adapter that isolates pymodbus 3.x call signatures."""

    def __init__(self, config: ModbusConnectionConfig):
        self._config = config
        self._client = None

    def connect(self) -> bool:
        try:
            client_module = importlib.import_module("pymodbus.client")
            ModbusTcpClient = client_module.ModbusTcpClient
        except ImportError as exc:
            raise ModbusTransportError("pymodbus is not installed") from exc
        if self._client is None:
            self._client = ModbusTcpClient(
                host=self._config.host,
                port=self._config.port,
                timeout=self._config.request_timeout_ms / 1000.0,
            )
        try:
            return bool(self._client.connect())
        except Exception as exc:
            raise ModbusTransportError(f"Modbus connect failed: {exc}") from exc

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                raise ModbusTransportError(f"Modbus close failed: {exc}") from exc
            finally:
                self._client = None

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        response = self._call("read_holding_registers", address, count=count)
        registers = getattr(response, "registers", None)
        if registers is None or len(registers) != count:
            raise ModbusTransportError(f"Invalid Modbus FC03 response at offset {address}")
        return [int(value) & 0xFFFF for value in registers]

    def write_registers(self, address: int, values: list[int]) -> None:
        if not values:
            return
        self._call("write_registers", address, values=list(values))

    def _call(self, method_name: str, address: int, **kwargs):
        if self._client is None:
            raise ModbusTransportError("Modbus client is not connected")
        method = getattr(self._client, method_name)
        try:
            try:
                response = method(address, device_id=self._config.device_id, **kwargs)
            except TypeError:
                response = method(address, slave=self._config.device_id, **kwargs)
        except Exception as exc:
            raise ModbusTransportError(f"Modbus {method_name} failed: {exc}") from exc
        if response is None or response.isError():
            raise ModbusTransportError(f"Modbus {method_name} returned an error at offset {address}")
        return response