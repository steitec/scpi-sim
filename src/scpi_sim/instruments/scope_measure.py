"""Waveform measurements, computed from the acquired samples like on a real oscilloscope.

Because they work on samples, they fail the way real ones do: a clipped waveform
reads the screen limits, and without a complete period on the screen there is no
frequency (``None``, which the dialects answer as "no value", usually 9.9E37).

Levels and edges follow IEEE 181: top and base from a histogram (the extremes if
the waveform has no flat tops), times between the 10 %, 50 % and 90 % levels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class _Edge:
    rising: bool
    mid: float
    duration: float  # 10 % to 90 %


def _levels(volts: list[float], low: float, high: float) -> tuple[float, float]:
    """(top, base): the most frequent value of each half, if the waveform dwells there."""
    span = high - low
    if span <= 0:
        return high, low
    bins = [0] * 100
    for value in volts:
        bins[min(99, int((value - low) / span * 100))] += 1
    enough = 0.15 * len(volts)  # a sine dwells less than this in any one bin

    def mode(indices: range, fallback: float) -> float:
        best = max(indices, key=bins.__getitem__)
        return low + (best + 0.5) * span / 100 if bins[best] >= enough else fallback

    return mode(range(50, 100), high), mode(range(0, 50), low)


def _edges(volts: list[float], dt: float, base: float, top: float) -> list[_Edge]:
    amplitude = top - base
    low, mid, high = base + 0.1 * amplitude, base + 0.5 * amplitude, base + 0.9 * amplitude

    def crossing(i: int, level: float) -> float:
        a, b = volts[i], volts[i + 1]
        return (i + ((level - a) / (b - a) if b != a else 0.5)) * dt

    edges: list[_Edge] = []
    state = None  # "low" or "high": which level the waveform last reached
    start = 0  # last sample at or beyond the level it comes from
    for i, value in enumerate(volts):
        if value <= low:
            if state == "high" and i:
                first = crossing(start, high)
                edges.append(_Edge(False, _between(volts, start, i, mid, crossing), crossing(i - 1, low) - first))
            state, start = "low", i
        elif value >= high:
            if state == "low" and i:
                first = crossing(start, low)
                edges.append(_Edge(True, _between(volts, start, i, mid, crossing), crossing(i - 1, high) - first))
            state, start = "high", i
    return edges


def _between(volts: list[float], start: int, stop: int, level: float, crossing) -> float:
    for i in range(start, stop):
        if (volts[i] - level) * (volts[i + 1] - level) <= 0:
            return crossing(i, level)
    return crossing(start, level)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def measure(volts: list[float], dt: float, lsb: float = 0.0) -> dict[str, float | None]:
    """All measurements of one record; ``lsb``: a waveform flatter than a few steps has no edges."""
    if not volts:
        return dict.fromkeys(_NAMES)
    high, low = max(volts), min(volts)
    mean = sum(volts) / len(volts)
    top, base = _levels(volts, low, high)
    amplitude = top - base
    result: dict[str, float | None] = dict.fromkeys(_NAMES)
    result.update(
        vmax=high, vmin=low, vpp=high - low, vavg=mean, vtop=top, vbase=base, vamp=amplitude,
        vmid=(top + base) / 2, vrms=math.sqrt(sum(v * v for v in volts) / len(volts)),
        stddev=math.sqrt(sum((v - mean) ** 2 for v in volts) / len(volts)),
        overshoot=(high - top) / amplitude if amplitude > 0 else None,
        preshoot=(base - low) / amplitude if amplitude > 0 else None,
    )
    if amplitude <= max(4 * lsb, 1e-12):
        return result
    edges = _edges(volts, dt, base, top)
    rising = [e for e in edges if e.rising]
    falling = [e for e in edges if not e.rising]
    result["rise"] = _mean([e.duration for e in rising])
    result["fall"] = _mean([e.duration for e in falling])
    if len(rising) >= 2 or len(falling) >= 2:
        same = rising if len(rising) >= 2 else falling
        period = (same[-1].mid - same[0].mid) / (len(same) - 1)
        result["period"], result["frequency"] = period, 1 / period
    high_widths, low_widths = [], []
    for a, b in zip(edges, edges[1:]):
        (high_widths if a.rising else low_widths).append(b.mid - a.mid)
    result["pwidth"], result["nwidth"] = _mean(high_widths), _mean(low_widths)
    if result["period"] and result["pwidth"] is not None:
        result["pduty"] = result["pwidth"] / result["period"]
    if result["period"] and result["nwidth"] is not None:
        result["nduty"] = result["nwidth"] / result["period"]
    return result


_NAMES = ("vmax", "vmin", "vpp", "vavg", "vrms", "vtop", "vbase", "vamp", "vmid", "stddev", "overshoot", "preshoot",
          "period", "frequency", "rise", "fall", "pwidth", "nwidth", "pduty", "nduty")
