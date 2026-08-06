"""Pure Modbus word and scaling conversion helpers."""

from __future__ import annotations

from collections.abc import Iterable


UINT16_MAX = 0xFFFF
UINT32_MAX = 0xFFFFFFFF


def saturate_uint16(value: int | float) -> int:
    """Clamp a numeric value to the Modbus unsigned 16-bit range."""
    return max(0, min(UINT16_MAX, int(round(value))))


def uint32_to_words(value: int) -> tuple[int, int]:
    """Pack an unsigned 32-bit value in low-word-first order."""
    value = max(0, min(UINT32_MAX, int(value)))
    return value & UINT16_MAX, (value >> 16) & UINT16_MAX


def words_to_uint32(low_word: int, high_word: int) -> int:
    """Unpack a low-word-first unsigned 32-bit Modbus value."""
    return (saturate_uint16(low_word) | (saturate_uint16(high_word) << 16))


def encode_bits(enabled_bits: Iterable[int]) -> int:
    """Encode bit numbers into one uint16 bit field."""
    value = 0
    for bit in enabled_bits:
        if not 0 <= bit < 16:
            raise ValueError(f"Bit {bit} is outside uint16 range")
        value |= 1 << bit
    return value


def decode_bits(value: int) -> frozenset[int]:
    """Decode a uint16 bit field into its set bit numbers."""
    word = saturate_uint16(value)
    return frozenset(bit for bit in range(16) if word & (1 << bit))


def score_to_scaled_uint16(score: float, scale: int = 10_000) -> int:
    """Convert a non-negative score to a saturated scaled register value."""
    if scale <= 0:
        raise ValueError("score scale must be positive")
    return saturate_uint16(max(0.0, float(score)) * scale)


def scaled_uint16_to_score(value: int, scale: int = 10_000) -> float:
    """Convert a scaled uint16 register value to a floating-point score."""
    if scale <= 0:
        raise ValueError("score scale must be positive")
    return saturate_uint16(value) / float(scale)