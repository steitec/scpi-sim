"""Loads on a power supply output and the operating point they produce.

A load is anything with ``current(volts) -> amps``: the current it draws at a
given terminal voltage (non-decreasing in volts). A load that can also invert
that relation exactly provides ``voltage(amps) -> volts | None``; otherwise the
current-limit point is found by bisection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

_BISECTION_STEPS = 60


class Load(Protocol):
    def current(self, volts: float) -> float: ...


@dataclass(frozen=True)
class Resistor:
    ohms: float

    def __post_init__(self) -> None:
        if not self.ohms >= 0:  # also rejects nan
            raise ValueError(f"resistance must be a number >= 0: {self.ohms}")

    def current(self, volts: float) -> float:
        if self.ohms == 0:  # short circuit
            return math.inf if volts > 0 else 0.0
        return volts / self.ohms

    def voltage(self, amps: float) -> float:
        return amps * self.ohms


@dataclass(frozen=True)
class Parallel:
    loads: tuple[Load, ...]

    def current(self, volts: float) -> float:
        return sum((load.current(volts) for load in self.loads), 0.0)

    def voltage(self, amps: float) -> float | None:
        """Exact only for resistors; None means: use bisection."""
        if not all(isinstance(load, Resistor) for load in self.loads):
            return None
        if any(load.ohms == 0 for load in self.loads):
            return 0.0
        return amps / sum(1 / load.ohms for load in self.loads)


def operating_point(load: Load | None, set_volts: float, limit_amps: float) -> tuple[float, float, str]:
    """(volts, amps, "CV" or "CC") of a supply set to set_volts / limit_amps driving load."""
    if load is None:
        return set_volts, 0.0, "CV"
    amps = load.current(set_volts)
    if amps <= limit_amps:
        return set_volts, amps, "CV"
    inverse = getattr(load, "voltage", None)
    volts = inverse(limit_amps) if inverse is not None else None
    if volts is None:
        low, high = 0.0, set_volts
        for _ in range(_BISECTION_STEPS):
            middle = (low + high) / 2
            if load.current(middle) <= limit_amps:
                low = middle
            else:
                high = middle
        volts = low
    return volts + 0.0, limit_amps, "CC"
