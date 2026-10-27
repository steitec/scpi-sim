"""Rohde & Schwarz HMC8012 digital multimeter.

Source: R&S HMC8012 SCPI Programmer's Manual, version 01 (2016), chapter 2
"Remote Control Command Reference".

Simulated: see ``dmm_common``, with the HMC8012's ranges (DC V 400 mV-750 V,
DC I 20 mA-10 A, 2-wire resistance 400 ohm-250 Mohm; DEF selects the smallest
range, as the manual says), ``MEAS[:VOLT][:DC]?`` with a range, ``FUNC VOLT``
(also unquoted) and ``FUNC?`` answering ``VOLT`` without quotes, ``RANG[:UPP]``,
``FETC?`` returning the present reading (no error before a trigger). Numbers are
answered like the manual's overload value ``9.90000000E+37`` (no leading +).
``*IDN?`` uses the manual's manufacturer string ``HAMEG``.

Not simulated: AC, 4-wire resistance, frequency, temperature, continuity,
diode, capacitance, the trigger system (auto/single/manual), statistics,
math functions, data logging. ``CONF?`` is left out: the manual shows only a
temperature example, not the format for the DC functions.
"""

from __future__ import annotations

from ..errors import ScpiError
from ..instrument import Call, scpi
from .dmm_common import Scpi34401Style


class RsHmc8012(Scpi34401Style):
    profile = "rs-hmc8012"
    description = "Rohde & Schwarz HMC8012, 5.75-digit multimeter"
    manufacturer = "HAMEG"
    model = "HMC8012"
    serial = "100001"
    firmware = "01.000"
    QUOTED_FUNCTION = False
    RANGES = {
        "VOLT": ("V", (0.4, 4.0, 40.0, 400.0, 750.0)),
        "CURR": ("A", (0.02, 0.2, 2.0, 10.0)),
        "RES": ("OHM", (400.0, 4e3, 4e4, 4e5, 4e6, 4e7, 2.5e8)),
    }
    DEFAULT_RANGES = {"VOLT": 0.4, "CURR": 0.02, "RES": 400.0}

    def format_number(self, value: float) -> str:
        return f"{value:.8E}"

    @scpi("MEASure[:VOLTage][:DC]?", max_args=1)
    def meas_volt(self, call: Call) -> float:
        self._configure("VOLT", call.optional_arg(0))
        return self._measure()

    @scpi("FETCh?")
    def fetch(self, call: Call) -> float:
        return self._measure()

    @scpi("CONFigure?")
    def conf_query(self, call: Call) -> str:
        raise ScpiError(-113)
