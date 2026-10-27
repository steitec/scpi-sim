"""Keysight DAQ970A data acquisition system with DAQM901A multiplexer cards.

Source: Keysight DAQ970A/DAQ973A Data Acquisition System Programming Guide, part
number DAQ97-90002, Edition 3 (March 2020), chapter 2 "SCPI Programming".

Simulated (see ``daq_common`` for the sweep model): ``MEAS:VOLT:DC?`` and
``MEAS:RES?`` with a channel list, ``CONF:VOLT:DC``/``CONF:RES`` (redefine the scan
list, like the instrument), ``ROUT:SCAN``/``ROUT:SCAN?`` (definite-length block,
``#13(@)`` when empty), ``INIT``, ``FETC?``, ``READ?``, ``ROUT:CLOS``/``OPEN`` and their
queries. Manual switching on a card that has channels in the scan list is
refused (-221), as the manual describes. Cards: DAQM901A in slots 1-3 (option
``cards``), channels s01-s20; DC volts 100 mV-300 V, 2-wire resistance
100 ohm-100 Mohm; readings in the instrument's ``+5.00000000E+00`` format.

Not simulated: the current channels s21/s22, AC, 4-wire resistance,
temperature, other modules, scan triggers/timer/count, ``FORM:READ`` options
(channel, time, unit), alarms, mX+b, ``SENS:...`` per-channel settings,
``ROUT:CLOS:EXCL``, ``SYST:PERS``. The *IDN? serial is invented; the firmware
field is the manual's typical response.
"""

from __future__ import annotations

from ..instrument import Call, scpi
from ..options import real, whole
from .daq_common import DaqMultiplexer


class KeysightDaq970A(DaqMultiplexer):
    profile = "keysight-daq970a"
    description = "Keysight DAQ970A with DAQM901A 20-channel multiplexer cards (1-3)"
    options = {"cards": whole(1, 3), "noise": real(low=0), "seed": whole(0)}
    manufacturer = "Keysight Technologies"
    model = "DAQ970A"
    serial = "MY58000001"
    firmware = "A.02.04-00.16-11.29-00.02-02-01"
    SLOTS = 3
    CHANNELS_PER_CARD = 20
    DMM_RANGES = {
        "VOLT": ("V", (0.1, 1.0, 10.0, 100.0, 300.0)),
        "CURR": ("A", (1e-4, 1e-3, 1e-2, 0.1, 1.0)),
        "RES": ("OHM", (100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)),
    }

    @scpi("MEASure[:VOLTage][:DC]?", max_args=3)
    def meas_volt(self, call: Call) -> list[float]:
        return self._sweep(self._configure("VOLT", call))

    @scpi("MEASure:RESistance?", max_args=3)
    def meas_res(self, call: Call) -> list[float]:
        return self._sweep(self._configure("RES", call))

    @scpi("CONFigure[:VOLTage][:DC]", max_args=3)
    def conf_volt(self, call: Call) -> None:
        self._configure("VOLT", call)

    @scpi("CONFigure:RESistance", max_args=3)
    def conf_res(self, call: Call) -> None:
        self._configure("RES", call)

    @scpi("ROUTe:SCAN")
    def route_scan(self, call: Call) -> None:
        self._route_scan(call)

    @scpi("ROUTe:SCAN?")
    def route_scan_query(self, call: Call) -> str:
        return self._scan_list_block()

    @scpi("INITiate[:IMMediate]", max_args=0)
    def initiate(self, call: Call) -> None:
        self._initiate()

    @scpi("FETCh?")
    def fetch(self, call: Call) -> list[float]:
        return self._fetch()

    @scpi("READ?")
    def read(self, call: Call) -> list[float]:
        self._initiate()
        return self._fetch()

    @scpi("ROUTe:CLOSe")
    def close(self, call: Call) -> None:
        self._close_manual(call)
