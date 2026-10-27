"""Rohde & Schwarz NGE100B series power supplies.

Source: R&S NGE100B User Manual 5601.1343.02 - 09 (firmware 1.55), chapter 7
"Remote control commands". The dialect is the HMP one (see ``rs_hmp``) with these
differences, taken from the manual's examples:

- ``INST?`` answers ``OUTPut1`` (``OUTP:SEL?`` and ``OUTP:GEN?`` exist).
- Current limit 3 mA to 3.025 A, voltage up to 32.20 V.
- ``MEAS:CURR?`` answers ``1.000``, ``MEAS[:VOLT]?`` ``1.000E+00`` and
  ``MEAS:POW?`` ``3.00E+00`` -- the manual shows these different formats.

Not simulated besides the HMP list: ``OUTP:MODE`` as a setting, ``POW:PROT``,
``VOLT:RAMP``, ``DIO``, ``HCOP``, WLAN/Ethernet setup. The manual's examples are
not consistent everywhere (``INST? -> 2``, ``APPLY? -> 6, 2``, ``VOLT? MAX -> 32.20``);
where they disagree, the format of the dedicated example section is used.
"""

from __future__ import annotations

from ..instrument import Call, scpi
from .rs_hmp import RsHmpFamily


class RsNge103b(RsHmpFamily):
    profile = "rs-nge103b"
    description = "Rohde & Schwarz NGE103B, 3 x 32 V / 3 A"
    model = "NGE103B"
    serial = "5601.1414k03/100001"  # <part number>/<serial number>, as in the manual's example
    firmware = "1.55"
    CHANNEL_LIMITS = ((32.2, 3.025),) * 3
    MIN_AMPS = 0.003

    @scpi("INSTrument[:SELect]?")
    def inst_select_query(self, call: Call) -> str:
        return f"OUTPut{self._selected}"

    @scpi("OUTPut:SELect?")
    def outp_select_query(self, call: Call) -> bool:
        return self._active[self._number() - 1]

    @scpi("OUTPut:GENeral?")
    def outp_general_query(self, call: Call) -> bool:
        return self._general

    @scpi("MEASure[:SCALar][:VOLTage][:DC]?")
    def meas_volt(self, call: Call) -> str:
        return f"{self.output(self._number())[0]:.3E}"

    @scpi("MEASure[:SCALar]:POWer?")
    def meas_power(self, call: Call) -> str:
        volts, amps, _ = self.output(self._number())
        return f"{volts * amps:.2E}"
