"""Validated Modbus/TCP configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModbusConnectionConfig:
    enabled: bool
    host: str
    port: int
    device_id: int
    poll_interval_ms: int
    request_timeout_ms: int
    reconnect_interval_ms: int
    heartbeat_interval_ms: int
    heartbeat_timeout_ms: int
    max_consecutive_failures: int


@dataclass(frozen=True, slots=True)
class RegisterBlockConfig:
    plc_to_pc_start: int
    plc_to_pc_count: int
    pc_to_plc_start: int
    pc_to_plc_count: int


@dataclass(frozen=True, slots=True)
class ModbusBehaviorConfig:
    require_modbus_for_inspection: bool
    retain_unacknowledged_result: bool
    simulation_mode: bool


@dataclass(frozen=True, slots=True)
class ModbusConfig:
    connection: ModbusConnectionConfig
    registers: RegisterBlockConfig
    score_scale: int
    line_speed_scale: int
    behavior: ModbusBehaviorConfig

    @property
    def enabled(self) -> bool:
        return self.connection.enabled

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ModbusConfig":
        if not isinstance(raw, dict):
            raise ValueError("Modbus configuration must be a YAML mapping")
        try:
            connection = raw["connection"]
            registers = raw["registers"]
            data_format = raw["data_format"]
            behavior = raw["behavior"]
        except KeyError as exc:
            raise ValueError(f"Missing Modbus configuration section: {exc.args[0]}") from exc
        if data_format.get("uint32_word_order") != "low_high":
            raise ValueError("data_format.uint32_word_order must be 'low_high'")

        cfg = cls(
            connection=ModbusConnectionConfig(
                enabled=bool(connection["enabled"]),
                host=str(connection["host"]),
                port=int(connection["port"]),
                device_id=int(connection["device_id"]),
                poll_interval_ms=int(connection["poll_interval_ms"]),
                request_timeout_ms=int(connection["request_timeout_ms"]),
                reconnect_interval_ms=int(connection["reconnect_interval_ms"]),
                heartbeat_interval_ms=int(connection["heartbeat_interval_ms"]),
                heartbeat_timeout_ms=int(connection["heartbeat_timeout_ms"]),
                max_consecutive_failures=int(connection["max_consecutive_failures"]),
            ),
            registers=RegisterBlockConfig(
                plc_to_pc_start=int(registers["plc_to_pc"]["start"]),
                plc_to_pc_count=int(registers["plc_to_pc"]["count"]),
                pc_to_plc_start=int(registers["pc_to_plc"]["start"]),
                pc_to_plc_count=int(registers["pc_to_plc"]["count"]),
            ),
            score_scale=int(data_format["score_scale"]),
            line_speed_scale=int(data_format["line_speed_scale"]),
            behavior=ModbusBehaviorConfig(
                require_modbus_for_inspection=bool(behavior["require_modbus_for_inspection"]),
                retain_unacknowledged_result=bool(behavior["retain_unacknowledged_result"]),
                simulation_mode=bool(behavior["simulation_mode"]),
            ),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        c = self.connection
        r = self.registers
        if not c.host.strip():
            raise ValueError("connection.host must not be empty")
        if not 1 <= c.port <= 65535:
            raise ValueError("connection.port must be between 1 and 65535")
        if not 0 <= c.device_id <= 247:
            raise ValueError("connection.device_id must be between 0 and 247")
        for name, value in (
            ("poll_interval_ms", c.poll_interval_ms),
            ("request_timeout_ms", c.request_timeout_ms),
            ("reconnect_interval_ms", c.reconnect_interval_ms),
            ("heartbeat_interval_ms", c.heartbeat_interval_ms),
            ("heartbeat_timeout_ms", c.heartbeat_timeout_ms),
            ("max_consecutive_failures", c.max_consecutive_failures),
            ("data_format.score_scale", self.score_scale),
            ("data_format.line_speed_scale", self.line_speed_scale),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if c.heartbeat_timeout_ms < c.heartbeat_interval_ms:
            raise ValueError("heartbeat_timeout_ms must be at least heartbeat_interval_ms")
        if r.plc_to_pc_start < 0 or r.pc_to_plc_start < 0:
            raise ValueError("Modbus addresses are zero-based non-negative offsets")
        if r.plc_to_pc_count != 20 or r.pc_to_plc_count != 30:
            raise ValueError("PLC-to-PC and PC-to-PLC block counts must be 20 and 30")