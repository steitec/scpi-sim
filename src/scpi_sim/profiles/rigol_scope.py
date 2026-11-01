"""Rigol DS1054Z oscilloscope.

Source: RIGOL DS1000Z Programming Guide, PGA19109-1110, May 2019 (software 00.04.03.SP2).

Simulated: ``:RUN``, ``:STOP``, ``:SINGle``, ``:TFORce``, ``:AUToscale``; ``:ACQuire:MDEPth``,
``:SRATe?``, ``:TYPE?``; ``:CHANnel<n>`` scale, offset, display, coupling, probe, range;
``:TIMebase[:MAIN]`` scale and offset, ``:TIMebase:MODE?``; ``:TRIGger`` mode, sweep,
``:STATus?``, ``:EDGe`` source, slope and level; ``:WAVeform`` source, mode (NORMal is the 1200
screen points, RAW the memory and only on a stopped scope), format, ``:DATA?``, start, stop,
preamble and the X/Y queries; ``:MEASure:SOURce``, ``:MEASure:ITEM``, ``:MEASure:ITEM?``, ``:MEASure:STATistic:ITEM?`` (of one acquisition, no history);
``:SYSTem:GAM?``; ``:MATH:DISPlay`` (always off). As in the guide: numbers like ``1.000000e+00``, volts =
(code - YORigin - YREFerence) x YINCrement with YINCrement = scale/25 and YREFerence 127, 12
horizontal divisions, scales in 1-2-5 steps, memory depth by the number of channels on, at
most 250000/125000/15625 points per read (BYTE/WORD/ASCii) in RAW, WORD data with the upper byte
0, a positive offset moves the trace up, 9.9E37 for a measurement without a result, ratios
(not percent) for overshoot. ``:WAV:STOP`` stays at 1200 until set, so a RAW read without it
returns 1200 points, as the guide's procedure implies.

Assumptions: serial number; duty cycle as a ratio like overshoot (the guide gives no example);
reading a channel that is off gives -221.

Not simulated: MATH, digital channels D0-D15, decoders, other trigger types, averaging and peak
detect, delay and phase measurements, statistics over several acquisitions, reference waveforms, the source (DG) option,
``:DISPlay:DATA?``, 1X probe offset limits.
"""

from __future__ import annotations

import re

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.scope import Capture, NO_VALUE, ScopeEngine, keyword_arg, number_arg, signals_option, spellings
from ..options import real, whole
from ..scpi.params import parse_bool

_ITEMS = {
    "VMAX": "vmax", "VMIN": "vmin", "VPP": "vpp", "VTOP": "vtop", "VBAS": "vbase", "VBASE": "vbase",
    "VAMP": "vamp", "VAVG": "vavg", "VRMS": "vrms", "OVER": "overshoot", "OVERSHOOT": "overshoot",
    "PRES": "preshoot", "PRESHOOT": "preshoot", "PER": "period", "PERIOD": "period", "FREQ": "frequency",
    "FREQUENCY": "frequency", "RTIM": "rise", "RTIME": "rise", "FTIM": "fall", "FTIME": "fall",
    "PWID": "pwidth", "PWIDTH": "pwidth", "NWID": "nwidth", "NWIDTH": "nwidth", "PDUT": "pduty", "PDUTY": "pduty",
    "NDUT": "nduty", "NDUTY": "nduty", "VMID": "vmid",
}
_READ_LIMIT = {"BYTE": 250_000, "WORD": 125_000, "ASC": 15_625}
_DEPTHS = {1: (12_000, 120_000, 1_200_000, 12_000_000, 24_000_000),
           2: (6_000, 60_000, 600_000, 6_000_000, 12_000_000),
           4: (3_000, 30_000, 300_000, 3_000_000, 6_000_000)}
_SCREEN_POINTS = 1200


class RigolDs1054z(ScopeEngine):
    profile = "rigol-ds1054z"
    description = "Rigol DS1054Z, 4 x 50 MHz oscilloscope"
    options: dict = {"signals": signals_option, "noise": real(low=0), "seed": whole(0)}
    manufacturer = "RIGOL TECHNOLOGIES"
    model = "DS1054Z"
    serial = "DS1ZA000000001"
    firmware = "00.04.03.SP2"
    DIVS_X = 12
    LSB_PER_DIV = 25.0
    ADC_REF = 127
    MAX_RATE = 1e9
    MAX_DEPTH = 24_000_000
    VOLTS_MIN = 1e-3
    VOLTS_MAX = 10.0
    TIME_MIN = 5e-9
    TIME_MAX = 50.0
    SNAP_VOLTS = True
    SNAP_TIME = True
    OFFSET_SIGN = -1
    DEFAULT_PROBE = 10.0
    DEFAULT_TIME = 1e-6
    PROBES = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)

    def format_number(self, value: float) -> str:
        return f"{value:.6e}"

    def reset(self) -> None:
        super().reset()
        self.wave_source = 1
        self.wave_mode = "NORM"
        self.wave_format = "BYTE"
        self.wave_start, self.wave_stop = 1, _SCREEN_POINTS
        self.measure_source = 1
        self.single_sweep = False

    def _source(self, text: str) -> int:
        match = re.fullmatch(r"CHAN(?:NEL)?([1-4])", text.strip().upper())
        if not match:
            raise ScpiError(-224)
        return int(match.group(1))

    # --- root -----------------------------------------------------------------------------

    @scpi("RUN", max_args=0)
    def _run_cmd(self, call: Call) -> None:
        self.single_sweep = False
        self.run()

    @scpi("STOP", max_args=0)
    def _stop_cmd(self, call: Call) -> None:
        self.stop()

    @scpi("SINGle", max_args=0)
    def _single_cmd(self, call: Call) -> None:
        self.single_sweep = True
        self.single()

    @scpi("TFORce", max_args=0)
    def _tforce(self, call: Call) -> None:
        self.force_trigger()

    @scpi("AUToscale", max_args=0)
    def _autoscale_cmd(self, call: Call) -> None:
        self.autoscale()

    # --- acquire ---------------------------------------------------------------------------

    @scpi("ACQuire:MDEPth")
    def _mdepth(self, call: Call) -> None:
        arg = call.arg(0).strip().upper()
        if arg == "AUTO":
            self.depth = None
            return
        if not arg.isdigit() or int(arg) not in _DEPTHS[self._channel_share()]:
            raise ScpiError(-224)
        self.depth = int(arg)

    @scpi("ACQuire:MDEPth?")
    def _mdepth_q(self, call: Call) -> str | int:
        return "AUTO" if self.depth is None else self.depth

    @scpi("ACQuire:SRATe?")
    def _srate(self, call: Call) -> float:
        return self.record()[0]

    @scpi("ACQuire:TYPE?")
    def _acq_type(self, call: Call) -> str:
        return "NORM"

    # --- channels ----------------------------------------------------------------------------

    @scpi("CHANnel#:SCALe")
    def _chan_scale(self, call: Call) -> None:
        self.set_volts(self.channel_number(call.suffixes[0]), number_arg(call, 0, unit="V"))

    @scpi("CHANnel#:SCALe?")
    def _chan_scale_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).scale

    @scpi("CHANnel#:RANGe")
    def _chan_range(self, call: Call) -> None:
        self.set_volts(self.channel_number(call.suffixes[0]), number_arg(call, 0, unit="V") / 8)

    @scpi("CHANnel#:RANGe?")
    def _chan_range_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).scale * 8

    @scpi("CHANnel#:OFFSet")
    def _chan_offset(self, call: Call) -> None:
        channel = self.channel(call.suffixes[0])
        limit = 1000.0 if channel.scale >= 5 else 20.0  # 10X probe; the 1X limits are ten times smaller
        channel.offset = number_arg(call, 0, -limit, limit, unit="V")

    @scpi("CHANnel#:OFFSet?")
    def _chan_offset_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).offset

    @scpi("CHANnel#:DISPlay")
    def _chan_display(self, call: Call) -> None:
        self.channel(call.suffixes[0]).on = parse_bool(call.arg(0))

    @scpi("CHANnel#:DISPlay?")
    def _chan_display_q(self, call: Call) -> bool:
        return self.channel(call.suffixes[0]).on

    @scpi("CHANnel#:COUPling")
    def _chan_coupling(self, call: Call) -> None:
        self.channel(call.suffixes[0]).coupling = keyword_arg(call, 0, {"AC": "AC", "DC": "DC", "GND": "GND"})

    @scpi("CHANnel#:COUPling?")
    def _chan_coupling_q(self, call: Call) -> str:
        return self.channel(call.suffixes[0]).coupling

    @scpi("CHANnel#:PROBe")
    def _chan_probe(self, call: Call) -> None:
        self.set_probe(self.channel_number(call.suffixes[0]), number_arg(call, 0))

    @scpi("CHANnel#:PROBe?")
    def _chan_probe_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).probe

    # --- timebase ---------------------------------------------------------------------------------

    @scpi("TIMebase[:MAIN]:SCALe")
    def _time_scale(self, call: Call) -> None:
        self.set_time(number_arg(call, 0, unit="S"))

    @scpi("TIMebase[:MAIN]:SCALe?")
    def _time_scale_q(self, call: Call) -> float:
        return self.time_scale

    @scpi("TIMebase[:MAIN]:OFFSet")
    def _time_offset(self, call: Call) -> None:
        self.time_position = number_arg(call, 0, unit="S")

    @scpi("TIMebase[:MAIN]:OFFSet?")
    def _time_offset_q(self, call: Call) -> float:
        return self.time_position

    @scpi("TIMebase:MODE?")
    def _time_mode(self, call: Call) -> str:
        return "MAIN"

    @scpi("MATH:DISPlay")
    def _math_display(self, call: Call) -> None:
        if parse_bool(call.arg(0)):
            raise ScpiError(-221, "Settings conflict; MATH is not simulated")

    @scpi("MATH:DISPlay?")  # client libraries ask this to list the displayed channels
    def _math_display_q(self, call: Call) -> int:
        return 0

    @scpi("SYSTem:GAM?")
    def _gam(self, call: Call) -> int:
        return self.DIVS_X

    # --- trigger ------------------------------------------------------------------------------------

    @scpi("TRIGger:MODE")
    def _trig_mode(self, call: Call) -> None:
        if call.arg(0).strip().upper() not in ("EDGE", "EDG"):
            raise ScpiError(-221, "Settings conflict; only the edge trigger is simulated")

    @scpi("TRIGger:MODE?")
    def _trig_mode_q(self, call: Call) -> str:
        return "EDGE"

    @scpi("TRIGger:SWEep")
    def _trig_sweep(self, call: Call) -> None:
        value = keyword_arg(call, 0, spellings("AUTO", "NORMal", "SINGle"))
        self.single_sweep = value == "SING"
        if self.single_sweep:
            self.single()
        else:
            self.sweep = "AUTO" if value == "AUTO" else "NORMAL"

    @scpi("TRIGger:SWEep?")
    def _trig_sweep_q(self, call: Call) -> str:
        return "SING" if self.single_sweep else "AUTO" if self.sweep == "AUTO" else "NORM"

    @scpi("TRIGger:STATus?")
    def _trig_status(self, call: Call) -> str:
        return {"stop": "STOP", "triggered": "TD", "auto": "AUTO", "wait": "WAIT"}[self.status]

    @scpi("TRIGger:EDGe:SOURce")
    def _edge_source(self, call: Call) -> None:
        self.trigger_source = self._source(call.arg(0))

    @scpi("TRIGger:EDGe:SOURce?")
    def _edge_source_q(self, call: Call) -> str:
        return f"CHAN{self.trigger_source}"

    @scpi("TRIGger:EDGe:SLOPe")
    def _edge_slope(self, call: Call) -> None:
        self.trigger_slope = keyword_arg(call, 0, {"POS": "POS", "POSITIVE": "POS", "NEG": "NEG", "NEGATIVE": "NEG",
                                                   "RFAL": "EITHER", "RFALL": "EITHER"})

    @scpi("TRIGger:EDGe:SLOPe?")
    def _edge_slope_q(self, call: Call) -> str:
        return {"POS": "POS", "NEG": "NEG", "EITHER": "RFAL"}[self.trigger_slope]

    @scpi("TRIGger:EDGe:LEVel")
    def _edge_level(self, call: Call) -> None:
        channel = self.channels[self.trigger_source]
        limit = 5 * channel.scale  # (-5 x VerticalScale - OFFSet) to (5 x VerticalScale - OFFSet)
        self.trigger_level = number_arg(call, 0, -limit - channel.offset, limit - channel.offset, unit="V")

    @scpi("TRIGger:EDGe:LEVel?")
    def _edge_level_q(self, call: Call) -> float:
        return self.trigger_level

    # --- waveform -----------------------------------------------------------------------------------

    @scpi("WAVeform:SOURce")
    def _wav_source(self, call: Call) -> None:
        self.wave_source = self._source(call.arg(0))

    @scpi("WAVeform:SOURce?")
    def _wav_source_q(self, call: Call) -> str:
        return f"CHAN{self.wave_source}"

    @scpi("WAVeform:MODE")
    def _wav_mode(self, call: Call) -> None:
        self.wave_mode = keyword_arg(call, 0, spellings("NORMal", "MAXimum", "RAW"))

    @scpi("WAVeform:MODE?")
    def _wav_mode_q(self, call: Call) -> str:
        return self.wave_mode

    @scpi("WAVeform:FORMat")
    def _wav_format(self, call: Call) -> None:
        self.wave_format = keyword_arg(call, 0, {"WORD": "WORD", "BYTE": "BYTE", "ASC": "ASC", "ASCII": "ASC"})

    @scpi("WAVeform:FORMat?")
    def _wav_format_q(self, call: Call) -> str:
        return self.wave_format

    @scpi("WAVeform:STARt")
    def _wav_start(self, call: Call) -> None:
        self.wave_start = round(number_arg(call, 0, 1, self.MAX_DEPTH))

    @scpi("WAVeform:STARt?")
    def _wav_start_q(self, call: Call) -> int:
        return self.wave_start

    @scpi("WAVeform:STOP")
    def _wav_stop(self, call: Call) -> None:
        self.wave_stop = round(number_arg(call, 0, 1, self.MAX_DEPTH))

    @scpi("WAVeform:STOP?")
    def _wav_stop_q(self, call: Call) -> int:
        return self.wave_stop

    def _uses_memory(self) -> bool:
        return self.wave_mode == "RAW" or (self.wave_mode == "MAX" and not self.running)

    def _view(self, capture: Capture) -> tuple[int, float]:
        """(points in the selected record, time between them)."""
        points = capture.points if self._uses_memory() else _SCREEN_POINTS
        return points, capture.window / points

    @scpi("WAVeform:XINCrement?")
    def _xinc(self, call: Call) -> float:
        return self._view(self.current_capture())[1]

    @scpi("WAVeform:XORigin?")
    def _xorigin(self, call: Call) -> float:
        return self.current_capture().x0

    @scpi("WAVeform:XREFerence?")
    def _xref(self, call: Call) -> int:
        return 0

    @scpi("WAVeform:YINCrement?")
    def _yinc(self, call: Call) -> float:
        return self.current_capture().traces[self.wave_source].lsb

    def _y_origin(self, capture: Capture) -> int:
        trace = capture.traces[self.wave_source]
        return round(-trace.center / trace.lsb)

    @scpi("WAVeform:YORigin?")
    def _yorigin(self, call: Call) -> int:
        return self._y_origin(self.current_capture())

    @scpi("WAVeform:YREFerence?")
    def _yref(self, call: Call) -> int:
        return int(self.ADC_REF)

    @scpi("WAVeform:PREamble?")
    def _preamble(self, call: Call) -> str:
        capture = self.current_capture()
        points, dt = self._view(capture)
        kind = {"BYTE": 0, "WORD": 1, "ASC": 2}[self.wave_format]
        mode = {"NORM": 0, "MAX": 1, "RAW": 2}[self.wave_mode]
        lsb = capture.traces[self.wave_source].lsb
        return (f"{kind},{mode},{points},1,{dt:.6e},{capture.x0:.6e},0,{lsb:.6e},"
                f"{self._y_origin(capture)},{int(self.ADC_REF)}")

    @scpi("WAVeform:DATA?")
    def _wav_data(self, call: Call) -> bytes | str:
        if self.wave_mode == "RAW" and self.running:
            raise ScpiError(-221, "Settings conflict; RAW data needs a stopped scope")
        capture = self.current_capture()
        number = self.wave_source
        if not capture.traces[number].on:
            raise ScpiError(-221, "Settings conflict; waveform source is not displayed")
        points, dt = self._view(capture)
        stop = min(self.wave_stop, points)
        if self.wave_start > stop:
            raise ScpiError(-222)
        first, count = self.wave_start - 1, stop - self.wave_start + 1
        if self._uses_memory() and count > _READ_LIMIT[self.wave_format]:
            raise ScpiError(-222, "Data out of range; too many points for one read")
        if self.wave_format == "ASC":
            return ",".join(f"{v:.6e}" for v in self.read_volts(capture, number, first, count, dt))
        codes = self.read_codes(capture, number, first, count, dt)
        if self.wave_format == "BYTE":
            return _tmc(bytes(codes))
        return _tmc(b"".join(bytes((code, 0)) for code in codes))

    # --- measure ------------------------------------------------------------------------------------------

    @scpi("MEASure:SOURce")
    def _meas_source(self, call: Call) -> None:
        self.measure_source = self._source(call.arg(0))

    @scpi("MEASure:SOURce?")
    def _meas_source_q(self, call: Call) -> str:
        return f"CHAN{self.measure_source}"

    def _item(self, call: Call) -> tuple[str, int]:
        name = _ITEMS.get(call.arg(0).strip().upper())
        if name is None:
            raise ScpiError(-224)
        return name, self._source(call.args[1]) if len(call.args) > 1 else self.measure_source

    @scpi("MEASure:ITEM", max_args=3)
    def _meas_item(self, call: Call) -> None:
        self._item(call)

    @scpi("MEASure:ITEM?", max_args=3)
    def _meas_item_q(self, call: Call) -> float:
        return self.measure_value(*reversed(self._item(call)))

    @scpi("MEASure:STATistic:ITEM?", max_args=4)
    def _meas_statistic_q(self, call: Call) -> float:
        # no history is kept: the statistics of one acquisition
        kind = keyword_arg(call, 0, spellings("MAXimum", "MINimum", "CURRent", "AVERages", "DEViation"))
        name = _ITEMS.get(call.arg(1).strip().upper())
        if name is None:
            raise ScpiError(-224)
        source = self._source(call.args[2]) if len(call.args) > 2 else self.measure_source
        value = self.measure_value(source, name)
        return 0.0 if kind == "DEV" and value != NO_VALUE else value


def _tmc(data: bytes) -> bytes:
    """The guide's TMC header: always ``#9`` and nine digits."""
    return f"#9{len(data):09d}".encode() + data


