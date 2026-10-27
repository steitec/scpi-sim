"""Rohde & Schwarz HMP series power supplies.

Source: R&S HMP Series Power Supplies User Manual 1178.6833.02 - 05 (firmware 2.7x),
chapter 8 "Remote control commands".

Simulated: channel selection (``INST OUTn``, ``INST:NSEL n``), ``VOLT``/``CURR``
with MIN/MAX/DEF, ``APPL``, ``OUTP:SEL`` + ``OUTP:GEN`` and ``OUTP``, ``MEAS:VOLT?``/
``MEAS:CURR?``, the common commands and ``SYST:ERR?``. Numbers are answered with
three decimals, as in the manual's examples (``10.000``).

Not simulated: ``VOLT``/``CURR UP|DOWN`` and ``:STEP``, ``FUSE``, ``VOLT:PROT``,
``ARB``, ``*SAV``/``*RCL``, ``SYST:BEEP|LOC|REM|RWL|MIX``, the power limit per
channel and in total (HMP4040: 160 W / 384 W). The *IDN? firmware field is a
plausible value; the manual only gives the format.
"""

from __future__ import annotations

import re
from typing import ClassVar

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.psu import PowerSupply
from ..loads import Load
from ..scpi.params import parse_bool, parse_number

_OUTPUT_NAME = re.compile(r"OUT(?:P(?:UT)?)?(\d+)")


class RsHmpFamily(PowerSupply):
    """The dialect R&S HMP (and NGE) supplies share; subclasses fix model and limits."""

    kind: ClassVar[str] = "psu"
    profile: ClassVar[str]
    description: ClassVar[str]
    options: ClassVar[dict] = {}
    manufacturer = "Rohde&Schwarz"
    RESET_VOLTS = 1.0  # APPLy *RST values in the manual
    RESET_AMPS = 1.0
    CHANNEL_LIMITS: ClassVar[tuple[tuple[float, float], ...]]

    def __init__(self, *, load: Load | None = None, load_ohms: float | None = None, serial: str | None = None):
        super().__init__(load=load, load_ohms=load_ohms, limits=self.CHANNEL_LIMITS, serial=serial)

    def reset(self) -> None:
        super().reset()
        self._active = [False] * self.channel_count  # OUTP:SEL per channel
        self._general = False                        # OUTP:GEN
        self._apply_outputs()

    def _apply_outputs(self) -> None:
        for channel, active in zip(self._channels, self._active):
            channel.output = active and self._general

    def format_number(self, value: float) -> str:
        return f"{value:.3f}"

    # --- channel selection ----------------------------------------------------

    @scpi("INSTrument[:SELect]")
    def inst_select(self, call: Call) -> None:
        m = _OUTPUT_NAME.fullmatch(call.arg(0).strip().upper())
        if not m:
            raise ScpiError(-224)
        number = int(m.group(1))
        if not 1 <= number <= self.channel_count:
            raise ScpiError(-222)
        self._selected = number

    @scpi("INSTrument[:SELect]?")
    def inst_select_query(self, call: Call) -> str:
        return f"OUT{self._selected}"

    # --- settings ---------------------------------------------------------------

    @scpi("APPLy", max_args=2)
    def apply(self, call: Call) -> None:
        number = self._number()
        max_volts, max_amps = self.limits[number - 1]
        volts = parse_number(call.arg(0), "V", minimum=0.0, maximum=max_volts, default=self.RESET_VOLTS)
        amps = parse_number(call.arg(1), "A", minimum=self.MIN_AMPS, maximum=max_amps, default=self.RESET_AMPS)
        channel = self._channels[number - 1]
        channel.volts, channel.amps = volts + 0.0, amps + 0.0

    @scpi("APPLy?")
    def apply_query(self, call: Call) -> list[float]:
        channel = self._channel()
        return [channel.volts, channel.amps]

    # --- outputs ----------------------------------------------------------------

    @scpi("OUTPut:SELect")
    def outp_select(self, call: Call) -> None:
        self._active[self._number() - 1] = parse_bool(call.arg(0))
        self._apply_outputs()

    @scpi("OUTPut:GENeral")
    def outp_general(self, call: Call) -> None:
        self._general = parse_bool(call.arg(0))
        self._apply_outputs()

    @scpi("OUTPut[:STATe]")
    def outp(self, call: Call) -> None:
        """Activates the selected channel and turns on the output (off: deactivates the channel)."""
        on = parse_bool(call.arg(0))
        self._active[self._number() - 1] = on
        if on:
            self._general = True
        self._apply_outputs()

    @scpi("OUTPut[:STATe]?")
    def outp_query(self, call: Call) -> bool:
        return self._channel().output

    # --- measurements -----------------------------------------------------------

    @scpi("MEASure[:SCALar]:VOLTage[:DC]?")
    def meas_volt(self, call: Call) -> float:
        return self.output(self._number())[0]

    @scpi("MEASure[:SCALar]:CURRent[:DC]?")
    def meas_curr(self, call: Call) -> float:
        return self.output(self._number())[1]


class RsHmp4040(RsHmpFamily):
    profile = "rs-hmp4040"
    description = "Rohde & Schwarz HMP4040, 4 x 32 V / 10 A"
    model = "HMP4040"
    serial = "3629.6776.04/100001"  # <part number>/<serial number>
    firmware = "2.70"
    CHANNEL_LIMITS = ((32.05, 10.01),) * 4
