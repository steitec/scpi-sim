"""A generic digital multimeter: DC volts, DC amps, 2-wire resistance."""

from __future__ import annotations

import math
import random
from typing import ClassVar, Protocol

from ..instrument import Call, Instrument, scpi
from ..scpi.params import format_number, parse_number

OVERLOAD = 9.9e37

# function -> (unit suffix, available ranges); an instrument class may replace them (RANGES)
_FUNCTIONS = {
    "VOLT": ("V", (0.1, 1.0, 10.0, 100.0, 1000.0)),
    "CURR": ("A", (0.01, 0.1, 1.0, 3.0, 10.0)),
    "RES": ("OHM", (100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)),
}


class InputSource(Protocol):
    """What is connected to the input terminals (provided by a bench)."""

    def volts(self) -> float: ...

    def ohms(self) -> float: ...


class Multimeter(Instrument):
    model = "DMM"
    serial = "SIM00001"
    SPECIFIC_FAULTS = frozenset({"offset", "noise"})
    RANGES: ClassVar[dict[str, tuple[str, tuple[float, ...]]]] = _FUNCTIONS

    def __init__(self, *, volts: float = 5.0, amps: float = 0.1, ohms: float = 1000.0,
                 noise: float = 50e-6, seed: int | None = None, source: InputSource | None = None,
                 serial: str | None = None):
        super().__init__(serial=serial)
        self.source = source
        self.volts = volts
        self.amps = amps
        self.ohms = ohms
        self.noise = noise
        self._random = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        super().reset()
        self._function = "VOLT"
        self._range: float | None = None  # None = auto range

    def _configure(self, function: str, arg: str | None) -> None:
        unit, ranges = self.RANGES[function]
        keyword = arg.strip().upper() if arg is not None else "AUTO"
        if keyword in ("AUTO", "DEF", "DEFAULT"):
            selected = None
        elif keyword in ("MIN", "MINIMUM"):
            selected = ranges[0]
        elif keyword in ("MAX", "MAXIMUM"):
            selected = ranges[-1]
        else:
            value = parse_number(arg, unit, minimum=-ranges[-1], maximum=ranges[-1], default=0.0)
            selected = next(r for r in ranges if r >= abs(value))
        self._function, self._range = function, selected

    def _input(self, function: str) -> float:
        """What is at the input for ``function``, before noise and range."""
        if self.source is not None and function != "CURR":
            value = self.source.volts() if function == "VOLT" else self.source.ohms()
        else:
            value = {"VOLT": self.volts, "CURR": self.amps, "RES": self.ohms}[function]
        return float(value)

    def _read(self) -> float:
        value = self._input(self._function)
        noise = self._faults["noise"]["sigma"] if "noise" in self._faults else self.noise
        reading = value * (1 + self._random.gauss(0, noise)) if noise else value
        limit = self._range if self._range is not None else self.RANGES[self._function][1][-1]
        if abs(reading) > 1.2 * limit:
            return math.copysign(OVERLOAD, reading)
        if "offset" in self._faults:
            reading += self._faults["offset"]["value"]
        return reading

    @scpi("MEASure:VOLTage[:DC]?", max_args=2)  # [range[,resolution]]
    def meas_volt(self, call: Call) -> float:
        self._configure("VOLT", call.optional_arg(0))
        return self._read()

    @scpi("MEASure:CURRent[:DC]?", max_args=2)
    def meas_curr(self, call: Call) -> float:
        self._configure("CURR", call.optional_arg(0))
        return self._read()

    @scpi("MEASure:RESistance?", max_args=2)
    def meas_res(self, call: Call) -> float:
        self._configure("RES", call.optional_arg(0))
        return self._read()

    @scpi("CONFigure:VOLTage[:DC]", max_args=2)
    def conf_volt(self, call: Call) -> None:
        self._configure("VOLT", call.optional_arg(0))

    @scpi("CONFigure:CURRent[:DC]", max_args=2)
    def conf_curr(self, call: Call) -> None:
        self._configure("CURR", call.optional_arg(0))

    @scpi("CONFigure:RESistance", max_args=2)
    def conf_res(self, call: Call) -> None:
        self._configure("RES", call.optional_arg(0))

    @scpi("CONFigure?")
    def conf_query(self, call: Call) -> str:
        rng = "AUTO" if self._range is None else format_number(self._range)
        return f'"{self._function} {rng}"'

    @scpi("READ?")
    def read(self, call: Call) -> float:
        return self._read()
