"""Development-only local Modbus/TCP PLC simulator for the AIGaikan register map.

Run from the project root after installing requirements:
    python tools/plc_simulator.py --port 5020

Commands: enable, bypass, recipe <id> <revision>, speed <mm_per_s>, heartbeat,
command <0-6>, ack, status, quit.
"""

from __future__ import annotations

import argparse
import importlib
import threading
from typing import Any

from app.core.modbus.data_types import uint32_to_words, words_to_uint32
from app.core.modbus.register_map import PC_TO_PLC_START, PLC_TO_PC_START


class SimulatorRegisters:
    """Shared datastore facade used by the interactive shell and Modbus server."""

    def __init__(self, context: Any):
        self._context = context
        self._recipe_sequence = 0
        self._command_sequence = 0
        self._heartbeat = 0

    def _set(self, offset: int, values: list[int]) -> None:
        self._context[0].setValues(3, offset, values)

    def _get(self, offset: int, count: int = 1) -> list[int]:
        return self._context[0].getValues(3, offset, count)

    def heartbeat(self) -> None:
        self._heartbeat = (self._heartbeat + 1) & 0xFFFF
        self._set(111, [self._heartbeat])

    def recipe(self, recipe_id: int, revision: int) -> None:
        self._recipe_sequence = (self._recipe_sequence + 1) & 0xFFFFFFFF
        low, high = uint32_to_words(self._recipe_sequence)
        self._set(101, [recipe_id, revision, low, high])

    def control_bit(self, bit: int, enabled: bool) -> None:
        word = self._get(100)[0]
        word = word | (1 << bit) if enabled else word & ~(1 << bit)
        self._set(100, [word])

    def command(self, command: int) -> None:
        self._command_sequence = (self._command_sequence + 1) & 0xFFFF
        self._set(113, [command, self._command_sequence])

    def speed(self, value: float) -> None:
        self._set(105, [max(0, min(0xFFFF, round(value * 100)))])

    def acknowledge_latest_result(self) -> None:
        low, high = self._get(125, 2)
        self._set(109, [low, high])

    def print_status(self) -> None:
        plc = self._get(PLC_TO_PC_START, 20)
        pc = self._get(PC_TO_PLC_START, 30)
        print(
            "PLC control=0x%04X heartbeat=%d | PC status=0x%04X recipe=%d rev=%d "
            "result seq=%d code=%d pending=%s error=%d"
            % (
                plc[0], plc[11], pc[0], pc[1], pc[2], words_to_uint32(pc[5], pc[6]),
                pc[7], bool(pc[0] & (1 << 6)), pc[18],
            )
        )


def start_server(host: str, port: int):
    """Start the Pymodbus 3.7 server on a daemon thread and return its datastore."""
    try:
        datastore = importlib.import_module("pymodbus.datastore")
        ModbusSlaveContext = datastore.ModbusSlaveContext
        ModbusSequentialDataBlock = datastore.ModbusSequentialDataBlock
        ModbusServerContext = datastore.ModbusServerContext
        StartTcpServer = importlib.import_module("pymodbus.server").StartTcpServer
    except ImportError as exc:
        raise SystemExit("pymodbus==3.7.4 is required; install requirements.txt first") from exc

    device = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * 1000), zero_mode=True)
    context = ModbusServerContext(slaves=device, single=True)
    thread = threading.Thread(
        target=StartTcpServer,
        kwargs={"context": context, "address": (host, port)},
        daemon=True,
        name="PymodbusSimulator",
    )
    thread.start()
    return context


def main() -> None:
    parser = argparse.ArgumentParser(description="AIGaikan Modbus/TCP PLC simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    args = parser.parse_args()
    registers = SimulatorRegisters(start_server(args.host, args.port))
    print(f"Simulator listening on {args.host}:{args.port}; Modbus unit/device ID 1.")
    print("Commands: enable <on|off>, bypass <on|off>, recipe <id> <rev>, speed <value>,")
    print("heartbeat, command <0-6>, ack, status, quit")
    while True:
        try:
            parts = input("plc> ").strip().split()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not parts:
            continue
        try:
            if parts[0] == "quit":
                return
            if parts[0] == "enable":
                registers.control_bit(0, parts[1].lower() == "on")
            elif parts[0] == "bypass":
                registers.control_bit(1, parts[1].lower() == "on")
            elif parts[0] == "recipe":
                registers.recipe(int(parts[1]), int(parts[2]))
            elif parts[0] == "speed":
                registers.speed(float(parts[1]))
            elif parts[0] == "heartbeat":
                registers.heartbeat()
            elif parts[0] == "command":
                registers.command(int(parts[1]))
            elif parts[0] == "ack":
                registers.acknowledge_latest_result()
            elif parts[0] == "status":
                registers.print_status()
            else:
                print("Unknown command")
        except (IndexError, ValueError) as exc:
            print(f"Invalid command: {exc}")


if __name__ == "__main__":
    main()
