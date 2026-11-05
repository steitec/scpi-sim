"""A digital oscilloscope: signals at the inputs, an 8-bit ADC, edge trigger, acquisitions, measurements.

``ScopeEngine`` is the instrument without a command set: every real model speaks its
own dialect, and a Tektronix must not understand Keysight's ``:WAV:DATA?``. The
dialects are subclasses. ``Oscilloscope`` is the generic one (the ``:WAVeform``,
``:MEASure`` and ``:TRIGger`` tree that Keysight InfiniiVision scopes use).

What makes it behave like a real scope:

- **Acquisitions are snapshots.** A stopped scope keeps answering with its last
  capture, and the scale in the preamble is the one the capture was taken with.
  A running scope takes a new capture for every query.
- **The trigger is real.** It looks for the level in the signal. A level the signal
  never crosses means no trigger: Auto sweep runs unsynchronised, Normal keeps the
  old capture, Single waits (``*OPC?`` gets no answer, so the client times out).
- **Samples go through an ADC.** Too small a V/div clips, too slow a sample rate
  aliases, and the measurements are computed from those samples, so they are wrong
  in the same way.

Captures are lazy: a 24 Mpoint record costs nothing until a part of it is read.

An input is a ``Signal``, which repeats forever, or a ``Transient``, which happens
once: a device's power-on, say. A Transient lives on the clock of ``time.monotonic()``,
so a scope armed with Single catches it only if it happens after arming.
"""

from __future__ import annotations

import math
import random
import re
import struct
import time
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from typing import Callable, ClassVar

from ..errors import ScpiError
from ..faults import validate as validate_fault
from ..instrument import Call, Instrument, scpi
from ..scpi.params import parse_bool, parse_number
from .scope_measure import measure

NO_VALUE = 9.9e37
SHAPES = ("dc", "sine", "square", "triangle", "sawtooth")
_NOISE_POINTS = 1 << 16
# How far apart two readings of the clock can be and still look equal: up to 16 ms on
# Windows. An event in the same tick as a look at it must still count as after that look.
_CLOCK_TICK = time.get_clock_info("monotonic").resolution
_SI = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}


@dataclass(frozen=True)
class Signal:
    """What is at a channel's input. ``offset`` is the middle of the swing, ``vpp`` its height."""

    shape: str = "dc"
    frequency: float = 1e3
    vpp: float = 0.0
    offset: float = 0.0
    duty: float = 0.5  # square: fraction of the period above the middle
    phase: float = 0.0  # degrees
    rise: float | None = None  # square: edge time in seconds; None: 1 % of the period

    def __post_init__(self) -> None:
        if self.shape not in SHAPES:
            raise ValueError(f"unknown signal shape {self.shape!r} (allowed: {', '.join(SHAPES)})")
        if not (self.frequency > 0 and math.isfinite(self.frequency)):
            raise ValueError("signal frequency must be > 0")
        if not (self.vpp >= 0 and math.isfinite(self.vpp) and math.isfinite(self.offset)):
            raise ValueError("signal vpp must be >= 0 and offset a finite number")
        if not 0 < self.duty < 1:
            raise ValueError("signal duty must be between 0 and 1")

    @property
    def mean(self) -> float:
        if self.shape == "square":
            return self.offset + self.vpp / 2 * (2 * self.duty - 1)
        return self.offset

    def sampler(self) -> Callable[[float], float]:
        """A fast function of time."""
        offset, half = self.offset, self.vpp / 2
        if self.shape == "dc" or half == 0:
            return lambda t: offset
        frequency, shift = self.frequency, self.phase / 360
        if self.shape == "sine":
            two_pi, sin = 2 * math.pi, math.sin
            return lambda t: offset + half * sin(two_pi * (t * frequency + shift))
        if self.shape == "triangle":
            return lambda t: offset + half * (1 - 4 * abs((t * frequency + shift + 0.25) % 1 - 0.5))
        if self.shape == "sawtooth":
            return lambda t: offset + half * (2 * ((t * frequency + shift) % 1) - 1)
        duty = self.duty
        edge = min(1 / 100 if self.rise is None else self.rise * frequency, duty, 1 - duty)

        def square(t: float) -> float:
            p = (t * frequency + shift) % 1
            if p < edge:
                return offset + half * (2 * p / edge - 1)
            if p < duty:
                return offset + half
            if p < duty + edge:
                return offset + half * (1 - 2 * (p - duty) / edge)
            return offset - half
        return square


@dataclass(frozen=True)
class Transient:
    """A one-shot waveform: straight lines between ``points``, ``(seconds after start, volts)``.

    ``start`` is the ``time.monotonic()`` of the event. Before the first point the
    signal has the first value, after the last point the last value. Two points at
    the same time are a step. ``ripple`` is added once the shape has settled, after
    the last point; the trigger does not see it.
    """

    start: float
    points: tuple[tuple[float, float], ...]
    ripple: Signal | None = None

    def __post_init__(self) -> None:
        points = tuple((float(t), float(v)) for t, v in self.points)
        object.__setattr__(self, "points", points)
        if not points:
            raise ValueError("a transient needs at least one point")
        if not all(math.isfinite(t) and math.isfinite(v) for t, v in points):
            raise ValueError("transient points must be finite numbers")
        if points[0][0] < 0 or any(a[0] > b[0] for a, b in zip(points, points[1:])):
            raise ValueError("transient times must start at 0 or later and must not go back")

    shape: ClassVar[str] = "transient"

    @property
    def vpp(self) -> float:
        values = [v for _, v in self.points]
        return max(values) - min(values)

    @property
    def offset(self) -> float:
        """The middle of the swing."""
        values = [v for _, v in self.points]
        return (max(values) + min(values)) / 2

    @property
    def mean(self) -> float:
        """Where the signal settles: what AC coupling takes away."""
        return self.points[-1][1] + (self.ripple.mean if self.ripple else 0.0)

    @property
    def duration(self) -> float:
        return self.points[-1][0] - self.points[0][0]

    def at(self, seconds: float) -> float:
        """The value ``seconds`` after the start, without the ripple."""
        return self._line([t for t, _ in self.points], [v for _, v in self.points])(seconds)

    @staticmethod
    def _line(times: list[float], values: list[float]) -> Callable[[float], float]:
        last = len(times)

        def line(t: float) -> float:
            i = bisect_right(times, t)
            if i == 0:
                return values[0]
            if i == last:
                return values[-1]
            t0, v0, t1, v1 = times[i - 1], values[i - 1], times[i], values[i]
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return line

    def sampler(self) -> Callable[[float], float]:
        """A fast function of ``time.monotonic()`` time."""
        start = self.start
        line = self._line([t for t, _ in self.points], [v for _, v in self.points])
        if self.ripple is None:
            return lambda t: line(t - start)
        ripple, settled = self.ripple.sampler(), start + self.points[-1][0]
        return lambda t: line(t - start) + (ripple(t) if t >= settled else 0.0)

    def crossing(self, level: float, slope: str, after: float, until: float) -> float | None:
        """The first time in ``after < t <= until`` the shape goes through ``level``."""
        for (t0, v0), (t1, v1) in zip(self.points, self.points[1:]):
            rising, falling = v0 < level <= v1, v0 >= level > v1
            if (rising and slope != "NEG") or (falling and slope != "POS"):
                t = self.start + t0 + (level - v0) / (v1 - v0) * (t1 - t0)
                if after < t <= until:
                    return t
        return None


def _si_number(text: str) -> float:
    match = re.fullmatch(r"([+-]?[\d.]+(?:[eE][+-]?\d+)?)\s*([numkMG]?)(?:Hz|V|s)?", text.strip())
    if not match:
        raise ValueError(f"not a number: {text!r}")
    return float(match.group(1)) * _SI.get(match.group(2), 1.0)


def parse_signal(text: str) -> tuple[int, Signal]:
    """``1:square,freq=1k,vpp=3.3,offset=1.65,duty=0.25,phase=90`` -> channel 1 and its signal."""
    channel, sep, rest = text.partition(":")
    if not sep or not channel.strip().upper().removeprefix("CH").isdigit():
        raise ValueError(f"signal {text!r}: expected CHANNEL:SHAPE[,KEY=VALUE...], e.g. 1:sine,freq=1k,vpp=2")
    shape, *items = [part.strip() for part in rest.split(",")]
    values: dict = {"shape": shape}
    names = {"freq": "frequency", "frequency": "frequency", "vpp": "vpp", "offset": "offset", "duty": "duty",
             "phase": "phase", "rise": "rise"}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or key.strip() not in names:
            raise ValueError(f"signal {text!r}: unknown setting {item!r} (allowed: {', '.join(sorted(set(names)))})")
        values[names[key.strip()]] = _si_number(value)
    return int(channel.strip().upper().removeprefix("CH")), Signal(**values)


def signals_option(value) -> dict[int, Signal]:
    """Bench option ``signals``: ``{1: {shape: sine, frequency: 1000, vpp: 2}, ...}`` (keys 1 or ch1)."""
    if not isinstance(value, dict):
        raise ValueError("must map channels to signals, e.g. {1: {shape: sine, frequency: 1000, vpp: 2}}")
    signals = {}
    for key, settings in value.items():
        number = str(key).upper().removeprefix("CH")
        if not number.isdigit() or not isinstance(settings, dict):
            raise ValueError(f"entry {key!r}: expected a channel and a mapping of signal settings")
        try:
            signals[int(number)] = Signal(**settings)
        except TypeError as error:
            raise ValueError(f"channel {key}: {error}") from None
    return signals


@dataclass
class _Channel:
    scale: float
    probe: float
    on: bool
    offset: float = 0.0
    position: float = 0.0  # divisions; models that have only an offset leave it at 0
    coupling: str = "DC"


@dataclass(frozen=True)
class _Trace:
    """One channel of a capture: the settings it was taken with."""

    sampler: Callable[[float], float]
    center: float
    lsb: float
    on: bool
    noise: float
    noise_start: int


@dataclass(frozen=True)
class Capture:
    x0: float  # time of the first point of the record, relative to the trigger
    window: float
    points: int
    delay: float  # signal time at the trigger
    triggered: bool
    traces: dict[int, _Trace] = field(default_factory=dict)

    @property
    def dt(self) -> float:
        return self.window / self.points


def snap_125(value: float) -> float:
    """The nearest value of the 1-2-5 sequence."""
    decade = 10.0 ** math.floor(math.log10(value))
    return min((m * decade for m in (1, 2, 5, 10)), key=lambda step: abs(step - value))


class ScopeEngine(Instrument):
    """The oscilloscope without a command set. Class attributes describe the model."""

    kind: ClassVar[str] = "scope"
    SPECIFIC_FAULTS = frozenset({"offset", "noise", "no_trigger", "signal"})
    CHANNELS: ClassVar[int] = 4
    DIVS_X: ClassVar[int] = 10
    DIVS_Y: ClassVar[int] = 8
    # the ADC: unsigned code = (volts - center) / lsb + ADC_REF, limited to ADC_MIN..ADC_MAX
    LSB_PER_DIV: ClassVar[float] = 32.0
    ADC_REF: ClassVar[float] = 128
    ADC_MIN: ClassVar[int] = 0
    ADC_MAX: ClassVar[int] = 255
    MAX_RATE: ClassVar[float] = 2e9  # samples/s with one channel per converter
    MAX_DEPTH: ClassVar[int] = 1_000_000  # with one channel per converter
    INTERLEAVED: ClassVar[bool] = True  # more channels on: lower rate and depth (see _channel_share)
    VOLTS_MIN: ClassVar[float] = 1e-3  # V/div with a 1:1 probe
    VOLTS_MAX: ClassVar[float] = 5.0
    TIME_MIN: ClassVar[float] = 5e-9
    TIME_MAX: ClassVar[float] = 50.0
    SNAP_VOLTS: ClassVar[bool] = False
    SNAP_TIME: ClassVar[bool] = False
    OFFSET_SIGN: ClassVar[int] = 1  # +1: offset is the volts at center screen; -1: it moves the trace up
    DEFAULT_PROBE: ClassVar[float] = 1.0
    DEFAULT_VOLTS: ClassVar[float] = 1.0
    DEFAULT_TIME: ClassVar[float] = 100e-6
    MEASURE_POINTS: ClassVar[int] = 10_000
    PROBES: ClassVar[tuple[float, ...]] = (1, 10, 100)

    def __init__(self, *, signals: dict[int, Signal] | None = None, noise: float = 0.002, seed: int | None = None,
                 serial: str | None = None):
        super().__init__(serial=serial)
        self.noise = noise
        self._random = random.Random(seed)
        self._noise_table: list[float] | None = None
        self.signals: dict[int, Signal] = {
            1: Signal("sine", frequency=1e3, vpp=2.0),
            2: Signal("square", frequency=1e3, vpp=3.3, offset=1.65),
            **{channel: Signal() for channel in range(3, self.CHANNELS + 1)},
        }
        for channel, signal in (signals or {}).items():
            if not 1 <= channel <= self.CHANNELS:
                raise ValueError(f"channel {channel} does not exist (the {self.model} has {self.CHANNELS})")
            self.signals[channel] = signal
        self._overrides: dict[int, Signal] = {}
        # what is wired to a channel on a bench: channel -> function returning volts or a waveform
        self.inputs: dict[int, Callable[[], float | Signal | Transient]] = {}
        self.reset()

    # --- settings ------------------------------------------------------------------

    def reset(self) -> None:
        super().reset()
        self.channels = {n: _Channel(self.DEFAULT_VOLTS, self.DEFAULT_PROBE, n == 1)
                         for n in range(1, self.CHANNELS + 1)}
        self.time_scale = self.DEFAULT_TIME
        self.time_position = 0.0
        self.trigger_source = 1
        self.trigger_level = 0.0
        self.trigger_slope = "POS"
        self.sweep = "AUTO"
        self.depth: int | None = None  # None: as much as fits the sample rate
        self._state = "run"  # run, single (waiting for its trigger) or stop
        self._status = "auto"  # triggered, auto, wait or stop
        self._capture: Capture | None = None
        self._new_acquisition = False  # read by event registers
        self._seen_until = time.monotonic() - _CLOCK_TICK  # edges of a Transient before this are over

    def channel(self, suffix: int | None) -> _Channel:
        return self.channels[self.channel_number(suffix)]

    def channel_number(self, suffix: int | None) -> int:
        number = 1 if suffix is None else suffix
        if not 1 <= number <= self.CHANNELS:
            raise ScpiError(-114)
        return number

    def set_volts(self, number: int, value: float) -> None:
        channel = self.channels[number]
        low, high = self.VOLTS_MIN * channel.probe, self.VOLTS_MAX * channel.probe
        if not low <= value <= high:
            raise ScpiError(-222)
        channel.scale = snap_125(value) if self.SNAP_VOLTS else value

    def set_time(self, value: float) -> None:
        if not self.TIME_MIN <= value <= self.TIME_MAX:
            raise ScpiError(-222)
        self.time_scale = snap_125(value) if self.SNAP_TIME else value

    def set_probe(self, number: int, ratio: float) -> None:
        if ratio not in self.PROBES:
            raise ScpiError(-222)
        channel = self.channels[number]
        channel.scale *= ratio / channel.probe
        channel.probe = ratio

    def center(self, channel: _Channel) -> float:
        """The volts at center screen."""
        return self.OFFSET_SIGN * channel.offset - channel.position * channel.scale

    def lsb(self, channel: _Channel) -> float:
        return channel.scale / self.LSB_PER_DIV

    def _channel_share(self) -> int:
        """How many channels share a converter: rate and depth divide by this."""
        if not self.INTERLEAVED:
            return 1
        on = sum(channel.on for channel in self.channels.values())
        return 1 if on <= 1 else 2 if on == 2 else 4

    def record(self) -> tuple[float, int]:
        """(sample rate, points) with the current settings."""
        share = self._channel_share()
        window = self.DIVS_X * self.time_scale
        max_rate, max_depth = self.MAX_RATE / share, self.MAX_DEPTH // share
        wanted = max_depth if self.depth is None else min(self.depth, max_depth)
        points = max(2, min(wanted, int(max_rate * window)))
        return points / window, points

    # --- what is at the inputs ------------------------------------------------------

    def input_signal(self, number: int) -> Signal | Transient:
        if number in self._overrides:
            return self._overrides[number]
        if number not in self.inputs:
            return self.signals[number]
        value = self.inputs[number]()  # wired on a bench
        if isinstance(value, (Signal, Transient)):
            return value
        if isinstance(value, (int, float)):
            return Signal(offset=float(value))
        raise TypeError(f"channel {number}: expected volts, a Signal or a Transient, not {type(value).__name__}")

    def _has_transient(self) -> bool:
        return any(isinstance(self.input_signal(number), Transient) for number in self.inputs)

    def _sampler(self, number: int) -> Callable[[float], float]:
        channel = self.channels[number]
        signal = self.input_signal(number)
        added = self._faults["offset"]["value"] if "offset" in self._faults else 0.0
        if channel.coupling == "GND":
            return lambda t: 0.0
        if channel.coupling == "AC" and isinstance(signal, Transient):
            shape, settled = signal.sampler(), signal.mean
            sampler = lambda t: shape(t) - settled  # noqa: E731
        else:
            if channel.coupling == "AC":
                signal = replace(signal, offset=signal.offset - signal.mean)
            sampler = signal.sampler()
        return (lambda t: sampler(t) + added) if added else sampler

    def inject(self, kind: str, **params) -> None:
        if kind != "signal":
            super().inject(kind, **params)
            return
        params = validate_fault(kind, params)
        number = params.pop("channel")
        if number > self.CHANNELS:
            raise ValueError(f"fault 'signal': the {self.model} has no channel {number}")
        signal = Signal(**params)
        with self._lock:
            self._overrides[number] = signal
            self._faults["signal"] = {"channels": ",".join(map(str, sorted(self._overrides)))}

    def clear_faults(self) -> None:
        with self._lock:
            super().clear_faults()
            self._overrides.clear()

    # --- trigger and acquisition ------------------------------------------------------

    def _find_trigger(self) -> float | None:
        """Signal time of an edge through the trigger level, or None if there is none."""
        if "no_trigger" in self._faults:
            return None
        signal = self.input_signal(self.trigger_source)
        if isinstance(signal, Transient):
            return self._transient_edge(signal)
        if signal.shape == "dc" or signal.vpp == 0:
            return None
        f = self._sampler(self.trigger_source)
        level, slope, period = self.trigger_level, self.trigger_slope, 1 / signal.frequency
        steps = 2048
        # off the grid: a sine through the level at t = 0 has no sign change there.
        # A Transient on another channel needs the same clock.
        start = (time.monotonic() if self._has_transient() else 0.0) + 0.37 * period / steps
        previous = f(start) - level
        for i in range(1, steps + 2):
            t = start + i * period / steps
            now = f(t) - level
            rising = previous < 0 <= now
            falling = previous >= 0 > now
            if (rising and slope != "NEG") or (falling and slope != "POS"):
                low, high = t - period / steps, t
                for _ in range(40):  # an edge can be much shorter than a scan step
                    middle = (low + high) / 2
                    if (f(middle) - level >= 0) == rising:
                        high = middle
                    else:
                        low = middle
                return high
            previous = now
        return None

    def _transient_edge(self, signal: Transient) -> float | None:
        """Time of the first edge since the last look; nothing before that can trigger again."""
        now = time.monotonic()
        channel = self.channels[self.trigger_source]
        edge = None
        if channel.coupling != "GND":
            level = self.trigger_level
            level -= self._faults["offset"]["value"] if "offset" in self._faults else 0.0
            level += signal.mean if channel.coupling == "AC" else 0.0
            edge = signal.crossing(level, self.trigger_slope, self._seen_until, now)
        self._seen_until = max(self._seen_until, now - _CLOCK_TICK) if edge is None else edge
        return edge

    def _free_run_delay(self) -> float:
        """Signal time at the middle of an untriggered capture. With a Transient: the latest samples."""
        if self._has_transient():
            rate, points = self.record()
            return time.monotonic() - self.time_position - points / rate / 2
        return self._random.random()

    def _take_capture(self, delay: float, triggered: bool) -> None:
        rate, points = self.record()
        window = points / rate
        noise = self._faults["noise"]["sigma"] if "noise" in self._faults else self.noise
        traces = {}
        for number, channel in self.channels.items():
            traces[number] = _Trace(self._sampler(number), self.center(channel), self.lsb(channel), channel.on,
                                    noise, self._random.randrange(_NOISE_POINTS))
        self._capture = Capture(self.time_position - window / 2, window, points, delay, triggered, traces)
        self._new_acquisition = True

    def poll(self) -> None:
        """Bring the acquisition up to date; every query that looks at data calls this."""
        if self._state == "stop":
            return
        delay = self._find_trigger()
        if delay is not None:
            self._take_capture(delay, True)
            self._status = "triggered"
            if self._state == "single":
                self._state = "stop"
            return
        if self._state == "run" and self.sweep == "AUTO":
            self._take_capture(self._free_run_delay(), False)
            self._status = "auto"
        else:
            self._status = "wait"

    @property
    def status(self) -> str:
        """stop (a finished or stopped acquisition), triggered, auto or wait."""
        self.poll()
        return "stop" if self._state == "stop" else self._status

    @property
    def running(self) -> bool:
        return self._state != "stop"

    def run(self) -> None:
        if self._state == "stop":
            self._seen_until = time.monotonic() - _CLOCK_TICK  # a stopped scope saw nothing
        self._state = "run"

    def stop(self) -> None:
        self.poll()
        self._state = "stop"

    def single(self) -> None:
        self._seen_until = time.monotonic() - _CLOCK_TICK  # only what happens from now on
        self._state = "single"
        self.poll()

    def force_trigger(self) -> None:
        """Take a capture now, whatever the trigger says."""
        if self._state != "stop":
            self._take_capture(self._free_run_delay(), False)
            self._status = "triggered"
            if self._state == "single":
                self._state = "stop"

    def autoscale(self) -> None:
        """Fit the channels that carry a signal on the screen, and trigger on the first of them."""
        first = None
        for number, channel in self.channels.items():
            signal = self.input_signal(number)
            if signal.shape == "dc" or signal.vpp == 0:
                continue
            channel.on = True
            wanted = max(signal.vpp / (self.DIVS_Y - 2), self.VOLTS_MIN * channel.probe)
            self.set_volts(number, min(self._next_up(wanted), self.VOLTS_MAX * channel.probe))
            channel.position, channel.offset = 0.0, self.OFFSET_SIGN * signal.offset
            first = first or (number, signal)
        if first is not None:
            number, signal = first
            self.trigger_source, self.trigger_level, self.trigger_slope = number, signal.offset, "POS"
            self.sweep = "AUTO"
            span = signal.duration if isinstance(signal, Transient) else 3 / signal.frequency
            self.set_time(max(self.TIME_MIN, min(self.TIME_MAX, self._next_up(span / self.DIVS_X))))
            self.time_position = 0.0
        self._state = "run"

    @staticmethod
    def _next_up(value: float) -> float:
        decade = 10.0 ** math.floor(math.log10(value))
        return next(m * decade for m in (1, 2, 5, 10) if m * decade >= value * 0.999)

    @scpi("*OPC?")
    def _opc_query(self, call: Call) -> int | None:  # no trigger: the query waits, as on a real scope
        self.poll()
        return None if self._state == "single" else 1

    # --- reading a capture --------------------------------------------------------------

    def current_capture(self) -> Capture:
        self.poll()
        if self._capture is None:
            raise ScpiError(-230, "Data corrupt or stale; no acquisition yet")
        return self._capture

    def _noise_values(self) -> list[float]:
        if self._noise_table is None:
            gauss = random.Random(self._random.random()).gauss
            self._noise_table = [gauss(0, 1) for _ in range(_NOISE_POINTS)]
        return self._noise_table

    def read_codes(self, capture: Capture, number: int, first: int, count: int, dt: float,
                   x0: float | None = None) -> list[int]:
        """Unsigned ADC codes of ``count`` points, ``dt`` apart, from point ``first`` of a record."""
        trace = capture.traces[number]
        start = capture.x0 if x0 is None else x0
        sampler, delay, center, lsb = trace.sampler, capture.delay, trace.center, trace.lsb
        ref, low, high = self.ADC_REF, self.ADC_MIN, self.ADC_MAX
        values = [sampler(start + (first + i) * dt + delay) for i in range(count)]
        if trace.noise:
            table, sigma, mask = self._noise_values(), trace.noise, _NOISE_POINTS - 1
            base = trace.noise_start + first
            values = [v + sigma * table[(base + i) & mask] for i, v in enumerate(values)]
        return [min(high, max(low, round((v - center) / lsb + ref))) for v in values]

    def read_volts(self, capture: Capture, number: int, first: int, count: int, dt: float,
                   x0: float | None = None) -> list[float]:
        trace = capture.traces[number]
        center, lsb, ref = trace.center, trace.lsb, self.ADC_REF
        return [center + (code - ref) * lsb for code in self.read_codes(capture, number, first, count, dt, x0)]

    def measurements(self, number: int) -> dict[str, float | None]:
        """All measurements of a channel; an empty result if it is off or there is no capture."""
        self.poll()
        capture = self._capture
        if capture is None or not capture.traces[number].on:
            return measure([], 1.0)
        points = min(capture.points, self.MEASURE_POINTS)
        dt = capture.window / points
        trace = capture.traces[number]
        return measure(self.read_volts(capture, number, 0, points, dt), dt, trace.lsb)

    def measure_value(self, number: int, name: str) -> float:
        value = self.measurements(number)[name]
        return NO_VALUE if value is None else value


def number_arg(call: Call, i: int, low: float = -1e12, high: float = 1e12, unit: str | None = None) -> float:
    """A plain numeric parameter (no MIN/MAX, as the scope manuals document none)."""
    return parse_number(call.arg(i), unit, minimum=low, maximum=high, default=0.0)


def keyword_arg(call: Call, i: int, table: dict[str, str]) -> str:
    """A discrete parameter by short or long form; ``table`` maps each spelling to its value."""
    try:
        return table[call.arg(i).strip().upper()]
    except KeyError:
        raise ScpiError(-224) from None


def spellings(*names: str) -> dict[str, str]:
    """``spellings("NORMal")`` -> ``{"NORM": "NORM", "NORMAL": "NORM"}``: the value is the short form."""
    table = {}
    for name in names:
        short = re.match(r"[A-Z0-9]*", name).group(0)
        table[short] = table[name.upper()] = short
    return table


# waveform and measurement sources: CHANnel1, CHAN1
_CHANNEL_SOURCE = re.compile(r"CHAN(?:NEL)?(\d+)")
_SWEEP = {"AUTO": "AUTO", "NORM": "NORMAL", "NORMAL": "NORMAL"}
_SLOPE = {"POS": "POS", "POSITIVE": "POS", "NEG": "NEG", "NEGATIVE": "NEG", "EITH": "EITHER", "EITHER": "EITHER"}
_COUPLING = {"DC": "DC", "AC": "AC", "GND": "GND", "GROUND": "GND"}


def _measure_query(keyword: str, name: str, percent: bool = False) -> Callable:
    """``:MEASure:<keyword>? [<source>]``; VRMS and VAVerage also take ``[<interval>][,<type>]``."""
    @scpi(f"MEASure:{keyword}?", max_args=3)
    def query(self, call: Call) -> float:
        value = self._measured(call, name)
        return value * 100 if percent and value != NO_VALUE else value
    return query


def _block8(data: bytes) -> bytes:
    """The header in the programmer's guide: always eight digits, ``#800001000``."""
    return f"#8{len(data):08d}".encode() + data


class Oscilloscope(ScopeEngine):
    """The generic scope: the command tree of Keysight InfiniiVision scopes (see the profile for what it covers)."""

    model = "SCOPE"
    serial = "SIM00004"
    ADC_MIN = 1  # 0 is a hole in the record, 1 clipped low, 255 clipped high
    DEFAULT_POINTS: ClassVar[int] = 1000
    MAX_MEASUREMENT_RECORD: ClassVar[int] = 62_500

    def __init__(self, *, channels: int = 4, **kwargs):
        if channels not in (2, 4):
            raise ValueError("channels must be 2 or 4")
        self.CHANNELS = channels
        super().__init__(**kwargs)

    def reset(self) -> None:
        super().reset()
        self.wave_source = 1
        self.wave_format = "BYTE"
        self.wave_points = self.DEFAULT_POINTS
        self.wave_points_mode = "NORM"
        self.wave_msb_first = True
        self.wave_unsigned = True
        self.measure_source = 1

    def _source(self, text: str) -> int:
        match = _CHANNEL_SOURCE.fullmatch(text.strip().upper())
        if not match:
            raise ScpiError(-224)
        number = int(match.group(1))
        if not 1 <= number <= self.CHANNELS:
            raise ScpiError(-224)
        return number

    # --- root ---------------------------------------------------------------------------

    @scpi("RUN", max_args=0)
    def _run_cmd(self, call: Call) -> None:
        self.run()

    @scpi("STOP", max_args=0)
    def _stop_cmd(self, call: Call) -> None:
        self.stop()

    @scpi("SINGle", max_args=0)
    def _single_cmd(self, call: Call) -> None:
        self.single()

    @scpi("DIGitize", max_args=5)
    def _digitize(self, call: Call) -> None:
        if call.args:  # the listed channels are acquired, the others switched off
            listed = {self._source(arg) for arg in call.args}
            for number, channel in self.channels.items():
                channel.on = number in listed
        self.single()
        if self.sweep == "AUTO":  # Auto sweep does not wait for a trigger
            self.force_trigger()

    @scpi("AUToscale", max_args=4)
    def _autoscale_cmd(self, call: Call) -> None:
        self.autoscale()

    @scpi("TER?")
    def _ter(self, call: Call) -> int:
        self.poll()
        fired, self._new_acquisition = self._new_acquisition, False
        return 1 if fired else 0

    @scpi("OPERegister:CONDition?")
    def _operation(self, call: Call) -> int:
        self.poll()
        return 8 if self.running else 0  # bit 3: Run

    # --- channels --------------------------------------------------------------------------

    @scpi("CHANnel#:SCALe")
    def _chan_scale(self, call: Call) -> None:
        number = self.channel_number(call.suffixes[0])
        self.set_volts(number, number_arg(call, 0, unit="V"))

    @scpi("CHANnel#:SCALe?")
    def _chan_scale_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).scale

    @scpi("CHANnel#:RANGe")
    def _chan_range(self, call: Call) -> None:
        number = self.channel_number(call.suffixes[0])
        self.set_volts(number, number_arg(call, 0, unit="V") / self.DIVS_Y)

    @scpi("CHANnel#:RANGe?")
    def _chan_range_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).scale * self.DIVS_Y

    @scpi("CHANnel#:OFFSet")
    def _chan_offset(self, call: Call) -> None:
        self.channel(call.suffixes[0]).offset = number_arg(call, 0, unit="V")

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
        self.channel(call.suffixes[0]).coupling = keyword_arg(call, 0, _COUPLING)

    @scpi("CHANnel#:COUPling?")
    def _chan_coupling_q(self, call: Call) -> str:
        return self.channel(call.suffixes[0]).coupling

    @scpi("CHANnel#:PROBe")
    def _chan_probe(self, call: Call) -> None:
        self.set_probe(self.channel_number(call.suffixes[0]), number_arg(call, 0))

    @scpi("CHANnel#:PROBe?")
    def _chan_probe_q(self, call: Call) -> float:
        return self.channel(call.suffixes[0]).probe

    # --- timebase -----------------------------------------------------------------------------

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

    # --- trigger ----------------------------------------------------------------------------------

    @scpi("TRIGger[:EDGE]:LEVel", max_args=2)
    def _trig_level(self, call: Call) -> None:
        level = number_arg(call, 0, unit="V")
        source = self.trigger_source if len(call.args) < 2 else self._source(call.args[1])
        channel = self.channels[source]
        if abs(level - self.center(channel)) > 0.75 * channel.scale * self.DIVS_Y:
            raise ScpiError(-222)
        self.trigger_level = level
        self.trigger_source = source

    @scpi("TRIGger[:EDGE]:LEVel?", max_args=1)
    def _trig_level_q(self, call: Call) -> float:
        return self.trigger_level

    @scpi("TRIGger[:EDGE]:SOURce")
    def _trig_source(self, call: Call) -> None:
        self.trigger_source = self._source(call.arg(0))

    @scpi("TRIGger[:EDGE]:SOURce?")
    def _trig_source_q(self, call: Call) -> str:
        return f"CHAN{self.trigger_source}"

    @scpi("TRIGger[:EDGE]:SLOPe")
    def _trig_slope(self, call: Call) -> None:
        self.trigger_slope = keyword_arg(call, 0, _SLOPE)

    @scpi("TRIGger[:EDGE]:SLOPe?")
    def _trig_slope_q(self, call: Call) -> str:
        return {"POS": "POS", "NEG": "NEG", "EITHER": "EITH"}[self.trigger_slope]

    @scpi("TRIGger:SWEep")
    def _trig_sweep(self, call: Call) -> None:
        self.sweep = keyword_arg(call, 0, _SWEEP)

    @scpi("TRIGger:SWEep?")
    def _trig_sweep_q(self, call: Call) -> str:
        return "AUTO" if self.sweep == "AUTO" else "NORM"

    @scpi("TRIGger:FORCe", max_args=0)
    def _trig_force(self, call: Call) -> None:
        self.force_trigger()

    # --- acquire --------------------------------------------------------------------------------------

    @scpi("ACQuire:SRATe?")
    def _srate(self, call: Call) -> float:
        return self.record()[0]

    @scpi("ACQuire:POINts?")
    def _acq_points(self, call: Call) -> int:
        return self.record()[1]

    # --- waveform --------------------------------------------------------------------------------------

    @scpi("WAVeform:SOURce")
    def _wav_source(self, call: Call) -> None:
        self.wave_source = self._source(call.arg(0))

    @scpi("WAVeform:SOURce?")
    def _wav_source_q(self, call: Call) -> str:
        return f"CHAN{self.wave_source}"

    @scpi("WAVeform:FORMat")
    def _wav_format(self, call: Call) -> None:
        self.wave_format = keyword_arg(call, 0, {"BYTE": "BYTE", "WORD": "WORD", "ASC": "ASC", "ASCII": "ASC"})

    @scpi("WAVeform:FORMat?")
    def _wav_format_q(self, call: Call) -> str:
        return self.wave_format

    @scpi("WAVeform:BYTeorder")
    def _wav_order(self, call: Call) -> None:
        self.wave_msb_first = keyword_arg(call, 0, {"MSBF": "M", "MSBFIRST": "M", "LSBF": "L", "LSBFIRST": "L"}) == "M"

    @scpi("WAVeform:BYTeorder?")
    def _wav_order_q(self, call: Call) -> str:
        return "MSBF" if self.wave_msb_first else "LSBF"

    @scpi("WAVeform:UNSigned")
    def _wav_unsigned(self, call: Call) -> None:
        self.wave_unsigned = parse_bool(call.arg(0))

    @scpi("WAVeform:UNSigned?")
    def _wav_unsigned_q(self, call: Call) -> bool:
        return self.wave_unsigned

    @scpi("WAVeform:POINts")
    def _wav_points(self, call: Call) -> None:
        arg = call.arg(0).strip().upper()
        if arg in ("NORM", "NORMAL", "MAX", "MAXIMUM", "RAW"):  # the deprecated form of POINts:MODE
            self.wave_points_mode = arg[:3] if arg.startswith("MAX") else "NORM" if arg.startswith("NORM") else "RAW"
            return
        self.wave_points = round(number_arg(call, 0, low=1, high=8e6))

    @scpi("WAVeform:POINts?", max_args=1)
    def _wav_points_q(self, call: Call) -> int:
        return self._transfer_points(self.current_capture())

    @scpi("WAVeform:POINts:MODE")
    def _wav_points_mode(self, call: Call) -> None:
        self.wave_points_mode = keyword_arg(call, 0, spellings("NORMal", "MAXimum", "RAW"))

    @scpi("WAVeform:POINts:MODE?")
    def _wav_points_mode_q(self, call: Call) -> str:
        return self.wave_points_mode

    def _transfer_points(self, capture: Capture) -> int:
        if self.wave_points_mode != "NORM" and not self.running:
            available = capture.points
        else:  # the raw record is only there on a stopped scope
            available = min(capture.points, self.MAX_MEASUREMENT_RECORD)
        return min(self.wave_points, available)

    def _wave_view(self, capture: Capture) -> tuple[int, float]:
        points = self._transfer_points(capture)
        return points, capture.window / points

    def _wave_y(self, capture: Capture) -> tuple[float, float, int]:
        """(y increment, y origin, y reference) of the selected source and format."""
        trace = capture.traces[self.wave_source]
        if self.wave_format == "WORD":
            return trace.lsb / 256, trace.center, 32768
        return trace.lsb, trace.center, int(self.ADC_REF)

    @scpi("WAVeform:PREamble?")
    def _preamble(self, call: Call) -> str:
        capture = self.current_capture()
        points, dt = self._wave_view(capture)
        yinc, yorigin, yref = self._wave_y(capture)
        kind = {"BYTE": 0, "WORD": 1, "ASC": 4}[self.wave_format]
        return (f"+{kind},+0,+{points},+1,{dt:+.8E},{capture.x0:+.8E},+0,"
                f"{yinc:+.8E},{yorigin:+.8E},+{yref}")

    @scpi("WAVeform:XINCrement?")
    def _xinc(self, call: Call) -> float:
        capture = self.current_capture()
        return self._wave_view(capture)[1]

    @scpi("WAVeform:XORigin?")
    def _xorigin(self, call: Call) -> float:
        return self.current_capture().x0

    @scpi("WAVeform:XREFerence?")
    def _xref(self, call: Call) -> int:
        return 0

    @scpi("WAVeform:YINCrement?")
    def _yinc(self, call: Call) -> float:
        return self._wave_y(self.current_capture())[0]

    @scpi("WAVeform:YORigin?")
    def _yorigin(self, call: Call) -> float:
        return self._wave_y(self.current_capture())[1]

    @scpi("WAVeform:YREFerence?")
    def _yref(self, call: Call) -> int:
        return self._wave_y(self.current_capture())[2]

    @scpi("WAVeform:DATA?")
    def _wav_data(self, call: Call) -> bytes | str:
        capture = self.current_capture()
        number = self.wave_source
        if not capture.traces[number].on:
            raise ScpiError(-221, "Settings conflict; waveform source is not displayed")
        points, dt = self._wave_view(capture)
        if self.wave_format == "ASC":
            volts = self.read_volts(capture, number, 0, points, dt)
            return ",".join(f"{v:+.6E}" for v in volts)
        codes = self.read_codes(capture, number, 0, points, dt)
        if self.wave_format == "BYTE":
            return _block8(bytes(codes if self.wave_unsigned else [(c - 128) & 0xFF for c in codes]))
        values = [c * 256 if self.wave_unsigned else (c - 128) * 256 for c in codes]
        order = (">" if self.wave_msb_first else "<") + ("H" if self.wave_unsigned else "h") * len(values)
        return _block8(struct.pack(order, *values))

    # --- measure --------------------------------------------------------------------------------------

    @scpi("MEASure:SOURce")
    def _meas_source(self, call: Call) -> None:
        self.measure_source = self._source(call.arg(0))

    @scpi("MEASure:SOURce?", max_args=2)
    def _meas_source_q(self, call: Call) -> str:
        return f"CHAN{self.measure_source}"

    def _measured(self, call: Call, name: str) -> float:
        # the source is the last parameter; VRMS and VAVerage take [<interval>][,<type>] before it
        number = self.measure_source
        for arg in call.args:
            match = _CHANNEL_SOURCE.fullmatch(arg.strip().upper())
            if match:
                number = self._source(arg)
        return self.measure_value(number, name)

    _meas_vpp = _measure_query("VPP", "vpp")
    _meas_vmax = _measure_query("VMAX", "vmax")
    _meas_vmin = _measure_query("VMIN", "vmin")
    _meas_vavg = _measure_query("VAVerage", "vavg")
    _meas_vrms = _measure_query("VRMS", "vrms")
    _meas_vamp = _measure_query("VAMPlitude", "vamp")
    _meas_vtop = _measure_query("VTOP", "vtop")
    _meas_vbase = _measure_query("VBASe", "vbase")
    _meas_freq = _measure_query("FREQuency", "frequency")
    _meas_period = _measure_query("PERiod", "period")
    _meas_rise = _measure_query("RISetime", "rise")
    _meas_fall = _measure_query("FALLtime", "fall")
    _meas_pwidth = _measure_query("PWIDth", "pwidth")
    _meas_nwidth = _measure_query("NWIDth", "nwidth")
    _meas_duty = _measure_query("DUTYcycle", "pduty", percent=True)
    _meas_nduty = _measure_query("NDUTy", "nduty", percent=True)
    _meas_overshoot = _measure_query("OVERshoot", "overshoot", percent=True)
    _meas_preshoot = _measure_query("PREShoot", "preshoot", percent=True)
