"""The Python side of the browser demo: the example bench, driven message by message.

Runs in Pyodide next to the scpi-sim wheel. The page calls ``send``, ``inject``
and ``reset`` and draws the instrument fronts from the returned state, so every
value it shows comes from the simulator itself. No sockets: the bench is never
started, messages go straight to ``Instrument.process``.

The state reads a few private attributes of the HMP4040, DAQ970A and DS1054Z models;
the demo is always built from the same release as the package, and
web/tests/test_demo.py pins what it relies on.
"""

from __future__ import annotations

from scpi_sim.bench import Bench

# button name -> (instrument, fault kind, parameters)
FAULTS: dict[str, tuple[str, str, dict]] = {
    "daq-no-response": ("daq", "no_response", {"count": 1}),
    "daq-offset": ("daq", "offset", {"value": 0.5}),
    "psu-error": ("psu", "error", {"code": -222}),
    "scope-no-trigger": ("scope", "no_trigger", {}),
    "scope-noise": ("scope", "noise", {"sigma": 0.1}),
    "scope-probe-off": ("scope", "signal", {"channel": 1, "shape": "dc", "vpp": 0}),
}
MAX_LINE = 256
MAX_RESPONSE = 400  # characters of a long text answer shown in the terminal
TARGETS = ("psu", "daq", "scope")
SCREEN_POINTS = 600  # per trace on the scope screen
# so the screen shows something after power-on; *RST typed by hand gives the real default, 1 us/div
SCOPE_SETUP = ":TIM:SCAL 0.0002;:CHAN1:SCAL 0.5"


def _clean(line: str) -> str:
    return "".join(ch for ch in line[:MAX_LINE] if ch == "\t" or ch.isprintable())


def _printable(response: str | bytes | None) -> str | None:
    """What the terminal shows: a binary block by its size, a long answer shortened."""
    if isinstance(response, bytes):
        start = response.find(b"#")
        digits = int(response[start + 1:start + 2])
        length = int(response[start + 2:start + 2 + digits])
        head = response[:start + 2 + digits].decode()
        return f"{head}<{length} bytes of binary data>"
    if response is not None and len(response) > MAX_RESPONSE:
        return f"{response[:MAX_RESPONSE]}… ({len(response)} characters)"
    return response


class Demo:
    def __init__(self, bench_path: str = "bench.yaml"):
        self.bench = Bench.from_file(bench_path)
        self.psu = self.bench.instruments["psu"]
        self.daq = self.bench.instruments["daq"]
        self.scope = self.bench.instruments["scope"]
        self.scope.process(SCOPE_SETUP)

    def _instrument(self, target: str):
        if target not in TARGETS:
            raise ValueError(f"unknown instrument {target!r} (allowed: {', '.join(TARGETS)})")
        return self.bench.instruments[target]

    def send(self, target: str, line: str) -> dict:
        """Send one program message; a query without an answer is a timeout, as in PyVISA."""
        instrument = self._instrument(target)
        line = _clean(line)
        response = instrument.process(line)
        return {"response": _printable(response), "timeout": response is None and "?" in line, "state": self.state()}

    def inject(self, fault: str) -> dict:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r} (allowed: {', '.join(FAULTS)})")
        target, kind, params = FAULTS[fault]
        self._instrument(target).inject(kind, **params)
        return self.state()

    def reset(self) -> dict:
        for target in TARGETS:
            instrument = self._instrument(target)
            instrument.clear_faults()
            instrument.process("*RST;*CLS")
        self.scope.process(SCOPE_SETUP)
        return self.state()

    def state(self) -> dict:
        return {"psu": self._psu_state(), "daq": self._daq_state(), "scope": self.scope_state()}

    def _psu_state(self) -> dict:
        psu = self.psu
        channels = []
        for number, (channel, active) in enumerate(zip(psu._channels, psu._active), start=1):
            out_v, out_a, mode = psu.output(number)
            channels.append({"set_v": channel.volts, "set_a": channel.amps, "active": active,
                             "out_v": out_v, "out_a": out_a, "mode": mode})
        return {"model": psu.model, "selected": psu._selected, "general": psu._general,
                "error": bool(psu._errors), "faults": sorted(psu.faults), "channels": channels}

    def _daq_state(self) -> dict:
        daq = self.daq
        last = None
        if daq._readings:
            last = {"channel": daq._scan[len(daq._readings) - 1], "value": daq._readings[-1]}
        return {"model": daq.model, "closed": sorted(daq.closed), "last": last,
                "error": bool(daq._errors), "faults": sorted(daq.faults)}

    def scope_state(self) -> dict:
        """The screen: every trace of the latest capture, in divisions from center screen."""
        scope = self.scope
        status = scope.status  # brings the capture up to date
        capture = scope._capture
        traces = []
        if capture is not None:
            dt = capture.window / SCREEN_POINTS
            for number, trace in capture.traces.items():
                if trace.on:
                    codes = scope.read_codes(capture, number, 0, SCREEN_POINTS, dt)
                    divisions = [round((code - scope.ADC_REF) / scope.LSB_PER_DIV, 2) for code in codes]
                    traces.append({"channel": number, "divisions": divisions, "scale": trace.lsb * scope.LSB_PER_DIV})
        source = scope.channels[scope.trigger_source]
        return {"model": scope.model, "status": status, "traces": traces, "time_scale": scope.time_scale,
                "trigger": {"source": scope.trigger_source, "level": scope.trigger_level, "slope": scope.trigger_slope,
                            "divisions": (scope.trigger_level - scope.center(source)) / source.scale},
                "columns": scope.DIVS_X, "error": bool(scope._errors), "faults": sorted(scope.faults)}
