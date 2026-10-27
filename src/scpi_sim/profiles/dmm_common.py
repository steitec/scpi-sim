"""The 34401A-style multimeter dialect several models share (Keysight Truevolt, R&S HMC8012, Siglent SDM).

On top of the generic ``Multimeter`` (``MEAS``, ``CONF``, ``READ?``): ``CONF?`` with
range and resolution, ``INIT``/``FETC?`` (one reading: sample and trigger count 1),
``FUNC``/``FUNC?`` and ``[SENS:]<function>:RANG[:AUTO]`` per function. Only DC
volts, DC amps and 2-wire resistance are simulated.
"""

from __future__ import annotations

from typing import ClassVar

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.dmm import Multimeter
from ..options import real, whole
from ..scpi.params import parse_bool, parse_number

_FUNCTION_NAMES = {"VOLT": "VOLT", "VOLT:DC": "VOLT", "VOLTAGE": "VOLT", "VOLTAGE:DC": "VOLT",
                   "CURR": "CURR", "CURR:DC": "CURR", "CURRENT": "CURR", "CURRENT:DC": "CURR",
                   "RES": "RES", "RESISTANCE": "RES"}


class Scpi34401Style(Multimeter):
    kind: ClassVar[str] = "dmm"
    profile: ClassVar[str]
    description: ClassVar[str]
    options: ClassVar[dict] = {"volts": real(), "amps": real(), "ohms": real(low=0), "noise": real(low=0),
                               "seed": whole(0)}
    RESOLUTION: ClassVar[float] = 3e-7  # default resolution, relative to the range
    # best (MIN) and worst (MAX) resolution, relative to the range; None: as the default
    RESOLUTION_MIN: ClassVar[float | None] = None
    RESOLUTION_MAX: ClassVar[float | None] = None
    QUOTED_FUNCTION: ClassVar[bool] = True  # FUNC? answers "VOLT" (with quotes) or VOLT
    DEFAULT_RANGES: ClassVar[dict[str, float]]  # what RANG? DEF answers

    def reset(self) -> None:
        super().reset()
        self._ranges: dict[str, float | None] = dict.fromkeys(self.RANGES)  # None = autorange
        self._resolutions: dict[str, float | None] = dict.fromkeys(self.RANGES)  # None: relative
        self._relative: dict[str, float] = dict.fromkeys(self.RANGES, self.RESOLUTION)
        self._readings: list[float] | None = None

    def _configure(self, function: str, arg: str | None, resolution: str | None = None) -> None:
        super()._configure(function, arg)
        self._ranges[function] = self._range
        self._resolutions[function] = None
        self._relative[function] = self.RESOLUTION
        keyword = (resolution or "").strip().upper()
        if keyword in ("MIN", "MINIMUM"):
            self._relative[function] = self.RESOLUTION_MIN or self.RESOLUTION
        elif keyword in ("MAX", "MAXIMUM"):
            self._relative[function] = self.RESOLUTION_MAX or self.RESOLUTION
        elif keyword not in ("", "DEF", "DEFAULT"):
            self._resolutions[function] = parse_number(resolution, self.RANGES[function][0], minimum=0.0,
                                                       maximum=self.RANGES[function][1][-1], default=0.0)
        self._readings = None

    def _select(self, function: str) -> None:
        self._function, self._range = function, self._ranges[function]

    def _active_range(self, function: str) -> float:
        fixed = self._ranges[function]
        if fixed is not None:
            return fixed
        value = abs(self._input(function))
        ranges = self.RANGES[function][1]
        return next((r for r in ranges if value <= 1.2 * r), ranges[-1])

    def _measure(self) -> float:
        reading = self._read()
        self._readings = [reading]
        return reading

    # --- MEASure / CONFigure with resolution --------------------------------------

    @scpi("MEASure:VOLTage[:DC]?", max_args=2)
    def meas_volt(self, call: Call) -> float:
        self._configure("VOLT", call.optional_arg(0), call.optional_arg(1))
        return self._measure()

    @scpi("MEASure:CURRent[:DC]?", max_args=2)
    def meas_curr(self, call: Call) -> float:
        self._configure("CURR", call.optional_arg(0), call.optional_arg(1))
        return self._measure()

    @scpi("MEASure:RESistance?", max_args=2)
    def meas_res(self, call: Call) -> float:
        self._configure("RES", call.optional_arg(0), call.optional_arg(1))
        return self._measure()

    @scpi("CONFigure:VOLTage[:DC]", max_args=2)
    def conf_volt(self, call: Call) -> None:
        self._configure("VOLT", call.optional_arg(0), call.optional_arg(1))

    @scpi("CONFigure:CURRent[:DC]", max_args=2)
    def conf_curr(self, call: Call) -> None:
        self._configure("CURR", call.optional_arg(0), call.optional_arg(1))

    @scpi("CONFigure:RESistance", max_args=2)
    def conf_res(self, call: Call) -> None:
        self._configure("RES", call.optional_arg(0), call.optional_arg(1))

    @scpi("CONFigure?")
    def conf_query(self, call: Call) -> str:
        function = self._function
        rng = self._active_range(function)
        resolution = self._resolutions[function]
        if resolution is None:
            resolution = self._relative[function] * rng
        return f'"{function} {self.format_number(rng)},{self.format_number(resolution)}"'

    # --- triggering -----------------------------------------------------------------

    @scpi("READ?")
    def read(self, call: Call) -> float:
        return self._measure()

    @scpi("INITiate[:IMMediate]", max_args=0)
    def initiate(self, call: Call) -> None:
        self._measure()

    @scpi("FETCh?")
    def fetch(self, call: Call) -> list[float]:
        if self._readings is None:
            raise ScpiError(-230)  # data corrupt or stale: nothing measured since the configuration changed
        return self._readings

    # --- function and ranges ----------------------------------------------------------

    @scpi("[SENSe:]FUNCtion[:ON]")
    def function(self, call: Call) -> None:
        name = call.arg(0).strip().strip("\"'").upper()
        if name not in _FUNCTION_NAMES:
            raise ScpiError(-224)
        self._select(_FUNCTION_NAMES[name])
        self._readings = None

    @scpi("[SENSe:]FUNCtion[:ON]?")
    def function_query(self, call: Call) -> str:
        return f'"{self._function}"' if self.QUOTED_FUNCTION else self._function

    def _set_range(self, function: str, arg: str) -> None:
        ranges = self.RANGES[function][1]
        keyword = arg.strip().upper()
        if keyword in ("MIN", "MINIMUM"):
            selected = ranges[0]
        elif keyword in ("MAX", "MAXIMUM"):
            selected = ranges[-1]
        elif keyword in ("DEF", "DEFAULT"):
            selected = self.DEFAULT_RANGES[function]
        else:
            value = parse_number(arg, self.RANGES[function][0], minimum=-ranges[-1], maximum=ranges[-1], default=0.0)
            selected = next(r for r in ranges if r >= abs(value))
        self._ranges[function] = selected
        if self._function == function:
            self._range = selected
        self._readings = None

    def _range_query(self, function: str, arg: str | None) -> float:
        ranges = self.RANGES[function][1]
        keyword = (arg or "").strip().upper()
        if keyword in ("MIN", "MINIMUM"):
            return ranges[0]
        if keyword in ("MAX", "MAXIMUM"):
            return ranges[-1]
        if keyword in ("DEF", "DEFAULT"):
            return self.DEFAULT_RANGES[function]
        if keyword:
            raise ScpiError(-224)
        return self._active_range(function)

    def _set_auto(self, function: str, arg: str) -> None:
        keyword = arg.strip().upper()
        if keyword == "ONCE":
            self._ranges[function] = self._active_range(function)
        elif parse_bool(arg):
            self._ranges[function] = None
        elif self._ranges[function] is None:
            self._ranges[function] = self._active_range(function)  # fixed at the present range
        if self._function == function:
            self._range = self._ranges[function]

    @scpi("[SENSe:]VOLTage[:DC]:RANGe[:UPPer]")
    def volt_range(self, call: Call) -> None:
        self._set_range("VOLT", call.arg(0))

    @scpi("[SENSe:]VOLTage[:DC]:RANGe[:UPPer]?", max_args=1)
    def volt_range_query(self, call: Call) -> float:
        return self._range_query("VOLT", call.optional_arg(0))

    @scpi("[SENSe:]VOLTage[:DC]:RANGe:AUTO")
    def volt_auto(self, call: Call) -> None:
        self._set_auto("VOLT", call.arg(0))

    @scpi("[SENSe:]VOLTage[:DC]:RANGe:AUTO?")
    def volt_auto_query(self, call: Call) -> bool:
        return self._ranges["VOLT"] is None

    @scpi("[SENSe:]CURRent[:DC]:RANGe[:UPPer]")
    def curr_range(self, call: Call) -> None:
        self._set_range("CURR", call.arg(0))

    @scpi("[SENSe:]CURRent[:DC]:RANGe[:UPPer]?", max_args=1)
    def curr_range_query(self, call: Call) -> float:
        return self._range_query("CURR", call.optional_arg(0))

    @scpi("[SENSe:]CURRent[:DC]:RANGe:AUTO")
    def curr_auto(self, call: Call) -> None:
        self._set_auto("CURR", call.arg(0))

    @scpi("[SENSe:]CURRent[:DC]:RANGe:AUTO?")
    def curr_auto_query(self, call: Call) -> bool:
        return self._ranges["CURR"] is None

    @scpi("[SENSe:]RESistance:RANGe[:UPPer]")
    def res_range(self, call: Call) -> None:
        self._set_range("RES", call.arg(0))

    @scpi("[SENSe:]RESistance:RANGe[:UPPer]?", max_args=1)
    def res_range_query(self, call: Call) -> float:
        return self._range_query("RES", call.optional_arg(0))

    @scpi("[SENSe:]RESistance:RANGe:AUTO")
    def res_auto(self, call: Call) -> None:
        self._set_auto("RES", call.arg(0))

    @scpi("[SENSe:]RESistance:RANGe:AUTO?")
    def res_auto_query(self, call: Call) -> bool:
        return self._ranges["RES"] is None
