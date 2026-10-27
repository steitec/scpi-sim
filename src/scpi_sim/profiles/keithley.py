"""Keithley DMM6500 multimeter and DAQ6510 data acquisition system (SCPI command set).

Sources: Keithley DMM6500 Reference Manual DMM6500-901-01 Rev. A (April 2018) and
Keithley DAQ6510 Reference Manual DAQ6510-901-01 Rev. C (June 2024), the
"SCPI command reference" sections.

Simulated (both): ``:SENS:FUNC "VOLT"`` (also single quotes) and ``:SENS:FUNC?``
(``VOLT:DC``, ``CURR:DC``, ``RES``), ``:MEAS[:<function>]?`` and ``:READ?`` (with
the default buffers ``"defbuffer1"``/``"defbuffer2"`` only, no buffer elements but
``READ``), ``:INIT``, ``:FETC?``, ``:TRAC:DATA? <start>,<end>``, ``:TRAC:ACT?`` and
``:SENS:<function>:RANG[:UPP]``/``:RANG:AUTO`` with the models' ranges (DC V
100 mV-1000 V, DC I 10 uA-10 A, 2-wire resistance 10 ohm-100 Mohm). Readings use
the manual's format ``-5.693831E-05``.

DAQ6510 with 7700 cards (slots 1-2, channels s01-s20; see ``daq_common``):
``:SENS:FUNC '<function>', (@<list>)`` assigns a function to channels;
``:ROUT:CLOS`` closes the listed channels and, for a channel with a function,
opens all others first (then only one channel may be listed); ``:ROUT:CLOS?``,
``:ROUT:OPEN (@<list>|ALLSLOTS|SLOT1|SLOT2)``; ``:READ?`` measures the closed
channel with its function; ``:ROUT:SCAN[:CRE]`` scans in the listed order on
``:INIT``, readings via ``:TRAC:DATA?``.

Not simulated: TSP, 34401A/2000 emulation, user buffers and buffer elements
other than READ, AC/4-wire/temperature/digitize and the other functions,
configuration lists, triggering models, NPLC/aperture, the current channels
s21/s22 of the 7700, ``:CONF?``-style queries. ``RANG? DEF`` is not documented
per function: it answers the largest range. Serial numbers are invented, the
firmware field is the manual's example.
"""

from __future__ import annotations

import re

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.mux import Multiplexer
from ..options import real, whole
from ..scpi.params import parse_channel_list
from .daq_common import DaqMultiplexer
from .dmm_common import _FUNCTION_NAMES, Scpi34401Style

_RANGES = {
    "VOLT": ("V", (0.1, 1.0, 10.0, 100.0, 1000.0)),
    "CURR": ("A", (1e-5, 1e-4, 1e-3, 1e-2, 0.1, 1.0, 3.0, 10.0)),
    "RES": ("OHM", (10.0, 100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)),
}
_LONG_NAMES = {"VOLT": "VOLT:DC", "CURR": "CURR:DC", "RES": "RES"}
_DEFAULT_BUFFERS = {"DEFBUFFER1", "DEFBUFFER2"}
_CHANNEL_LIST = re.compile(r"\(\s*@[^)]*\)")


def _check_buffer(args: tuple[str, ...], elements_from: int = 1) -> None:
    """Only the default buffers and the READing element are simulated."""
    if args and args[0].strip().strip("\"'").upper() not in _DEFAULT_BUFFERS:
        raise ScpiError(-224, "Illegal parameter value;user buffers are not simulated")
    for element in args[elements_from:]:
        if element.strip().upper() not in ("READ", "READING"):
            raise ScpiError(-224, "Illegal parameter value;only the READing element is simulated")


def _function_name(arg: str) -> str:
    name = arg.strip().strip("\"'").upper()
    if name not in _FUNCTION_NAMES:
        raise ScpiError(-224)
    return _FUNCTION_NAMES[name]


class _KeithleyReadings:
    """``:TRAC:DATA?``/``:TRAC:ACT?``/``:FETC?`` over the readings of the last measurement."""

    _readings: list[float] | None

    def format_number(self, value: float) -> str:
        return f"{value:.6E}"

    @scpi("TRACe:DATA?", max_args=4)
    def trace_data(self, call: Call) -> list[float]:
        _check_buffer(call.args[2:], elements_from=1)
        try:
            start, end = int(call.arg(0)), int(call.arg(1))
        except ValueError:
            raise ScpiError(-104) from None
        readings = self._readings or []
        if not 1 <= start <= end <= len(readings):
            raise ScpiError(-222)
        return readings[start - 1:end]

    @scpi("TRACe:ACTual?", max_args=1)
    def trace_actual(self, call: Call) -> int:
        _check_buffer(call.args)
        return len(self._readings or [])

    @scpi("FETCh?", max_args=2)
    def fetch(self, call: Call) -> float:
        _check_buffer(call.args)
        if not self._readings:
            raise ScpiError(-230)
        return self._readings[-1]


class KeithleyDmm6500(_KeithleyReadings, Scpi34401Style):
    profile = "keithley-dmm6500"
    description = "Keithley DMM6500, 6.5-digit multimeter (SCPI command set)"
    manufacturer = "KEITHLEY INSTRUMENTS"
    model = "MODEL DMM6500"
    serial = "04000001"
    firmware = "1.6.3d"
    RANGES = _RANGES
    DEFAULT_RANGES = {function: ranges[-1] for function, (_, ranges) in _RANGES.items()}

    def _measure_function(self, function: str | None, call: Call) -> float:
        _check_buffer(call.args)
        if function is not None:
            self._select(function)  # like :SENS:FUNC, the range settings stay
        return self._measure()

    @scpi("MEASure[:VOLTage][:DC]?", max_args=2)
    def meas_volt(self, call: Call) -> float:
        return self._measure_function("VOLT", call)

    @scpi("MEASure:CURRent[:DC]?", max_args=2)
    def meas_curr(self, call: Call) -> float:
        return self._measure_function("CURR", call)

    @scpi("MEASure:RESistance?", max_args=2)
    def meas_res(self, call: Call) -> float:
        return self._measure_function("RES", call)

    @scpi("READ?", max_args=2)
    def read(self, call: Call) -> float:
        return self._measure_function(None, call)

    @scpi("[SENSe#:]FUNCtion[:ON]")
    def function(self, call: Call) -> None:
        self._select(_function_name(call.arg(0)))
        self._readings = None

    @scpi("[SENSe#:]FUNCtion[:ON]?")
    def function_query(self, call: Call) -> str:
        return _LONG_NAMES[self._function]

    @scpi("CONFigure?")
    def conf_query(self, call: Call) -> str:
        raise ScpiError(-113)


class KeithleyDaq6510(_KeithleyReadings, DaqMultiplexer):
    profile = "keithley-daq6510"
    description = "Keithley DAQ6510 with 7700 20-channel multiplexer cards (1-2)"
    options = {"cards": whole(1, 2), "noise": real(low=0), "seed": whole(0)}
    manufacturer = "KEITHLEY INSTRUMENTS"
    model = "MODEL DAQ6510"
    serial = "04000001"
    firmware = "1.6.3d"
    SLOTS = 2
    CHANNELS_PER_CARD = 20
    DMM_RANGES = _RANGES

    def reset(self) -> None:
        super().reset()
        self._function = "VOLT"  # the DMM's function when no channel with a function is closed

    def _list(self, arg: str) -> list[int]:
        return parse_channel_list(arg, valid=self.valid_channels)

    def _switch(self, closed: set[int]) -> None:
        with self.switch_lock:
            if self.before_close is not None:
                self.before_close(closed)
            self.closed = closed

    @scpi("[SENSe#:]FUNCtion[:ON]", max_args=2)
    def function(self, call: Call) -> None:
        function = _function_name(call.arg(0))
        if call.optional_arg(1) is None:
            self._function = function
            return
        for channel in self._list(call.arg(1)):
            self._config[channel] = (function, None)

    @scpi("[SENSe#:]FUNCtion[:ON]?")
    def function_query(self, call: Call) -> str:
        return _LONG_NAMES[self._function]

    @scpi("ROUTe[:CHANnel]:CLOSe")
    def close(self, call: Call) -> None:
        channels = self._list(call.arg(0))
        if any(channel in self._config for channel in channels):
            if len(channels) != 1:
                raise ScpiError(-221, "Settings conflict;only one channel with a function can be closed")
            self._switch(set(channels))  # opens the channels that would interfere
        else:
            self._switch(self.closed | set(channels))

    @scpi("ROUTe[:CHANnel]:CLOSe?", max_args=1)
    def close_query(self, call: Call) -> str | list[bool]:
        if call.optional_arg(0) is not None:
            return Multiplexer.close_query(self, call)
        return "(@" + ",".join(str(ch) for ch in sorted(self.closed)) + ")"

    @scpi("ROUTe[:CHANnel]:OPEN")
    def open(self, call: Call) -> None:
        arg = call.arg(0).strip().upper().replace(" ", "")
        slot = {"(@ALLSLOTS)": None, "(@SLOT1)": 1, "(@SLOT2)": 2}
        with self.switch_lock:
            if arg in slot:
                self.closed = set() if slot[arg] is None else {ch for ch in self.closed if ch // 100 != slot[arg]}
            else:
                self.closed = self.closed - set(self._list(call.arg(0)))

    close_state = open_query = None  # generic commands the manual does not have: -113

    @scpi("READ?", max_args=2)
    def read(self, call: Call) -> float:
        _check_buffer(call.args)
        configured = [ch for ch in self.closed if ch in self._config]
        function, range_arg = self._config[configured[0]] if configured else (self._function, None)
        closed = configured or sorted(self.closed)
        self.active_slot = closed[0] // 100 if closed else 1
        self.dmm._configure(function, range_arg)
        reading = self.dmm._read()
        self._readings = [reading]
        return reading

    @scpi("MEASure?", max_args=2)
    def meas(self, call: Call) -> float:
        return self.read(call)

    @scpi("ROUTe:SCAN[:CREate]", max_args=1)
    def route_scan(self, call: Call) -> None:
        arg = call.optional_arg(0)
        if arg is None or re.fullmatch(r"\(\s*@\s*\)", arg.strip()):
            self._scan = []
        else:
            self._scan = self._list(arg)  # scanned in the order listed
        self._readings = None

    @scpi("ROUTe:SCAN[:CREate]?")
    def route_scan_query(self, call: Call) -> str:
        return "(@" + ",".join(str(ch) for ch in self._scan) + ")"

    @scpi("INITiate[:IMMediate]", max_args=0)
    def initiate(self, call: Call) -> None:
        if self._scan:
            self._sweep(self._scan)
        else:
            self.read(Call((), ()))
