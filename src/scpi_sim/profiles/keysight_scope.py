"""Keysight (Agilent) InfiniiVision DSO-X 2024A oscilloscope.

Source: Programmer's Guide for InfiniiVision 2000 X-Series Oscilloscopes, October 2014
(chapters ":WAVeform Commands", ":MEASure Commands", ":TRIGger Commands"). ``*IDN?`` starts
with ``AGILENT TECHNOLOGIES`` as in that edition.

Simulated: see ``scpi_sim.instruments.scope.Oscilloscope``: ``:RUN``, ``:STOP``, ``:SINGle``,
``:DIGitize``, ``:AUToscale``, ``:TER?``, ``:OPERegister:CONDition?``, ``:CHANnel<n>`` scale,
range, offset, display, coupling, probe; ``:TIMebase`` scale, range, position;
``:TRIGger[:EDGE]`` level, source, slope; ``:TRIGger:SWEep``, ``:TRIGger:FORCe``;
``:ACQuire:SRATe?``, ``:ACQuire:POINts?``; ``:WAVeform`` source, format (BYTE, WORD, ASCii),
byte order, unsigned, points and points mode, preamble, data and the X/Y queries;
``:MEASure`` source and the common voltage and time items. As in the guide: 0x00 is reserved
for holes, 0x01 and 0xFF mean clipped, Y reference 128 (BYTE) and 32768 (WORD), up to 100000
raw points, only on a stopped scope; a measurement without a result answers 9.9E+37. An 8-bit
converter means WORD data carries 8 significant bits.

Assumptions: the *RST defaults of the waveform settings (BYTE, 1000 points, NORMal), the serial
number and firmware are plausible values; 2 GSa/s divided by the channels sharing a converter.

Not simulated: bandwidth limits, MATH/FFT, digital channels, serial decode, zoom window, mask
tests, segmented memory, peak detect and averaging, ``:SAVE``, the rest of the event registers.
"""

from __future__ import annotations

from ..instruments.scope import Oscilloscope, signals_option
from ..options import real, whole


class KeysightDsox2024a(Oscilloscope):
    profile = "keysight-dsox2024a"
    description = "Keysight InfiniiVision DSO-X 2024A, 4 x 200 MHz oscilloscope"
    options: dict = {"signals": signals_option, "noise": real(low=0), "seed": whole(0)}
    manufacturer = "AGILENT TECHNOLOGIES"
    model = "DSO-X 2024A"
    serial = "MY54100001"
    firmware = "02.43.00"
    ERROR_QUEUE_SIZE = 30
    MAX_DEPTH = 100_000
    VOLTS_MIN = 1e-3
    VOLTS_MAX = 5.0

    def __init__(self, **kwargs):
        super().__init__(channels=4, **kwargs)
