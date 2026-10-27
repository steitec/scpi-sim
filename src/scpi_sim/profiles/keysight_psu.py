"""Keysight E36312A triple-output power supply.

Source: Keysight E36300 Series Programmable DC Power Supplies Programming Guide,
part number E36311-90008, Edition 3 (October 2017).

Simulated: outputs addressed by channel list (``VOLT 5,(@1)``, ``VOLT? (@1:3)``,
``OUTP ON,(@1:3)``, ``MEAS:VOLT? (@2)``), by name (``P6V``/``P25V``/``N25V`` =
``CH1``/``CH2``/``CH3``) or through the selected output (``INST``, ``INST:NSEL``);
``APPL``/``APPL?`` (quoted ``"5.000000,1.000000"``); ``CURR``/``VOLT`` with
MIN/MAX/DEF. Limits from the programming-range table: P6V 0-6.18 V / 0.001-5.15 A,
P25V and N25V (a +25 V output on the E36312A) 0-25.75 V / 0.001-1.03 A; *RST
0 V and 5 A / 1 A / 1 A, outputs off.

Not simulated: E3631A persona mode, tracking, coupling, output delays and
sequencing, triggered levels, protection (OVP/OCP), datalogging, remote sense.
The *IDN? serial is invented and the revision code (format X.X.X-X.X.X-X.X) is a
plausible value.
"""

from __future__ import annotations

import re

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.psu import PowerSupply
from ..loads import Load
from ..scpi.params import parse_bool, parse_channel_list, parse_number

_CHANNEL_LIST = re.compile(r"\(\s*@[^)]*\)")
_NAMES = {"P6V": 1, "P25V": 2, "N25V": 3, "CH1": 1, "CH2": 2, "CH3": 3}
_NAME_OF = {1: "P6V", 2: "P25V", 3: "N25V"}


class KeysightE36312A(PowerSupply):
    profile = "keysight-e36312a"
    kind = "psu"
    description = "Keysight E36312A, 6 V / 5 A + 2 x 25 V / 1 A"
    options: dict = {}
    manufacturer = "Keysight Technologies"
    model = "E36312A"
    serial = "MY57000001"
    firmware = "2.1.0-1.0.4-1.12"
    RESET_VOLTS = 0.0
    MIN_AMPS = 0.001
    RESET_CURRENTS = (5.0, 1.0, 1.0)

    def __init__(self, *, load: Load | None = None, load_ohms: float | None = None, serial: str | None = None):
        super().__init__(load=load, load_ohms=load_ohms, limits=[(6.18, 5.15), (25.75, 1.03), (25.75, 1.03)],
                         serial=serial)

    def reset(self) -> None:
        super().reset()
        for channel, amps in zip(self._channels, self.RESET_CURRENTS):
            channel.amps = amps

    def _split(self, call: Call) -> tuple[list[str], list[int]]:
        """Parameters, and the outputs they address: a channel list, an output name, or the selected output."""
        text = ",".join(call.args)
        match = _CHANNEL_LIST.search(text)
        if match is not None:
            if text[match.end():].strip(" ,"):
                raise ScpiError(-108)
            params = [p.strip() for p in text[:match.start()].split(",") if p.strip()]
            return params, parse_channel_list(match.group(), valid=range(1, self.channel_count + 1))
        params = [p.strip() for p in text.split(",") if p.strip()]
        if params and params[-1].upper() in _NAMES:
            return params[:-1], [_NAMES[params[-1].upper()]]
        return params, [self._number()]

    def _set(self, call: Call, index: int, unit: str) -> None:
        params, outputs = self._split(call)
        if len(params) != 1:
            raise ScpiError(-109 if not params else -108)
        minimum = self.MIN_AMPS if index else 0.0
        defaults = self.RESET_CURRENTS if index else (self.RESET_VOLTS,) * self.channel_count
        values = [parse_number(params[0], unit, minimum=minimum, maximum=self.limits[n - 1][index],
                               default=defaults[n - 1]) + 0.0 for n in outputs]  # all checked before any change
        for n, value in zip(outputs, values):
            setattr(self._channels[n - 1], "amps" if index else "volts", value)

    def _get(self, call: Call, index: int) -> list[float]:
        params, outputs = self._split(call)
        if len(params) > 1:
            raise ScpiError(-108)
        minimum = self.MIN_AMPS if index else 0.0
        defaults = self.RESET_CURRENTS if index else (self.RESET_VOLTS,) * self.channel_count
        values = []
        for n in outputs:
            channel = self._channels[n - 1]
            setting = channel.amps if index else channel.volts
            call_n = Call(tuple(params), (None,))
            values.append(self._limit_query(call_n, setting, self.limits[n - 1][index], defaults[n - 1], minimum))
        return values

    # --- selection ------------------------------------------------------------------

    @scpi("INSTrument[:SELect]")
    def inst_select(self, call: Call) -> None:
        name = call.arg(0).strip().upper()
        if name not in _NAMES:
            raise ScpiError(-224)
        self._selected = _NAMES[name]

    @scpi("INSTrument[:SELect]?")
    def inst_select_query(self, call: Call) -> str:
        return _NAME_OF[self._selected]

    # --- settings -------------------------------------------------------------------

    @scpi("[SOURce:]VOLTage[:LEVel][:IMMediate][:AMPLitude]", max_args=4)
    def volt(self, call: Call) -> None:
        self._set(call, 0, "V")

    @scpi("[SOURce:]VOLTage[:LEVel][:IMMediate][:AMPLitude]?", max_args=4)
    def volt_query(self, call: Call) -> list[float]:
        return self._get(call, 0)

    @scpi("[SOURce:]CURRent[:LEVel][:IMMediate][:AMPLitude]", max_args=4)
    def curr(self, call: Call) -> None:
        self._set(call, 1, "A")

    @scpi("[SOURce:]CURRent[:LEVel][:IMMediate][:AMPLitude]?", max_args=4)
    def curr_query(self, call: Call) -> list[float]:
        return self._get(call, 1)

    @scpi("APPLy", max_args=3)
    def apply(self, call: Call) -> None:
        name = call.arg(0).strip().upper()
        if name not in _NAMES:
            raise ScpiError(-224)
        n = _NAMES[name]
        channel = self._channels[n - 1]
        max_volts, max_amps = self.limits[n - 1]
        volts, amps = channel.volts, channel.amps
        if (arg := call.optional_arg(1)) is not None:
            volts = parse_number(arg, "V", minimum=0.0, maximum=max_volts, default=self.RESET_VOLTS) + 0.0
        if (arg := call.optional_arg(2)) is not None:
            amps = parse_number(arg, "A", minimum=self.MIN_AMPS, maximum=max_amps,
                                default=self.RESET_CURRENTS[n - 1]) + 0.0
        self._selected = n
        channel.volts, channel.amps = volts, amps

    @scpi("APPLy?", max_args=1)
    def apply_query(self, call: Call) -> str:
        arg = call.optional_arg(0)
        if arg is not None and arg.strip().upper() not in _NAMES:
            raise ScpiError(-224)
        n = _NAMES[arg.strip().upper()] if arg is not None else self._selected
        channel = self._channels[n - 1]
        return f'"{channel.volts:.6f},{channel.amps:.6f}"'

    # --- outputs and measurements -----------------------------------------------------

    @scpi("OUTPut[:STATe]", max_args=4)
    def outp(self, call: Call) -> None:
        params, outputs = self._split(call)
        if len(params) != 1:
            raise ScpiError(-109 if not params else -108)
        on = parse_bool(params[0])
        for n in outputs:
            self._channels[n - 1].output = on

    @scpi("OUTPut[:STATe]?", max_args=3)
    def outp_query(self, call: Call) -> list[bool]:
        params, outputs = self._split(call)
        if params:
            raise ScpiError(-108)
        return [self._channels[n - 1].output for n in outputs]

    @scpi("MEASure[:SCALar]:VOLTage[:DC]?", max_args=3)
    def meas_volt(self, call: Call) -> list[float]:
        params, outputs = self._split(call)
        if params:
            raise ScpiError(-108)
        return [self.output(n)[0] for n in outputs]

    @scpi("MEASure[:SCALar]:CURRent[:DC]?", max_args=3)
    def meas_curr(self, call: Call) -> list[float]:
        params, outputs = self._split(call)
        if params:
            raise ScpiError(-108)
        return [self.output(n)[1] for n in outputs]
