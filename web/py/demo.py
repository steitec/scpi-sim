"""The Python side of the browser demo: the example bench, driven message by message.

Runs in Pyodide next to the scpi-sim wheel. The page calls ``send``, ``inject``
and ``reset`` and draws the instrument fronts from the returned state, so every
value it shows comes from the simulator itself. No sockets: the bench is never
started, messages go straight to ``Instrument.process``.

The state reads a few private attributes of the HMP4040 and DAQ970A models;
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
}
MAX_LINE = 256
TARGETS = ("psu", "daq")


def _clean(line: str) -> str:
    return "".join(ch for ch in line[:MAX_LINE] if ch == "\t" or ch.isprintable())


class Demo:
    def __init__(self, bench_path: str = "bench.yaml"):
        self.bench = Bench.from_file(bench_path)
        self.psu = self.bench.instruments["psu"]
        self.daq = self.bench.instruments["daq"]

    def _instrument(self, target: str):
        if target not in TARGETS:
            raise ValueError(f"unknown instrument {target!r} (allowed: {', '.join(TARGETS)})")
        return self.bench.instruments[target]

    def send(self, target: str, line: str) -> dict:
        """Send one program message; a query without an answer is a timeout, as in PyVISA."""
        instrument = self._instrument(target)
        line = _clean(line)
        response = instrument.process(line)
        return {"response": response, "timeout": response is None and "?" in line, "state": self.state()}

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
        return self.state()

    def state(self) -> dict:
        return {"psu": self._psu_state(), "daq": self._daq_state()}

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
