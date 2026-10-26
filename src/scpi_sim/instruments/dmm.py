"""A generic digital multimeter: DC volts, DC amps, 2-wire resistance."""

from __future__ import annotations

import random

from ..instrument import Call, Instrument, scpi
from ..scpi.params import format_number, parse_number

OVERLOAD = 9.9e37

# function -> (unit suffix, available ranges)
_FUNCTIONS = {
    "VOLT": ("V", (0.1, 1.0, 10.0, 100.0, 1000.0)),
    "CURR": ("A", (0.01, 0.1, 1.0, 3.0, 10.0)),
    "RES": ("OHM", (100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)),
}


class Multimeter(Instrument):
    model = "DMM"
    serial = "SIM00001"

    def __init__(self, *, volts: float = 5.0, amps: float = 0.1, ohms: float = 1000.0,
                 noise: float = 50e-6, seed: int | None = None):
        super().__init__()
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
        unit, ranges = _FUNCTIONS[function]
        if arg is None or arg.strip().upper() in ("AUTO", "DEF", "DEFAULT"):
            selected = None
        else:
            value = parse_number(arg, unit, minimum=0.0, maximum=ranges[-1], default=0.0)
            selected = next(r for r in ranges if r >= value)
        self._function, self._range = function, selected

    def _read(self) -> float:
        value = float({"VOLT": self.volts, "CURR": self.amps, "RES": self.ohms}[self._function])
        reading = value * (1 + self._random.gauss(0, self.noise)) if self.noise else value
        limit = self._range if self._range is not None else _FUNCTIONS[self._function][1][-1]
        return OVERLOAD if abs(reading) > 1.2 * limit else reading

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
