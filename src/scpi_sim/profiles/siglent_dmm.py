"""Siglent SDM3065X digital multimeter.

Source: Siglent SDM3065X Remote Manual RC06036-E01A, chapter 2 (SCPI subsystems).

Simulated: see ``dmm_common``, with the SDM3065X's ranges (DC V 200 mV-1000 V,
DC I 200 uA-10 A, 2-wire resistance 200 ohm-100 Mohm) and ``CONF?`` answering
function and range only (``"VOLT +2.00000000E-01"``, as in the manual).

Not simulated: AC, 4-wire resistance, frequency/period, temperature,
capacitance, continuity, diode, the scanner card, sample count above 1, NPLC,
math. ``RANG? DEF`` is not described in the manual; it answers 1000 V, 2 A and
2 kohm. The manual has no ``*IDN?`` example: the string follows Siglent's
usual format with an invented serial number.
"""

from __future__ import annotations

from ..instrument import Call, scpi
from .dmm_common import Scpi34401Style


class SiglentSdm3065x(Scpi34401Style):
    profile = "siglent-sdm3065x"
    description = "Siglent SDM3065X, 6.5-digit multimeter"
    manufacturer = "Siglent Technologies"
    model = "SDM3065X"
    serial = "SDM36FAD000001"
    firmware = "1.01.01.25"
    RANGES = {
        "VOLT": ("V", (0.2, 2.0, 20.0, 200.0, 1000.0)),
        "CURR": ("A", (2e-4, 2e-3, 2e-2, 0.2, 2.0, 10.0)),
        "RES": ("OHM", (200.0, 2e3, 2e4, 2e5, 1e6, 1e7, 1e8)),
    }
    DEFAULT_RANGES = {"VOLT": 1000.0, "CURR": 2.0, "RES": 2e3}

    @scpi("CONFigure?")
    def conf_query(self, call: Call) -> str:
        return f'"{self._function} {self.format_number(self._active_range(self._function))}"'
