"""Keysight Truevolt 34461A digital multimeter.

Source: Keysight Truevolt Series Operating and Service Guide, part number 34460-90901,
Edition 7 (March 2020), chapter "SCPI Programming Reference".

Simulated: see ``dmm_common`` (34401A-style ``MEAS``/``CONF``/``READ?``/``INIT``/
``FETC?``, ``FUNC``, ``[SENS:]<function>:RANG[:AUTO]``) with the 34461A ranges:
DC volts 100 mV-1000 V, DC amps 100 uA-10 A, resistance 100 ohm-100 Mohm. ``CONF?``
answers like ``"VOLT +1.00000000E+01,+1.00000000E-05"``. Resolution, relative to the
range, from the Resolution Table (34461A column): default 1 ppm (1 PLC), ``MIN`` 0.3 ppm
(100 PLC), ``MAX`` 100 ppm (0.02 PLC). The ``CONF?`` example in the manual shows 0.3 ppm,
but the table gives 1 ppm as the 34461A default.

Not simulated: AC functions, 4-wire resistance, frequency, temperature, capacitance,
``SAMP:COUN``/``TRIG:COUN`` above 1, trigger sources, math, ``DATA``, ``SYST:IDEN``
(*IDN? personalities), NPLC/aperture. The *IDN? firmware field is a plausible value
in the documented format h.ff.ff-pp.pp-mm.mm-gg.gg-bb-fp.
"""

from __future__ import annotations

from .dmm_common import Scpi34401Style


class Keysight34461A(Scpi34401Style):
    profile = "keysight-34461a"
    description = "Keysight Truevolt 34461A, 6.5-digit multimeter"
    manufacturer = "Keysight Technologies"
    model = "34461A"
    serial = "MY53200001"
    firmware = "A.03.03-03.15-03.03-00.52-04-01"
    RANGES = {
        "VOLT": ("V", (0.1, 1.0, 10.0, 100.0, 1000.0)),
        "CURR": ("A", (1e-4, 1e-3, 1e-2, 0.1, 1.0, 3.0, 10.0)),
        "RES": ("OHM", (100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)),
    }
    DEFAULT_RANGES = {"VOLT": 1000.0, "CURR": 1.0, "RES": 1e3}
    RESOLUTION = 1e-6  # Resolution Table, 34461A: 1 PLC (DEF)
    RESOLUTION_MIN = 3e-7  # 100 PLC
    RESOLUTION_MAX = 1e-4  # 0.02 PLC
