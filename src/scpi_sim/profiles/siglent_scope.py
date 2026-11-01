"""Siglent SDS1104X-E oscilloscope.

Source: Siglent Digital Oscilloscopes Programming Guide, EN02E (SDS1000 series, SDS2000X,
SDS2000X-E), November 2025; the waveform example for the SDS1000X-E series.

Siglent's own dialect: ``C1:VDIV 500mV``, answers repeat the command and carry their unit
(``C1:VDIV 5.00E-01V``, ``TDIV 5.00E-09S``, ``SARA 1.00E+09Sa/s``, ``C2:PAVA RISE,3.600000E-9S``).
Long and short names both work (``VOLT_DIV``/``VDIV``). No ``SYSTem:ERRor?``; errors are visible in
``*ESR?``.

Simulated: ``ARM``, ``STOP``, ``TRMD``, ``SAST?``, ``INR?``, ``SARA?``, ``SANU?``, ``MSIZ``, ``TDIV``, ``TRDL``;
``C<n>:VDIV``, ``:OFST``, ``:ATTN``, ``:CPL``, ``:TRA``; ``C<n>:TRLV``, ``:TRSL``; ``TRSE`` (edge only);
``WFSU``; ``C<n>:WF? DAT2``; ``C<n>:PAVA? <parameter>``. As in the guide: the answer to ``C1:WF? DAT2`` is
``C1:WF DAT2,#9<length><codes>`` plus the two newlines that end it (first data point is byte 23);
signed codes, volts = code x (vdiv / 25) - voffset; time = -(tdiv x 14 / 2) + index / rate,
14 divisions; memory 7K-7M, or 14K-14M with one channel of a pair on; ``SP``, ``NP``, ``FP``.

Assumptions: ``****`` for a measurement without a result (not in this guide; reported by users of
these scopes); ``MSIZ`` 14K after *RST so a transfer stays small (the real default may differ); serial
number and firmware.

Not simulated: ``CHDR`` (headers always on), math, digital channels, other trigger types, ``PACU``,
statistics, ``MEAD?``, the ``:FORMat:DATA`` digits, screen dumps.
"""

from __future__ import annotations

from ..errors import ScpiError
from ..instrument import Call, scpi
from ..instruments.scope import Capture, NO_VALUE, ScopeEngine, keyword_arg, number_arg, signals_option
from ..options import real, whole
from ..scpi.params import parse_bool

# parameter -> (measurement, unit, factor)
_PARAMETERS = {
    "PKPK": ("vpp", "V", 1), "MAX": ("vmax", "V", 1), "MIN": ("vmin", "V", 1), "AMPL": ("vamp", "V", 1),
    "TOP": ("vtop", "V", 1), "BASE": ("vbase", "V", 1), "MEAN": ("vavg", "V", 1), "RMS": ("vrms", "V", 1),
    "STDEV": ("stddev", "V", 1), "OVSP": ("overshoot", "%", 100), "RPRE": ("preshoot", "%", 100),
    "PER": ("period", "S", 1), "FREQ": ("frequency", "Hz", 1), "PWID": ("pwidth", "S", 1),
    "NWID": ("nwidth", "S", 1), "RISE": ("rise", "S", 1), "FALL": ("fall", "S", 1),
    "DUTY": ("pduty", "%", 100), "NDUTY": ("nduty", "%", 100),
}
_COUPLINGS = {"D1M": "DC", "D50": "DC", "A1M": "AC", "A50": "AC", "GND": "GND"}
_SIZES = {"7K": 7_000, "70K": 70_000, "700K": 700_000, "7M": 7_000_000,
          "14K": 14_000, "140K": 140_000, "1.4M": 1_400_000, "14M": 14_000_000}
_INR_NEW_ACQUISITION, _INR_TRIGGER_READY = 1, 8192


class SiglentSds1104xe(ScopeEngine):
    profile = "siglent-sds1104x-e"
    description = "Siglent SDS1104X-E, 4 x 100 MHz oscilloscope"
    options: dict = {"signals": signals_option, "noise": real(low=0), "seed": whole(0)}
    manufacturer = "Siglent Technologies"
    model = "SDS1104X-E"
    serial = "SDS1EBAX000001"
    firmware = "8.2.6.1.37R9"
    DIVS_X = 14
    LSB_PER_DIV = 25.0
    ADC_REF = 128
    MAX_RATE = 1e9
    MAX_DEPTH = 14_000_000
    VOLTS_MIN = 500e-6
    VOLTS_MAX = 10.0
    TIME_MIN = 1e-9
    TIME_MAX = 100.0
    SNAP_TIME = True
    OFFSET_SIGN = -1
    DEFAULT_TIME = 1e-6
    PROBES = (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)

    # no SYSTem subsystem in the guide
    _syst_err = None
    _syst_err_count = None
    _syst_version = None

    def reset(self) -> None:
        super().reset()
        self.depth = _SIZES["14K"]
        self.mode = "AUTO"
        self.sparse, self.count, self.first = 0, 0, 0
        self.coupling_names = {n: "D1M" for n in self.channels}

    def _channel_share(self) -> int:
        """Channels 1/2 and 3/4 each share a converter."""
        both = (self.channels[1].on and self.channels[2].on) or (self.channels[3].on and self.channels[4].on)
        return 2 if both else 1

    def _trace_name(self, suffix: int | None) -> str:
        return f"C{self.channel_number(suffix)}"

    # --- acquisition ------------------------------------------------------------------------

    @scpi("ARM", max_args=0, also=("ARM_ACQUISITION",))
    def _arm(self, call: Call) -> None:
        if self.mode == "SINGLE":
            self.single()
        else:
            self.run()

    @scpi("STOP", max_args=0)
    def _stop_cmd(self, call: Call) -> None:
        self.stop()

    @scpi("TRMD", also=("TRIG_MODE",))
    def _trmd(self, call: Call) -> None:
        self.mode = keyword_arg(call, 0, {"AUTO": "AUTO", "NORM": "NORM", "SINGLE": "SINGLE", "STOP": "STOP"})
        if self.mode == "STOP":
            self.stop()
        elif self.mode == "SINGLE":
            self.single()
        else:
            self.sweep = "AUTO" if self.mode == "AUTO" else "NORMAL"
            self.run()

    @scpi("TRMD?", also=("TRIG_MODE?",))
    def _trmd_q(self, call: Call) -> str:
        return f"TRMD {self.mode}"

    @scpi("SAST?", also=("SAMPLE_STATUS?",))
    def _sast(self, call: Call) -> str:
        return "SAST " + {"stop": "Stop", "triggered": "Trig'd", "auto": "Auto", "wait": "Ready"}[self.status]

    @scpi("INR?")
    def _inr(self, call: Call) -> str:
        self.poll()
        value = _INR_NEW_ACQUISITION if self._new_acquisition else 0
        self._new_acquisition = False
        if self.running:
            value |= _INR_TRIGGER_READY
        return f"INR {value}"

    @scpi("SARA?", also=("SAMPLE_RATE?",))
    def _sara(self, call: Call) -> str:
        return f"SARA {self.record()[0]:.2E}Sa/s"

    @scpi("SANU?", max_args=1, also=("SAMPLE_NUM?",))
    def _sanu(self, call: Call) -> str:
        return f"SANU {self.record()[1]:.2E}pts"

    @scpi("MSIZ", also=("MEMORY_SIZE",))
    def _msiz(self, call: Call) -> None:
        size = _SIZES.get(call.arg(0).strip().upper())
        if size is None:
            raise ScpiError(-224)
        self.depth = size

    @scpi("MSIZ?", also=("MEMORY_SIZE?",))
    def _msiz_q(self, call: Call) -> str:
        return "MSIZ " + next(name for name, size in _SIZES.items() if size == self.depth)

    # --- timebase --------------------------------------------------------------------------

    @scpi("TDIV", also=("TIME_DIV",))
    def _tdiv(self, call: Call) -> None:
        self.set_time(number_arg(call, 0, unit="S"))

    @scpi("TDIV?", also=("TIME_DIV?",))
    def _tdiv_q(self, call: Call) -> str:
        return f"TDIV {self.time_scale:.2E}S"

    @scpi("TRDL", also=("TRIG_DELAY",))
    def _trdl(self, call: Call) -> None:
        self.time_position = number_arg(call, 0, unit="S")

    @scpi("TRDL?", also=("TRIG_DELAY?",))
    def _trdl_q(self, call: Call) -> str:
        return f"TRDL {self.time_position:.2E}S"

    # --- channels -----------------------------------------------------------------------------

    @scpi("C#:VDIV", also=("C#:VOLT_DIV",))
    def _vdiv(self, call: Call) -> None:
        self.set_volts(self.channel_number(call.suffixes[0]), number_arg(call, 0, unit="V"))

    @scpi("C#:VDIV?", also=("C#:VOLT_DIV?",))
    def _vdiv_q(self, call: Call) -> str:
        return f"{self._trace_name(call.suffixes[0])}:VDIV {self.channel(call.suffixes[0]).scale:.2E}V"

    @scpi("C#:OFST", also=("C#:OFFSET",))
    def _ofst(self, call: Call) -> None:
        self.channel(call.suffixes[0]).offset = number_arg(call, 0, unit="V")

    @scpi("C#:OFST?", also=("C#:OFFSET?",))
    def _ofst_q(self, call: Call) -> str:
        return f"{self._trace_name(call.suffixes[0])}:OFST {self.channel(call.suffixes[0]).offset:.2E}V"

    @scpi("C#:ATTN", also=("C#:ATTENUATION",))
    def _attn(self, call: Call) -> None:
        self.set_probe(self.channel_number(call.suffixes[0]), number_arg(call, 0))

    @scpi("C#:ATTN?", also=("C#:ATTENUATION?",))
    def _attn_q(self, call: Call) -> str:
        return f"{self._trace_name(call.suffixes[0])}:ATTN {self.channel(call.suffixes[0]).probe:g}"

    @scpi("C#:CPL", also=("C#:COUPLING",))
    def _cpl(self, call: Call) -> None:
        number = self.channel_number(call.suffixes[0])
        name = call.arg(0).strip().upper()
        if name not in _COUPLINGS:
            raise ScpiError(-224)
        self.coupling_names[number] = name
        self.channels[number].coupling = _COUPLINGS[name]

    @scpi("C#:CPL?", also=("C#:COUPLING?",))
    def _cpl_q(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        return f"C{number}:CPL {self.coupling_names[number]}"

    @scpi("C#:TRA", also=("C#:TRACE",))
    def _tra(self, call: Call) -> None:
        self.channel(call.suffixes[0]).on = parse_bool(call.arg(0))

    @scpi("C#:TRA?", also=("C#:TRACE?",))
    def _tra_q(self, call: Call) -> str:
        return f"{self._trace_name(call.suffixes[0])}:TRA {'ON' if self.channel(call.suffixes[0]).on else 'OFF'}"

    # --- trigger -----------------------------------------------------------------------------------

    @scpi("C#:TRLV", also=("C#:TRIG_LEVEL",))
    def _trlv(self, call: Call) -> None:
        number = self.channel_number(call.suffixes[0])
        level = number_arg(call, 0, unit="V")
        if number == self.trigger_source:
            self.trigger_level = level

    @scpi("C#:TRLV?", also=("C#:TRIG_LEVEL?",))
    def _trlv_q(self, call: Call) -> str:
        return f"{self._trace_name(call.suffixes[0])}:TRLV {self.trigger_level:.2E}V"

    @scpi("C#:TRSL", also=("C#:TRIG_SLOPE",))
    def _trsl(self, call: Call) -> None:
        self.channel_number(call.suffixes[0])
        self.trigger_slope = keyword_arg(call, 0, {"POS": "POS", "NEG": "NEG"})

    @scpi("C#:TRSL?", also=("C#:TRIG_SLOPE?",))
    def _trsl_q(self, call: Call) -> str:
        return f"{self._trace_name(call.suffixes[0])}:TRSL {self.trigger_slope}"

    @scpi("TRSE", max_args=8, also=("TRIG_SELECT",))
    def _trse(self, call: Call) -> None:
        args = [arg.strip().upper() for arg in call.args]
        if not args or args[0] != "EDGE":
            raise ScpiError(-221, "Settings conflict; only the edge trigger is simulated")
        if "SR" in args:
            source = args[args.index("SR") + 1] if args.index("SR") + 1 < len(args) else ""
            if source not in ("C1", "C2", "C3", "C4"):
                raise ScpiError(-224)
            self.trigger_source = int(source[1])

    @scpi("TRSE?", also=("TRIG_SELECT?",))
    def _trse_q(self, call: Call) -> str:
        return f"TRSE EDGE,SR,C{self.trigger_source},HT,OFF"

    # --- waveform -----------------------------------------------------------------------------------

    @scpi("WFSU", max_args=6, also=("WAVEFORM_SETUP",))
    def _wfsu(self, call: Call) -> None:
        if len(call.args) % 2 or not call.args:
            raise ScpiError(-109)
        values = {"SP": self.sparse, "NP": self.count, "FP": self.first}
        for key, value in zip(call.args[0::2], call.args[1::2]):
            key = key.strip().upper()
            if key not in values or not value.strip().isdigit():
                raise ScpiError(-224)
            values[key] = int(value)
        self.sparse, self.count, self.first = values["SP"], values["NP"], values["FP"]

    @scpi("WFSU?", also=("WAVEFORM_SETUP?",))
    def _wfsu_q(self, call: Call) -> str:
        return f"WFSU SP,{self.sparse},NP,{self.count},FP,{self.first}"

    @scpi("C#:WF?", max_args=1, also=("C#:WAVEFORM?",))
    def _wf(self, call: Call) -> bytes:
        number = self.channel_number(call.suffixes[0])
        if call.arg(0).strip().upper() != "DAT2":
            raise ScpiError(-224)
        capture = self.current_capture()
        if not capture.traces[number].on:
            raise ScpiError(-221, "Settings conflict; trace is off")
        data = self._codes_for_transfer(capture, number)
        return f"C{number}:WF DAT2,#9{len(data):09d}".encode() + data + b"\n"  # the transport adds the second one

    def _codes_for_transfer(self, capture: Capture, number: int) -> bytes:
        step = max(1, self.sparse)
        available = max(0, capture.points - self.first)
        count = (available + step - 1) // step
        if self.count:
            count = min(count, self.count)
        dt = capture.dt * step
        codes = self.read_codes(capture, number, 0, count, dt, capture.x0 + self.first * capture.dt)
        return bytes((code - 128) & 0xFF for code in codes)

    # --- measurements -------------------------------------------------------------------------------

    @scpi("C#:PAVA?", max_args=1, also=("C#:PARAMETER_VALUE?",))
    def _pava(self, call: Call) -> str:
        number = self.channel_number(call.suffixes[0])
        parameter = call.arg(0).strip().upper()
        if parameter == "ALL":
            results = self.measurements(number)
            return f"C{number}:PAVA " + ",".join(_entry(name, results[spec[0]], spec)
                                                 for name, spec in _PARAMETERS.items())
        spec = _PARAMETERS.get(parameter)
        if spec is None:
            raise ScpiError(-224)
        value = self.measure_value(number, spec[0])
        return f"C{number}:PAVA {_entry(parameter, None if value == NO_VALUE else value, spec)}"


def _entry(name: str, value: float | None, spec: tuple[str, str, int]) -> str:
    if value is None:
        return f"{name},****"
    return f"{name},{value * spec[2]:.6E}{spec[1]}"
