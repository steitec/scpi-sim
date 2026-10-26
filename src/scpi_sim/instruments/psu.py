"""A generic multi-channel DC power supply with constant-voltage / constant-current behaviour."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import ScpiError
from ..instrument import Call, Instrument, scpi
from ..loads import Load, Resistor, operating_point
from ..scpi.params import parse_bool, parse_number

DEFAULT_VOLTS = 0.0
DEFAULT_AMPS = 0.1


@dataclass
class _Channel:
    volts: float = DEFAULT_VOLTS
    amps: float = DEFAULT_AMPS
    output: bool = False


class PowerSupply(Instrument):
    model = "PSU"
    serial = "SIM00002"

    def __init__(self, *, channels: int = 1, load: Load | None = None, load_ohms: float | None = None,
                 max_volts: float = 30.0, max_amps: float = 3.0):
        super().__init__()
        if channels < 1:
            raise ValueError("channels must be at least 1")
        if load is not None and load_ohms is not None:
            raise ValueError("give either load or load_ohms, not both")
        if load_ohms is not None:
            if load_ohms < 0:
                raise ValueError("load must not be negative")
            load = Resistor(load_ohms)
        self.channel_count = channels
        self.loads: list[Load | None] = [load] * channels  # what each output drives
        self.max_volts = max_volts
        self.max_amps = max_amps
        self.reset()

    def reset(self) -> None:
        super().reset()
        self._channels = [_Channel() for _ in range(self.channel_count)]
        self._selected = 1

    # --- helpers --------------------------------------------------------------

    def _number(self, suffix: int | None = None) -> int:
        number = self._selected if suffix is None else suffix
        if not 1 <= number <= self.channel_count:
            raise ScpiError(-114)
        return number

    def _channel(self, suffix: int | None = None) -> _Channel:
        return self._channels[self._number(suffix) - 1]

    def output(self, number: int) -> tuple[float, float, str]:
        """Actual (volts, amps, mode) at output ``number`` (1-based).

        Also called by a bench from other threads: ``_channels`` is only ever
        replaced (``*RST``), so one list is read consistently.
        """
        channel = self._channels[number - 1]
        if not channel.output:
            return 0.0, 0.0, "OFF"
        return operating_point(self.loads[number - 1], channel.volts, channel.amps)

    def _limit_query(self, call: Call, setting: float, maximum: float, default: float) -> float:
        arg = call.optional_arg(0)
        if arg is None:
            return setting
        arg = arg.upper()
        if arg in ("MIN", "MINIMUM"):
            return 0.0
        if arg in ("MAX", "MAXIMUM"):
            return maximum
        if arg in ("DEF", "DEFAULT"):
            return default
        raise ScpiError(-224)

    # --- channel selection ----------------------------------------------------

    @scpi("INSTrument:NSELect")
    def inst_nsel(self, call: Call) -> None:
        self._selected = round(parse_number(call.arg(0), minimum=1, maximum=self.channel_count, default=1))

    @scpi("INSTrument:NSELect?")
    def inst_nsel_query(self, call: Call) -> int:
        return self._selected

    @scpi("INSTrument[:SELect]")
    def inst_select(self, call: Call) -> None:
        m = re.fullmatch(r"CH(\d+)", call.arg(0).upper())
        if not m:
            raise ScpiError(-224)
        number = int(m.group(1))
        if not 1 <= number <= self.channel_count:
            raise ScpiError(-222)
        self._selected = number

    @scpi("INSTrument[:SELect]?")
    def inst_select_query(self, call: Call) -> str:
        return f"CH{self._selected}"

    # --- settings ---------------------------------------------------------------

    # SOURce<n> addresses channel n directly, without it the selected channel is used.

    @scpi("[SOURce#:]VOLTage[:LEVel][:IMMediate][:AMPLitude]")
    def volt(self, call: Call) -> None:
        self._channel(call.suffixes[0]).volts = parse_number(
            call.arg(0), "V", minimum=0.0, maximum=self.max_volts, default=DEFAULT_VOLTS) + 0.0  # no -0

    @scpi("[SOURce#:]VOLTage[:LEVel][:IMMediate][:AMPLitude]?", max_args=1)
    def volt_query(self, call: Call) -> float:
        return self._limit_query(call, self._channel(call.suffixes[0]).volts, self.max_volts, DEFAULT_VOLTS)

    @scpi("[SOURce#:]CURRent[:LEVel][:IMMediate][:AMPLitude]")
    def curr(self, call: Call) -> None:
        self._channel(call.suffixes[0]).amps = parse_number(
            call.arg(0), "A", minimum=0.0, maximum=self.max_amps, default=DEFAULT_AMPS) + 0.0

    @scpi("[SOURce#:]CURRent[:LEVel][:IMMediate][:AMPLitude]?", max_args=1)
    def curr_query(self, call: Call) -> float:
        return self._limit_query(call, self._channel(call.suffixes[0]).amps, self.max_amps, DEFAULT_AMPS)

    @scpi("OUTPut#[:STATe]")
    def outp(self, call: Call) -> None:
        self._channel(call.suffixes[0]).output = parse_bool(call.arg(0))

    @scpi("OUTPut#[:STATe]?")
    def outp_query(self, call: Call) -> bool:
        return self._channel(call.suffixes[0]).output

    @scpi("OUTPut#:MODE?")
    def outp_mode(self, call: Call) -> str:
        return self.output(self._number(call.suffixes[0]))[2]

    # --- measurements -----------------------------------------------------------

    @scpi("MEASure:VOLTage[:DC]?")
    def meas_volt(self, call: Call) -> float:
        return self.output(self._number())[0]

    @scpi("MEASure:CURRent[:DC]?")
    def meas_curr(self, call: Call) -> float:
        return self.output(self._number())[1]
