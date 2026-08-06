"""Pure sequence-based PLC protocol event detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .register_map import PlcCommand
from .state import PlcInput


class ProtocolEventType(str, Enum):
    RECIPE_CHANGE = "recipe_change"
    COMMAND = "command"
    RESULT_ACK = "result_ack"


@dataclass(frozen=True, slots=True)
class ProtocolEvent:
    type: ProtocolEventType
    sequence: int
    plc: PlcInput


class ProtocolEngine:
    """Detect new PLC actions using sequences, not persistent command values."""

    def __init__(self):
        self._last_recipe_sequence: int | None = None
        self._last_command_sequence: int | None = None
        self._last_result_ack_sequence: int | None = None

    def observe(self, plc: PlcInput) -> list[ProtocolEvent]:
        events: list[ProtocolEvent] = []
        if self._last_recipe_sequence != plc.recipe_change_sequence:
            self._last_recipe_sequence = plc.recipe_change_sequence
            events.append(ProtocolEvent(ProtocolEventType.RECIPE_CHANGE, plc.recipe_change_sequence, plc))
        if self._last_command_sequence != plc.command_sequence:
            self._last_command_sequence = plc.command_sequence
            if plc.command_code != PlcCommand.NONE:
                events.append(ProtocolEvent(ProtocolEventType.COMMAND, plc.command_sequence, plc))
        if self._last_result_ack_sequence != plc.result_ack_sequence:
            self._last_result_ack_sequence = plc.result_ack_sequence
            events.append(ProtocolEvent(ProtocolEventType.RESULT_ACK, plc.result_ack_sequence, plc))
        return events