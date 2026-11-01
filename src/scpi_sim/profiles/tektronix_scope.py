"""Tektronix TBS2104B oscilloscope.

Source: TBS2000B Series Programmer Manual, 077-1149-03.

Tektronix does not speak SCPI's tree: ``DATa:SOUrce CH1``, ``WFMOutpre?``, ``CURVe?``. With
``HEADer ON``, the manual's default, answers repeat their command (``:CH1:SCALE 1.0000E+00``);
*-commands never do. There is no ``SYSTem:ERRor?``: errors come from ``ALLEv?``, ``EVENT?``,
``EVMsg?`` and ``EVQty?`` as positive event codes, ``0`` if there are none.

Simulated: ``HEADer``; ``ACQuire:STATE``, ``:STOPAfter``, ``:NUMACq?``; ``HORizontal[:MAIn]:SCAle``,
``:SECdiv``, ``:SAMPLERate?``, ``HORizontal:POSition`` (here seconds of delay), ``:RECOrdlength``;
``CH<x>:SCAle``, ``:VOLts``, ``:OFFSet``, ``:POSition``, ``:COUPling``, ``:PRObe``; ``SELect:CH<x>``;
``TRIGger:A:MODe``, ``:LEVel``, ``:LEVel:CH<x>``, ``:EDGE:SOUrce``, ``:EDGE:SLOpe``; ``TRIGger:FORCe``,
``TRIGger:STATE?``; ``DATa`` INIT, ``DATa:SOUrce``, ``:STARt``, ``:STOP``, ``:WIDth``; ``WFMOutpre?`` and
its fields, ``CURVe?`` (binary RI or RP, 1 or 2 bytes, or ASCII);
``MEASUrement:IMMed:TYPe``, ``:SOUrce1``, ``:VALue?``, ``:UNIts?``. As in the manual:
value = (code - YOFf) x YMUlt + YZEro, ``9.9000E+37`` for no result, ``CURVe?`` of a channel that is
off answers nothing and queues events 2244 and 420.

Assumptions: 25 levels per division (YMUlt = scale/25, the usual Tektronix value; the manual only
gives examples for other widths), 1 GSa/s and 5 Mpoints, serial number and firmware (the
format follows the manual's TBS2104 example).

Not simulated: ``VERBose OFF`` (answers always use long forms), MATH/FFT, references, cursors, search,
``MEASUrement:MEAS<x>``, averaging, Trigger View, ``CURVe`` writes, file system.
"""

from __future__ import annotations

from ..errors import ScpiError
from ..instrument import Call, block, scpi
from ..instruments.scope import Capture, NO_VALUE, ScopeEngine, keyword_arg, number_arg, signals_option, spellings
from ..options import real, whole
from ..scpi.params import parse_bool

_MEASURES = {
    "AMP": ("vamp", "V"), "AMPLITUDE": ("vamp", "V"), "FALL": ("fall", "s"), "FREQ": ("frequency", "Hz"),
    "FREQUENCY": ("frequency", "Hz"), "HIGH": ("vtop", "V"), "LOW": ("vbase", "V"), "MAX": ("vmax", "V"),
    "MAXIMUM": ("vmax", "V"), "MEAN": ("vavg", "V"), "MINI": ("vmin", "V"), "MINIMUM": ("vmin", "V"),
    "NWI": ("nwidth", "s"), "NWIDTH": ("nwidth", "s"), "NDU": ("nduty", "%"), "NDUTY": ("nduty", "%"),
    "PDU": ("pduty", "%"), "PDUTY": ("pduty", "%"), "PERI": ("period", "s"), "PERIOD": ("period", "s"),
    "PK2": ("vpp", "V"), "PK2PK": ("vpp", "V"), "POV": ("overshoot", "%"), "POVERSHOOT": ("overshoot", "%"),
    "NOV": ("preshoot", "%"), "NOVERSHOOT": ("preshoot", "%"), "PWI": ("pwidth", "s"), "PWIDTH": ("pwidth", "s"),
    "RIS": ("rise", "s"), "RISE": ("rise", "s"), "RMS": ("vrms", "V"),
}
_NAMES = {"vamp": "AMPLITUDE", "fall": "FALL", "frequency": "FREQUENCY", "vtop": "HIGH", "vbase": "LOW",
          "vmax": "MAXIMUM", "vavg": "MEAN", "vmin": "MINIMUM", "nwidth": "NWIDTH", "nduty": "NDUTY",
          "pduty": "PDUTY", "period": "PERIOD", "vpp": "PK2PK", "overshoot": "POVERSHOOT",
          "preshoot": "NOVERSHOOT", "pwidth": "PWIDTH", "rise": "RISE", "vrms": "RMS"}
_NOT_ACTIVE = ScpiError(-2244, "Waveform requested is not activated")


class TektronixTbs2104b(ScopeEngine):
    profile = "tektronix-tbs2104b"
    description = "Tektronix TBS2104B, 4 x 100 MHz oscilloscope"
    options: dict = {"signals": signals_option, "noise": real(low=0), "seed": whole(0)}
    manufacturer = "TEKTRONIX"
    model = "TBS2104B"
    serial = "C010001"
    firmware = "CF:91.1CT FV:v1.0.0"
    LSB_PER_DIV = 25.0
    ADC_REF = 128
    MAX_RATE = 1e9
    MAX_DEPTH = 5_000_000
    INTERLEAVED = False
    VOLTS_MIN = 2e-3
    VOLTS_MAX = 5.0
    TIME_MIN = 2e-9
    TIME_MAX = 100.0
    DEFAULT_TIME = 4e-6

    # no SYSTem subsystem: errors are events
    _syst_err = None
    _syst_err_count = None
    _syst_version = None

    def format_number(self, value: float) -> str:
        return f"{value:.4E}"

    def reset(self) -> None:
        super().reset()
        self.headers = True
        self.stop_after = "RUNSTOP"
        self.depth = 2000
        self.acquisitions = 0
        self.data_source = 1
        self.data_start, self.data_stop = 1, 2000
        self.width = 1
        self.encoding = "BINARY"
        self.binary_format = "RI"
        self.msb_first = True
        self.measure_name = "vpp"
        self.measure_source_ = 1

    def _reply(self, header: str, value) -> str:
        text = value if isinstance(value, str) else self.format_number(value) if isinstance(value, float) else str(value)
        return f":{header} {text}" if self.headers else text

    def _ch(self, text: str) -> int:
        word = text.strip().upper()
        if word[:2] != "CH" or word[2:] not in ("1", "2", "3", "4"):
            raise ScpiError(-224)
        return int(word[2:])

    # --- miscellaneous and events ---------------------------------------------------------

    @scpi("HEADer")
    def _header(self, call: Call) -> None:
        self.headers = parse_bool(call.arg(0)) if call.arg(0).strip().upper() in ("ON", "OFF") else \
            number_arg(call, 0) != 0

    @scpi("HEADer?")
    def _header_q(self, call: Call) -> str:
        return self._reply("HEADER", "1" if self.headers else "0")

    def _event(self, error: ScpiError) -> str:
        return f'{abs(error.code)},"{error.message}"'

    @scpi("ALLEv?")
    def _allev(self, call: Call) -> str:
        events = [self._event(e) for e in self._errors] or ['0,"No events to report; queue empty"']
        self._errors.clear()
        return self._reply("ALLEV", ",".join(events))

    @scpi("EVMsg?")
    def _evmsg(self, call: Call) -> str:
        text = self._event(self._errors.pop(0)) if self._errors else '0,"No events to report; queue empty"'
        return self._reply("EVMSG", text)

    @scpi("EVENT?")
    def _event_q(self, call: Call) -> str:
        return self._reply("EVENT", str(abs(self._errors.pop(0).code)) if self._errors else "0")

    @scpi("EVQty?")
    def _evqty(self, call: Call) -> str:
        return self._reply("EVQTY", str(len(self._errors)))

    # --- acquisition -----------------------------------------------------------------------

    @scpi("ACQuire:STATE")
    def _acq_state(self, call: Call) -> None:
        word = call.arg(0).strip().upper()
        if word in ("OFF", "STOP", "0"):
            self.stop()
        elif word in ("ON", "RUN", "1"):
            self.acquisitions = 0
            if self.stop_after == "SEQUENCE":
                self.single()
            else:
                self.run()
        else:
            raise ScpiError(-224)

    @scpi("ACQuire:STATE?")
    def _acq_state_q(self, call: Call) -> str:
        self.poll()
        return self._reply("ACQUIRE:STATE", "1" if self.running else "0")

    @scpi("ACQuire:STOPAfter")
    def _stopafter(self, call: Call) -> None:
        self.stop_after = keyword_arg(call, 0, {"RUNST": "RUNSTOP", "RUNSTOP": "RUNSTOP", "SEQ": "SEQUENCE",
                                                "SEQUENCE": "SEQUENCE"})

    @scpi("ACQuire:STOPAfter?")
    def _stopafter_q(self, call: Call) -> str:
        return self._reply("ACQUIRE:STOPAFTER", self.stop_after)

    @scpi("ACQuire:NUMACq?")
    def _numacq(self, call: Call) -> str:
        self.poll()
        if self._new_acquisition:
            self._new_acquisition = False
            self.acquisitions += 1
        return self._reply("ACQUIRE:NUMACQ", str(self.acquisitions))

    # --- horizontal ------------------------------------------------------------------------

    @scpi("HORizontal[:MAIn]:SCAle", also=("HORizontal[:MAIn]:SECdiv",))
    def _hscale(self, call: Call) -> None:
        self.set_time(number_arg(call, 0))

    @scpi("HORizontal[:MAIn]:SCAle?", also=("HORizontal[:MAIn]:SECdiv?",))
    def _hscale_q(self, call: Call) -> str:
        return self._reply("HORIZONTAL:MAIN:SCALE", self.time_scale)

    @scpi("HORizontal[:MAIn]:SAMPLERate?")
    def _samplerate(self, call: Call) -> str:
        return self._reply("HORIZONTAL:MAIN:SAMPLERATE", self.record()[0])

    @scpi("HORizontal:POSition")
    def _hposition(self, call: Call) -> None:
        self.time_position = number_arg(call, 0)

    @scpi("HORizontal:POSition?")
    def _hposition_q(self, call: Call) -> str:
        return self._reply("HORIZONTAL:POSITION", self.time_position)

    @scpi("HORizontal:RECOrdlength")
    def _record(self, call: Call) -> None:
        self.depth = round(number_arg(call, 0, 1000, self.MAX_DEPTH))

    @scpi("HORizontal:RECOrdlength?")
    def _record_q(self, call: Call) -> str:
        return self._reply("HORIZONTAL:RECORDLENGTH", str(self.depth))

    # --- vertical ------------------------------------------------------------------------------

    @scpi("CH#:SCAle", also=("CH#:VOLts",))
    def _ch_scale(self, call: Call) -> None:
        self.set_volts(self.channel_number(call.suffixes[0]), number_arg(call, 0))

    @scpi("CH#:SCAle?", also=("CH#:VOLts?",))
    def _ch_scale_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return self._reply(f"CH{number}:SCALE", self.channels[number].scale)

    @scpi("CH#:OFFSet")
    def _ch_offset(self, call: Call) -> None:
        self.channel(call.suffixes[0]).offset = number_arg(call, 0)

    @scpi("CH#:OFFSet?")
    def _ch_offset_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return self._reply(f"CH{number}:OFFSET", self.channels[number].offset)

    @scpi("CH#:POSition")
    def _ch_position(self, call: Call) -> None:
        self.channel(call.suffixes[0]).position = number_arg(call, 0, -5, 5)

    @scpi("CH#:POSition?")
    def _ch_position_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return self._reply(f"CH{number}:POSITION", self.channels[number].position)

    @scpi("CH#:COUPling")
    def _ch_coupling(self, call: Call) -> None:
        self.channel(call.suffixes[0]).coupling = keyword_arg(call, 0, {"DC": "DC", "AC": "AC", "GND": "GND"})

    @scpi("CH#:COUPling?")
    def _ch_coupling_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return self._reply(f"CH{number}:COUPLING", self.channels[number].coupling)

    @scpi("CH#:PRObe")
    def _ch_probe(self, call: Call) -> None:
        self.set_probe(self.channel_number(call.suffixes[0]), number_arg(call, 0))

    @scpi("CH#:PRObe?")
    def _ch_probe_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return self._reply(f"CH{number}:PROBE", self.channels[number].probe)

    @scpi("SELect:CH#")
    def _select(self, call: Call) -> None:
        word = call.arg(0).strip().upper()
        self.channel(call.suffixes[0]).on = parse_bool(word) if word in ("ON", "OFF") else number_arg(call, 0) != 0

    @scpi("SELect:CH#?")
    def _select_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return self._reply(f"SELECT:CH{number}", "1" if self.channels[number].on else "0")

    # --- trigger -----------------------------------------------------------------------------------

    @scpi("TRIGger:A:MODe")
    def _trig_mode(self, call: Call) -> None:
        self.sweep = "AUTO" if keyword_arg(call, 0, spellings("AUTO", "NORMal")) == "AUTO" else "NORMAL"

    @scpi("TRIGger:A:MODe?")
    def _trig_mode_q(self, call: Call) -> str:
        return self._reply("TRIGGER:A:MODE", "AUTO" if self.sweep == "AUTO" else "NORMAL")

    @scpi("TRIGger:A:LEVel")
    def _trig_level(self, call: Call) -> None:
        self.trigger_level = _level(call, 0)

    @scpi("TRIGger:A:LEVel?")
    def _trig_level_q(self, call: Call) -> str:
        return self._reply("TRIGGER:A:LEVEL", self.trigger_level)

    @scpi("TRIGger:A:LEVel:CH#")
    def _trig_level_ch(self, call: Call) -> None:
        if self.channel_number(call.suffixes[0]) == self.trigger_source:
            self.trigger_level = _level(call, 0)

    @scpi("TRIGger:A:LEVel:CH#?")
    def _trig_level_ch_q(self, call: Call) -> str:
        return self._reply(f"TRIGGER:A:LEVEL:CH{self.channel_number(call.suffixes[0])}", self.trigger_level)

    @scpi("TRIGger:A:EDGE:SOUrce")
    def _trig_source(self, call: Call) -> None:
        self.trigger_source = self._ch(call.arg(0))

    @scpi("TRIGger:A:EDGE:SOUrce?")
    def _trig_source_q(self, call: Call) -> str:
        return self._reply("TRIGGER:A:EDGE:SOURCE", f"CH{self.trigger_source}")

    @scpi("TRIGger:A:EDGE:SLOpe")
    def _trig_slope(self, call: Call) -> None:
        self.trigger_slope = keyword_arg(call, 0, {"RIS": "POS", "RISE": "POS", "FALL": "NEG"})

    @scpi("TRIGger:A:EDGE:SLOpe?")
    def _trig_slope_q(self, call: Call) -> str:
        return self._reply("TRIGGER:A:EDGE:SLOPE", "RISE" if self.trigger_slope == "POS" else "FALL")

    @scpi("TRIGger:FORCe", max_args=0)
    def _trig_force(self, call: Call) -> None:
        self.force_trigger()

    @scpi("TRIGger:STATE?")
    def _trig_state(self, call: Call) -> str:
        state = {"stop": "SAVE", "triggered": "TRIGGER", "auto": "AUTO", "wait": "READY"}[self.status]
        return self._reply("TRIGGER:STATE", state)

    # --- waveform transfer ----------------------------------------------------------------------------

    @scpi("DATa")
    def _data(self, call: Call) -> None:
        if call.arg(0).strip().upper() not in ("INIT", "SNAP", "SNA"):
            raise ScpiError(-224)
        if call.arg(0).strip().upper() == "INIT":
            self.data_source, self.data_start, self.data_stop, self.width = 1, 1, self.depth or self.MAX_DEPTH, 1
            self.encoding, self.binary_format = "BINARY", "RI"

    @scpi("DATa:SOUrce")
    def _data_source(self, call: Call) -> None:
        self.data_source = self._ch(call.arg(0))

    @scpi("DATa:SOUrce?")
    def _data_source_q(self, call: Call) -> str:
        return self._reply("DATA:SOURCE", f"CH{self.data_source}")

    @scpi("DATa:STARt")
    def _data_start_cmd(self, call: Call) -> None:
        self.data_start = round(number_arg(call, 0, 1, self.MAX_DEPTH))

    @scpi("DATa:STARt?")
    def _data_start_q(self, call: Call) -> str:
        return self._reply("DATA:START", str(self.data_start))

    @scpi("DATa:STOP")
    def _data_stop_cmd(self, call: Call) -> None:
        self.data_stop = round(number_arg(call, 0, 1, self.MAX_DEPTH))

    @scpi("DATa:STOP?")
    def _data_stop_q(self, call: Call) -> str:
        return self._reply("DATA:STOP", str(self.data_stop))

    @scpi("DATa:WIDth", also=("WFMOutpre:BYT_Nr",))
    def _width(self, call: Call) -> None:
        self.width = round(number_arg(call, 0, 1, 2))

    @scpi("DATa:WIDth?")
    def _width_q(self, call: Call) -> str:
        return self._reply("DATA:WIDTH", str(self.width))

    @scpi("WFMOutpre:BYT_Nr?")
    def _byt_nr(self, call: Call) -> str:
        return self._reply("WFMOUTPRE:BYT_NR", str(self.width))

    @scpi("WFMOutpre:BIT_Nr")
    def _bit_nr_set(self, call: Call) -> None:
        bits = round(number_arg(call, 0, 8, 16))
        if bits not in (8, 16):
            raise ScpiError(-222)
        self.width = bits // 8

    @scpi("WFMOutpre:BIT_Nr?")
    def _bit_nr(self, call: Call) -> str:
        return self._reply("WFMOUTPRE:BIT_NR", str(self.width * 8))

    @scpi("WFMOutpre:ENCdg")
    def _encdg(self, call: Call) -> None:
        self.encoding = keyword_arg(call, 0, {"ASC": "ASCII", "ASCII": "ASCII", "BIN": "BINARY", "BINARY": "BINARY"})

    @scpi("WFMOutpre:ENCdg?")
    def _encdg_q(self, call: Call) -> str:
        return self._reply("WFMOUTPRE:ENCDG", self.encoding)

    @scpi("WFMOutpre:BN_Fmt")
    def _bn_fmt(self, call: Call) -> None:
        self.binary_format = keyword_arg(call, 0, {"RI": "RI", "RP": "RP"})

    @scpi("WFMOutpre:BN_Fmt?")
    def _bn_fmt_q(self, call: Call) -> str:
        return self._reply("WFMOUTPRE:BN_FMT", self.binary_format)

    @scpi("WFMOutpre:BYT_Or")
    def _byt_or(self, call: Call) -> None:
        self.msb_first = keyword_arg(call, 0, {"MSB": "M", "LSB": "L"}) == "M"

    @scpi("WFMOutpre:BYT_Or?")
    def _byt_or_q(self, call: Call) -> str:
        return self._reply("WFMOUTPRE:BYT_OR", "MSB" if self.msb_first else "LSB")

    def _window(self, capture: Capture) -> tuple[int, int]:
        """(first point, count) of the transfer."""
        stop = min(self.data_stop, capture.points)
        if self.data_start > stop:
            raise ScpiError(-222)
        return self.data_start - 1, stop - self.data_start + 1

    def _displayed_capture(self) -> Capture | None:
        """The capture of the data source; if the source is off, queue the event and answer nothing."""
        capture = self.current_capture()
        if not capture.traces[self.data_source].on:
            self.push_error(_NOT_ACTIVE)
            return None
        return capture

    def _y(self, capture: Capture) -> tuple[float, float, float]:
        """(YMUlt, YOFf, YZEro): value = (code - YOFf) x YMUlt + YZEro."""
        channel = self.channels[self.data_source]
        trace = capture.traces[self.data_source]
        zero = channel.offset
        levels = (zero - trace.center) / trace.lsb  # CH<x>:POSition, in digitizing levels
        if self.binary_format == "RP":
            levels += self.ADC_REF
        factor = 256 if self.width == 2 else 1
        return trace.lsb / factor, levels * factor, zero

    def _wfid(self, capture: Capture) -> str:
        trace = capture.traces[self.data_source]
        channel = self.channels[self.data_source]
        return (f'"Ch{self.data_source}, {channel.coupling} coupling, {trace.lsb * self.LSB_PER_DIV:.4E}V/div, '
                f'{capture.window / self.DIVS_X:.4E}s/div, {capture.points} points, Sample mode"')

    @scpi("WFMOutpre?")
    def _wfmoutpre(self, call: Call) -> str:
        fields = [("BYT_NR", str(self.width)), ("BIT_NR", str(self.width * 8)), ("ENCDG", self.encoding),
                  ("BN_FMT", self.binary_format), ("BYT_OR", "MSB" if self.msb_first else "LSB")]
        capture = self.current_capture()
        if capture.traces[self.data_source].on:
            first, count = self._window(capture)
            ymult, yoff, yzero = self._y(capture)
            fields += [("WFID", self._wfid(capture)), ("NR_PT", str(count)), ("PT_FMT", "Y"), ("XUNIT", '"s"'),
                       ("XINCR", f"{capture.dt:.4E}"), ("XZERO", f"{capture.x0 + first * capture.dt:.4E}"),
                       ("PT_OFF", "0"), ("YUNIT", '"V"'), ("YMULT", f"{ymult:.4E}"), ("YOFF", f"{yoff:.4E}"),
                       ("YZERO", f"{yzero:.4E}")]
        if self.headers:
            return ":WFMOUTPRE:" + ";".join(f"{name} {value}" for name, value in fields)
        return ";".join(value for _, value in fields)

    def _pre_field(self, header: str, value) -> str:
        return self._reply(f"WFMOUTPRE:{header}", value)

    @scpi("WFMOutpre:NR_Pt?")
    def _nr_pt(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("NR_PT", str(self._window(capture)[1]))

    @scpi("WFMOutpre:RECOrdlength?")
    def _recordlength(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("RECORDLENGTH", str(capture.points))

    @scpi("WFMOutpre:XINcr?")
    def _xincr(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("XINCR", capture.dt)

    @scpi("WFMOutpre:XZEro?")
    def _xzero(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        if capture is None:
            return None
        return self._pre_field("XZERO", capture.x0 + self._window(capture)[0] * capture.dt)

    @scpi("WFMOutpre:XUNit?")
    def _xunit(self, call: Call) -> str:
        return self._pre_field("XUNIT", '"s"')

    @scpi("WFMOutpre:YUNit?")
    def _yunit(self, call: Call) -> str:
        return self._pre_field("YUNIT", '"V"')

    @scpi("WFMOutpre:PT_Off?")
    def _pt_off(self, call: Call) -> str:
        return self._pre_field("PT_OFF", "0")

    @scpi("WFMOutpre:YMUlt?")
    def _ymult(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("YMULT", self._y(capture)[0])

    @scpi("WFMOutpre:YOFf?")
    def _yoff(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("YOFF", self._y(capture)[1])

    @scpi("WFMOutpre:YZEro?")
    def _yzero(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("YZERO", self._y(capture)[2])

    @scpi("WFMOutpre:WFId?")
    def _wfid_q(self, call: Call) -> str | None:
        capture = self._displayed_capture()
        return None if capture is None else self._pre_field("WFID", self._wfid(capture))

    @scpi("CURVe?")
    def _curve(self, call: Call) -> bytes | str | None:
        capture = self.current_capture()
        number = self.data_source
        if not capture.traces[number].on:  # the manual: returns nothing, events 2244 and 420
            self.push_error(_NOT_ACTIVE)
            self.push_error(ScpiError(-420, "Query UNTERMINATED"))
            return None
        first, count = self._window(capture)
        codes = self.read_codes(capture, number, first, count, capture.dt)
        signed = self.binary_format == "RI"
        values = [code - int(self.ADC_REF) for code in codes] if signed else codes
        prefix = b":CURVE " if self.headers else b""
        if self.encoding == "ASCII":
            factor = 256 if self.width == 2 else 1
            return prefix.decode() + ",".join(str(v * factor) for v in values)
        if self.width == 1:
            return prefix + block(bytes(v & 0xFF for v in values))
        order = "big" if self.msb_first else "little"
        return prefix + block(b"".join((v * 256).to_bytes(2, order, signed=signed) for v in values))

    # --- measurements ---------------------------------------------------------------------------------

    @scpi("MEASUrement:IMMed:TYPe")
    def _meas_type(self, call: Call) -> None:
        entry = _MEASURES.get(call.arg(0).strip().upper())
        if entry is None:
            raise ScpiError(-224)
        self.measure_name = entry[0]

    @scpi("MEASUrement:IMMed:TYPe?")
    def _meas_type_q(self, call: Call) -> str:
        return self._reply("MEASUREMENT:IMMED:TYPE", _NAMES[self.measure_name])

    @scpi("MEASUrement:IMMed:SOUrce#")
    def _meas_src(self, call: Call) -> None:
        if call.suffixes[0] not in (None, 1):  # SOUrce2 is for the delay measurements, not simulated
            raise ScpiError(-114)
        self.measure_source_ = self._ch(call.arg(0))

    @scpi("MEASUrement:IMMed:SOUrce#?")
    def _meas_src_q(self, call: Call) -> str:
        return self._reply("MEASUREMENT:IMMED:SOURCE1", f"CH{self.measure_source_}")

    @scpi("MEASUrement:IMMed:VALue?")
    def _meas_value(self, call: Call) -> str:
        value = self.measure_value(self.measure_source_, self.measure_name)
        unit = next(u for name, u in _MEASURES.values() if name == self.measure_name)
        if unit == "%" and value != NO_VALUE:
            value *= 100
        return self._reply("MEASUREMENT:IMMED:VALUE", value)

    @scpi("MEASUrement:IMMed:UNIts?")
    def _meas_units(self, call: Call) -> str:
        unit = next(u for name, u in _MEASURES.values() if name == self.measure_name)
        return self._reply("MEASUREMENT:IMMED:UNITS", f'"{unit}"')


def _level(call: Call, i: int) -> float:
    word = call.arg(i).strip().upper()
    if word == "TTL":
        return 1.4
    if word == "ECL":
        return -1.3
    return number_arg(call, i)
