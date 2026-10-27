"""Converters for instrument options in bench files (shared by the bench and profiles)."""

from __future__ import annotations

import math


def whole(low: int, high: int | None = None):
    def convert(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
            raise ValueError(f"must be a whole number {low}-{high}" if high else f"must be a whole number >= {low}")
        return value
    return convert


def real(low: float | None = None, above: float | None = None):
    def convert(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or (low is not None and value < low) or (above is not None and value <= above):
            bound = f" >= {low}" if low is not None else f" > {above}" if above is not None else ""
            raise ValueError(f"must be a number{bound}")
        return float(value)
    return convert
