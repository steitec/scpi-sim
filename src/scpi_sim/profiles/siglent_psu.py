"""Siglent SPD3303X triple-output power supply.

Source: Siglent SPD3303X/SPD3303X-E Quick Start, QS0503X-E01B, chapter 3 "Remote
control" (command list and descriptions).

Simulated: ``[CH1:|CH2:]VOLTage``/``CURRent`` and their queries, ``INSTrument
{CH1|CH2}``/``INSTrument?``, ``OUTPut {CH1|CH2|CH3},{ON|OFF}``, ``MEASure:CURRent?``/
``VOLTage?``/``POWEr? [{CH1|CH2}]``, ``SYSTem:STATus?`` (bits 0-5: CC mode of CH1/CH2,
independent mode, CH1/CH2 on) and ``SYSTem:VERSion?``. Numbers are answered with
three decimals as in the manual (``25.000``); ``*IDN?`` has the five fields of the
manual's typical return. CH1/CH2: 0-32 V, 0-3.2 A. CH3 is the fixed output (2.5,
3.3 or 5 V by a front-panel switch on the real unit): simulated at 5 V with a
3.2 A limit, only switched on and off.

Not simulated: tracking (series/parallel), timer, waveform display, ``*SAV``/
``*RCL``, network setup commands. ``SYSTem:ERRor?`` answers ``0 No Error`` for an
empty queue, as in the manual (the exact spacing is not recoverable from it);
errors keep the standard SCPI form (``-222,"Data out of range"``) because the
manual does not document Siglent's codes. Serial number and versions are plausible values.
"""

from __future__ import annotations

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.psu import PowerSupply
from ..loads import Load
from ..scpi.params import parse_bool

_CHANNEL_ARG = {"CH1": 1, "CH2": 2}
_OUTPUT_ARG = {"CH1": 1, "CH2": 2, "CH3": 3}
CH3_VOLTS = 5.0


class SiglentSpd3303x(PowerSupply):
    profile = "siglent-spd3303x"
    kind = "psu"
    description = "Siglent SPD3303X, 2 x 32 V / 3.2 A + fixed 5 V output"
    options: dict = {}
    manufacturer = "Siglent Technologies"
    model = "SPD3303X"
    serial = "SPD3XIDD4R0001"
    firmware = "1.01.01.02.05"
    hardware = "V3.0"

    def __init__(self, *, load: Load | None = None, load_ohms: float | None = None, serial: str | None = None):
        super().__init__(load=load, load_ohms=load_ohms, limits=[(32.0, 3.2), (32.0, 3.2), (CH3_VOLTS, 3.2)],
                         serial=serial)

    @property
    def idn(self) -> str:
        return f"{super().idn},{self.hardware}"

    def reset(self) -> None:
        super().reset()
        self._channels[2].volts, self._channels[2].amps = CH3_VOLTS, 3.2

    def format_number(self, value: float) -> str:
        return f"{value:.3f}"

    def _settable(self, suffix: int | None) -> int:
        number = self._number(suffix)
        if number == 3:
            raise ScpiError(-114)  # CH3 is set by the front-panel switch
        return number

    def _measured(self, call: Call) -> tuple[float, float, str]:
        arg = call.optional_arg(0)
        if arg is None:
            return self.output(self._selected)
        name = arg.strip().upper()
        if name not in _CHANNEL_ARG:
            raise ScpiError(-224)
        return self.output(_CHANNEL_ARG[name])

    @scpi("INSTrument[:SELect]")
    def inst_select(self, call: Call) -> None:
        name = call.arg(0).strip().upper()
        if name not in _CHANNEL_ARG:
            raise ScpiError(-224)
        self._selected = _CHANNEL_ARG[name]

    @scpi("INSTrument[:SELect]?")
    def inst_select_query(self, call: Call) -> str:
        return f"CH{self._selected}"

    @scpi("[CH#:]VOLTage")
    def volt(self, call: Call) -> None:
        self._settable(call.suffixes[0])
        super().volt(call)

    @scpi("[CH#:]VOLTage?")
    def volt_query(self, call: Call) -> float:
        self._settable(call.suffixes[0])
        return self._channels[self._number(call.suffixes[0]) - 1].volts

    @scpi("[CH#:]CURRent")
    def curr(self, call: Call) -> None:
        self._settable(call.suffixes[0])
        super().curr(call)

    @scpi("[CH#:]CURRent?")
    def curr_query(self, call: Call) -> float:
        self._settable(call.suffixes[0])
        return self._channels[self._number(call.suffixes[0]) - 1].amps

    @scpi("OUTPut", max_args=2)
    def outp(self, call: Call) -> None:
        name = call.arg(0).strip().upper()
        if name not in _OUTPUT_ARG:
            raise ScpiError(-224)
        self._channels[_OUTPUT_ARG[name] - 1].output = parse_bool(call.arg(1))

    @scpi("MEASure:VOLTage?", max_args=1)
    def meas_volt(self, call: Call) -> float:
        return self._measured(call)[0]

    @scpi("MEASure:CURRent?", max_args=1)
    def meas_curr(self, call: Call) -> float:
        return self._measured(call)[1]

    @scpi("MEASure:POWEr?", max_args=1)
    def meas_power(self, call: Call) -> float:
        volts, amps, _ = self._measured(call)
        return volts * amps

    @scpi("SYSTem:STATus?")
    def system_status(self, call: Call) -> str:
        bits = 0b100  # bits 2-3 = 01: independent mode
        for number, (cc_bit, on_bit) in ((1, (0, 4)), (2, (1, 5))):
            _, _, mode = self.output(number)
            if mode == "CC":
                bits |= 1 << cc_bit
            if self._channels[number - 1].output:
                bits |= 1 << on_bit
        return f"0x{bits:04X}"

    @scpi("SYSTem:VERSion?")
    def _syst_version(self, call: Call) -> str:
        return self.firmware

    @scpi("SYSTem:ERRor[:NEXT]?")
    def _syst_err(self, call: Call) -> str:
        return str(self._errors.pop(0)) if self._errors else "0 No Error"

    # generic commands the manual does not have: -113
    outp_mode = inst_nsel = inst_nsel_query = outp_query = None
