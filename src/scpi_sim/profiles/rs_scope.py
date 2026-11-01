"""Rohde & Schwarz RTB2004 oscilloscope.

Source: R&S RTB2000 User Manual, 1333.1611.02, version 12 (firmware 2.4), chapter "Remote control
commands".

Simulated: ``RUN``, ``RUNContinous``, ``RUNSingle``, ``SINGle``, ``STOP``, ``ACQuire:STATe``,
``ACQuire:POINts[:VALue]``, ``ACQuire:POINts:AUTomatic``, ``ACQuire:SRATe?``; ``TIMebase:SCALe``,
``:POSition``, ``:RANGe``, ``:DIVisions?``; ``CHANnel<m>:STATe``, ``:SCALe``, ``:RANGe``,
``:POSition``, ``:OFFSet``, ``:COUPling``; ``TRIGger:A:MODE``, ``:SOURce``, ``:LEVel<n>[:VALue]``,
``:EDGE:SLOPe``; ``FORMat[:DATA]`` (ASCii, REAL,32, UINT,8|16|32), ``FORMat:BORDer``;
``CHANnel<m>:DATA?``, ``:DATA:HEADer?``, ``:DATA:POINts`` (DEF, MAX, DMAX; only DEF while running),
``:DATA:XORigin?``, ``:XINCrement?``, ``:YORigin?``, ``:YINCrement?``, ``:YRESolution?``;
``MEASurement<m>[:ENABle]``, ``:MAIN``, ``:SOURce``, ``:RESult[:ACTual]? [<type>]``. As in the manual:
12 divisions; Yn = yOrigin + yIncrement x value, center screen is byte value 127.5 (so 25 levels
per division); big endian after *RST; ASCII is the *RST format; waveform values in REAL format
are volts.

Assumptions: 2.5 GSa/s and 20 Mpoints divided by the channels sharing a converter; 10000 screen
points for DEF; a measurement without a result answers 9.91E+37, SCPI's "not a number"; duty cycle
and overshoot in percent. ``*IDN?`` firmware is a plausible value in the manual's format.

Not simulated: bandwidth, math, digital channels, FFT, mask, history, protocol decoders, other
trigger types, ``ACQuire:TYPE`` other than sample, ``EXPort``, the cursors, measurement statistics
and gates, delay and phase.
"""

from __future__ import annotations

import struct

from ..errors import ScpiError
from ..instrument import Call, block, scpi
from ..instruments.scope import Capture, NO_VALUE, ScopeEngine, keyword_arg, number_arg, signals_option, spellings
from ..options import real, whole
from ..scpi.params import parse_bool

_NO_RESULT = 9.91e37  # SCPI: not a number
_TYPES = {
    "FREQ": "frequency", "FREQUENCY": "frequency", "PER": "period", "PERIOD": "period", "PEAK": "vpp",
    "UPE": "vmax", "UPEAKVALUE": "vmax", "LPE": "vmin", "LPEAKVALUE": "vmin", "HIGH": "vtop", "LOW": "vbase",
    "AMPL": "vamp", "AMPLITUDE": "vamp", "MEAN": "vavg", "RMS": "vrms", "RTIM": "rise", "RTIME": "rise",
    "FTIM": "fall", "FTIME": "fall", "PDCY": "pduty", "PDCYCLE": "pduty", "NDCY": "nduty", "NDCYCLE": "nduty",
    "PPW": "pwidth", "PPWIDTH": "pwidth", "NPW": "nwidth", "NPWIDTH": "nwidth", "STDD": "stddev",
    "STDDEV": "stddev", "POV": "overshoot", "POVERSHOOT": "overshoot", "NOV": "preshoot", "NOVERSHOOT": "preshoot",
}
_SHORT = {"frequency": "FREQ", "period": "PER", "vpp": "PEAK", "vmax": "UPE", "vmin": "LPE", "vtop": "HIGH",
          "vbase": "LOW", "vamp": "AMPL", "vavg": "MEAN", "vrms": "RMS", "rise": "RTIM", "fall": "FTIM",
          "pduty": "PDCY", "nduty": "NDCY", "pwidth": "PPW", "nwidth": "NPW", "stddev": "STDD",
          "overshoot": "POV", "preshoot": "NOV"}
_PERCENT = {"pduty", "nduty", "overshoot", "preshoot"}
_DEFAULT_POINTS = 10_000


class RsRtb2004(ScopeEngine):
    profile = "rs-rtb2004"
    description = "Rohde & Schwarz RTB2004, 4 x 300 MHz oscilloscope"
    options: dict = {"signals": signals_option, "noise": real(low=0), "seed": whole(0)}
    manufacturer = "Rohde&Schwarz"
    model = "RTB2004"
    serial = "1333.1005K04/100001"
    firmware = "02.400"
    DIVS_X = 12
    LSB_PER_DIV = 25.0
    ADC_REF = 127.5
    MAX_RATE = 2.5e9
    MAX_DEPTH = 20_000_000
    VOLTS_MIN = 1e-3
    VOLTS_MAX = 5.0
    TIME_MIN = 1e-9
    TIME_MAX = 50.0
    DEFAULT_TIME = 100e-6

    def format_number(self, value: float) -> str:
        return f"{value:.9E}"

    def reset(self) -> None:
        super().reset()
        self.levels = {n: 0.0 for n in self.channels}
        self.data_format, self.data_bits = "ASC", 0
        self.msb_first = True
        self.point_selection = "DEF"
        self.measure_main = {m: "frequency" for m in range(1, 7)}
        self.measure_sources = {m: 1 for m in range(1, 7)}
        self.measure_on = {m: False for m in range(1, 7)}
        self._finished = False

    def _ch(self, text: str) -> int:
        word = text.strip().upper()
        if word[:2] != "CH" or word[2:] not in ("1", "2", "3", "4"):
            raise ScpiError(-224)
        return int(word[2:])

    # --- run control ---------------------------------------------------------------------------

    @scpi("RUN", max_args=0, also=("RUNContinous",))
    def _run_cmd(self, call: Call) -> None:
        self._finished = False
        self.run()

    @scpi("RUNSingle", max_args=0, also=("SINGle",))
    def _single_cmd(self, call: Call) -> None:
        self._finished = True
        self.single()

    @scpi("STOP", max_args=0)
    def _stop_cmd(self, call: Call) -> None:
        self._finished = False
        self.stop()

    @scpi("ACQuire:STATe")
    def _acq_state(self, call: Call) -> None:
        word = keyword_arg(call, 0, spellings("RUN", "STOPping", "BREak"))
        self._finished = False
        if word == "RUN":
            self.run()
        else:
            self.stop()

    @scpi("ACQuire:STATe?")
    def _acq_state_q(self, call: Call) -> str:
        self.poll()
        if self.running:
            return "RUN"
        return "COMP" if self._finished else "STOP"

    @scpi("ACQuire:POINts[:VALue]")
    def _points(self, call: Call) -> None:
        self.depth = round(number_arg(call, 0, 10_000, self.MAX_DEPTH))

    @scpi("ACQuire:POINts[:VALue]?")
    def _points_q(self, call: Call) -> int:
        return self.record()[1]

    @scpi("ACQuire:POINts:AUTomatic")
    def _points_auto(self, call: Call) -> None:
        if parse_bool(call.arg(0)):
            self.depth = None

    @scpi("ACQuire:POINts:AUTomatic?")
    def _points_auto_q(self, call: Call) -> bool:
        return self.depth is None

    @scpi("ACQuire:SRATe?")
    def _srate(self, call: Call) -> float:
        return self.record()[0]

    # --- horizontal ----------------------------------------------------------------------------

    @scpi("TIMebase:SCALe")
    def _time_scale(self, call: Call) -> None:
        self.set_time(number_arg(call, 0, unit="S"))

    @scpi("TIMebase:SCALe?")
    def _time_scale_q(self, call: Call) -> float:
        return self.time_scale

    @scpi("TIMebase:RANGe")
    def _time_range(self, call: Call) -> None:
        self.set_time(number_arg(call, 0, unit="S") / self.DIVS_X)

    @scpi("TIMebase:RANGe?")
    def _time_range_q(self, call: Call) -> float:
        return self.time_scale * self.DIVS_X

    @scpi("TIMebase:POSition")
    def _time_position(self, call: Call) -> None:
        self.time_position = number_arg(call, 0, unit="S")

    @scpi("TIMebase:POSition?")
    def _time_position_q(self, call: Call) -> float:
        return self.time_position

    @scpi("TIMebase:DIVisions?")
    def _divisions(self, call: Call) -> int:
        return self.DIVS_X

    # --- vertical ----------------------------------------------------------------------------------

    @scpi("CHANnel#:STATe")
    def _chan_state(self, call: Call) -> None:
        self.channel(call.suffixes[0]).on = parse_bool(call.arg(0))

    @scpi("CHANnel#:STATe?")
    def _chan_state_q(self, call: Call) -> bool:
        return self.channel(call.suffixes[0]).on

    @scpi("CHANnel#:SCALe")
    def _chan_scale(self, call: Call) -> None:
        self.set_volts(self.channel_number(call.suffixes[0]), number_arg(call, 0, unit="V"))

    @scpi("CHANnel#:SCALe?")
    def _chan_scale_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).scale

    @scpi("CHANnel#:RANGe")
    def _chan_range(self, call: Call) -> None:
        self.set_volts(self.channel_number(call.suffixes[0]), number_arg(call, 0, unit="V") / self.DIVS_Y)

    @scpi("CHANnel#:RANGe?")
    def _chan_range_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).scale * self.DIVS_Y

    @scpi("CHANnel#:POSition")
    def _chan_position(self, call: Call) -> None:
        self.channel(call.suffixes[0]).position = number_arg(call, 0, -5, 5)

    @scpi("CHANnel#:POSition?")
    def _chan_position_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).position

    @scpi("CHANnel#:OFFSet")
    def _chan_offset(self, call: Call) -> None:
        self.channel(call.suffixes[0]).offset = number_arg(call, 0, unit="V")

    @scpi("CHANnel#:OFFSet?")
    def _chan_offset_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).offset

    @scpi("CHANnel#:COUPling")
    def _chan_coupling(self, call: Call) -> None:
        self.channel(call.suffixes[0]).coupling = keyword_arg(
            call, 0, {"DCL": "DC", "DCLIMIT": "DC", "ACL": "AC", "ACLIMIT": "AC", "GND": "GND"})

    @scpi("CHANnel#:COUPling?")
    def _chan_coupling_q(self, call: Call) -> str:
        return {"DC": "DCL", "AC": "ACL", "GND": "GND"}[self.channel(call.suffixes[0]).coupling]

    # --- trigger ----------------------------------------------------------------------------------------

    @scpi("TRIGger:A:MODE")
    def _trig_mode(self, call: Call) -> None:
        self.sweep = "AUTO" if keyword_arg(call, 0, spellings("AUTO", "NORMal")) == "AUTO" else "NORMAL"

    @scpi("TRIGger:A:MODE?")
    def _trig_mode_q(self, call: Call) -> str:
        return "AUTO" if self.sweep == "AUTO" else "NORM"

    @scpi("TRIGger:A:SOURce")
    def _trig_source(self, call: Call) -> None:
        self.trigger_source = self._ch(call.arg(0))
        self.trigger_level = self.levels[self.trigger_source]

    @scpi("TRIGger:A:SOURce?")
    def _trig_source_q(self, call: Call) -> str:
        return f"CH{self.trigger_source}"

    @scpi("TRIGger:A:LEVel#[:VALue]")
    def _trig_level(self, call: Call) -> None:
        number = self.channel_number(call.suffixes[0])
        self.levels[number] = number_arg(call, 0, unit="V")
        if number == self.trigger_source:
            self.trigger_level = self.levels[number]

    @scpi("TRIGger:A:LEVel#[:VALue]?")
    def _trig_level_q(self, call: Call) -> float:
        return self.levels[self.channel_number(call.suffixes[0])]

    @scpi("TRIGger:A:EDGE:SLOPe")
    def _trig_slope(self, call: Call) -> None:
        self.trigger_slope = keyword_arg(call, 0, {"POS": "POS", "POSITIVE": "POS", "NEG": "NEG", "NEGATIVE": "NEG",
                                                   "EITH": "EITHER", "EITHER": "EITHER"})

    @scpi("TRIGger:A:EDGE:SLOPe?")
    def _trig_slope_q(self, call: Call) -> str:
        return {"POS": "POS", "NEG": "NEG", "EITHER": "EITH"}[self.trigger_slope]

    # --- waveform data -------------------------------------------------------------------------------------

    @scpi("FORMat[:DATA]", max_args=2)
    def _format_data(self, call: Call) -> None:
        word = call.arg(0).strip().upper()
        accuracy = call.args[1].strip() if len(call.args) > 1 else None
        if word in ("ASC", "ASCII") and accuracy in (None, "0"):
            self.data_format, self.data_bits = "ASC", 0
        elif word == "REAL" and accuracy in (None, "32"):
            self.data_format, self.data_bits = "REAL", 32
        elif word in ("UINT", "UINTEGER") and accuracy in ("8", "16", "32"):
            self.data_format, self.data_bits = "UINT", int(accuracy)
        else:
            raise ScpiError(-224)

    @scpi("FORMat[:DATA]?")
    def _format_data_q(self, call: Call) -> str:
        return f"{self.data_format},{self.data_bits}"

    @scpi("FORMat:BORDer")
    def _border(self, call: Call) -> None:
        self.msb_first = keyword_arg(call, 0, {"MSBF": "M", "MSBFIRST": "M", "LSBF": "L", "LSBFIRST": "L"}) == "M"

    @scpi("FORMat:BORDer?")
    def _border_q(self, call: Call) -> str:
        return "MSBF" if self.msb_first else "LSBF"

    @scpi("CHANnel#:DATA:POINts")
    def _data_points(self, call: Call) -> None:
        if not self.running:  # the range can only be changed in STOP mode
            self.point_selection = keyword_arg(call, 0, {"DEF": "DEF", "DEFAULT": "DEF", "MAX": "MAX",
                                                         "MAXIMUM": "MAX", "DMAX": "DMAX", "DMAXIMUM": "DMAX"})

    def _count(self, capture: Capture) -> int:
        if self.point_selection == "DEF" or self.running:
            return min(capture.points, _DEFAULT_POINTS)
        return capture.points

    @scpi("CHANnel#:DATA:POINts?")
    def _data_points_q(self, call: Call) -> int:
        return self._count(self.current_capture())

    def _dt(self, capture: Capture) -> float:
        return capture.window / self._count(capture)

    @scpi("CHANnel#:DATA:HEADer?")
    def _data_header(self, call: Call) -> str:
        self.channel_number(call.suffixes[0])
        capture = self.current_capture()
        count = self._count(capture)
        stop = capture.x0 + (count - 1) * self._dt(capture)
        return f"{_three(capture.x0)},{_three(stop)},{count},1"

    @scpi("CHANnel#:DATA:XORigin?")
    def _data_xorigin(self, call: Call) -> float:
        return self.current_capture().x0

    @scpi("CHANnel#:DATA:XINCrement?")
    def _data_xinc(self, call: Call) -> float:
        return self._dt(self.current_capture())

    def _scale_factor(self) -> int:
        return {16: 256, 32: 1 << 24}.get(self.data_bits, 1)

    @scpi("CHANnel#:DATA:YORigin?")
    def _data_yorigin(self, call: Call) -> float:
        trace = self.current_capture().traces[self.channel_number(call.suffixes[0])]
        if self.data_format == "UINT":
            return trace.center - self.ADC_REF * trace.lsb
        return 0.0

    @scpi("CHANnel#:DATA:YINCrement?")
    def _data_yinc(self, call: Call) -> float:
        trace = self.current_capture().traces[self.channel_number(call.suffixes[0])]
        return trace.lsb / self._scale_factor() if self.data_format == "UINT" else 1.0

    @scpi("CHANnel#:DATA:YRESolution?")
    def _data_yres(self, call: Call) -> int:
        return self.data_bits if self.data_format == "UINT" else 8

    @scpi("CHANnel#:DATA?")
    def _data(self, call: Call) -> bytes | str:
        number = self.channel_number(call.suffixes[0])
        capture = self.current_capture()
        if not capture.traces[number].on:
            raise ScpiError(-221, "Settings conflict; channel is off")
        count, dt = self._count(capture), self._dt(capture)
        if self.data_format == "ASC":
            return ",".join(f"{v:.6f}" for v in self.read_volts(capture, number, 0, count, dt))
        order = ">" if self.msb_first else "<"
        if self.data_format == "REAL":
            volts = self.read_volts(capture, number, 0, count, dt)
            return block(struct.pack(f"{order}{count}f", *volts))
        codes = self.read_codes(capture, number, 0, count, dt)
        if self.data_bits == 8:
            return block(bytes(codes))
        letter, factor = ("H", 256) if self.data_bits == 16 else ("I", 1 << 24)
        return block(struct.pack(f"{order}{count}{letter}", *(code * factor for code in codes)))

    # --- measurements ------------------------------------------------------------------------------------------

    def _place(self, suffix: int | None) -> int:
        place = 1 if suffix is None else suffix
        if not 1 <= place <= 6:
            raise ScpiError(-114)
        return place

    @scpi("MEASurement#[:ENABle]")
    def _meas_enable(self, call: Call) -> None:
        self.measure_on[self._place(call.suffixes[0])] = parse_bool(call.arg(0))

    @scpi("MEASurement#[:ENABle]?")
    def _meas_enable_q(self, call: Call) -> bool:
        return self.measure_on[self._place(call.suffixes[0])]

    def _type(self, text: str) -> str:
        name = _TYPES.get(text.strip().upper())
        if name is None:
            raise ScpiError(-224)
        return name

    @scpi("MEASurement#:MAIN")
    def _meas_main(self, call: Call) -> None:
        self.measure_main[self._place(call.suffixes[0])] = self._type(call.arg(0))

    @scpi("MEASurement#:MAIN?")
    def _meas_main_q(self, call: Call) -> str:
        return _SHORT[self.measure_main[self._place(call.suffixes[0])]]

    @scpi("MEASurement#:SOURce")
    def _meas_source(self, call: Call) -> None:
        self.measure_sources[self._place(call.suffixes[0])] = self._ch(call.arg(0))

    @scpi("MEASurement#:SOURce?")
    def _meas_source_q(self, call: Call) -> str:
        return f"CH{self.measure_sources[self._place(call.suffixes[0])]}"

    @scpi("MEASurement#:RESult[:ACTual]?", max_args=1)
    def _meas_result(self, call: Call) -> float:
        place = self._place(call.suffixes[0])
        name = self._type(call.args[0]) if call.args else self.measure_main[place]
        value = self.measure_value(self.measure_sources[place], name)
        if value == NO_VALUE:
            return _NO_RESULT
        return value * 100 if name in _PERCENT else value


def _three(value: float) -> str:
    """The manual's header style: ``-9.477E-008``."""
    mantissa, exponent = f"{value:.3E}".split("E")
    return f"{mantissa}E{int(exponent):+04d}"
