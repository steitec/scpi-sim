"""Parsing of SCPI parameters and formatting of numeric responses."""

from __future__ import annotations

import re
from collections.abc import Collection

from ..errors import ScpiError

_NUMBER = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)")
_PREFIXES = {"U": 1e-6, "M": 1e-3, "K": 1e3}
_KEYWORDS = {"MIN": "minimum", "MINIMUM": "minimum", "MAX": "maximum",
             "MAXIMUM": "maximum", "DEF": "default", "DEFAULT": "default"}


def _scale(suffix: str, unit: str | None) -> float:
    if not suffix:
        return 1.0
    if unit is not None:
        if suffix == unit:
            return 1.0
        if unit == "OHM" and suffix == "MOHM":  # SCPI: MOHM is megaohm
            return 1e6
        if suffix[1:] == unit and suffix[0] in _PREFIXES:
            return _PREFIXES[suffix[0]]
    raise ScpiError(-131)


def parse_number(arg: str, unit: str | None = None, *, minimum: float,
                 maximum: float, default: float) -> float:
    """Parse a numeric parameter, with MIN/MAX/DEF and an optional unit suffix."""
    keyword = _KEYWORDS.get(arg.strip().upper())
    if keyword is not None:
        return {"minimum": minimum, "maximum": maximum, "default": default}[keyword]
    m = _NUMBER.fullmatch(arg.strip())
    if not m:
        raise ScpiError(-104)
    value = float(m.group(1)) * _scale(m.group(2).upper(), unit)
    if not minimum <= value <= maximum:
        raise ScpiError(-222)
    return value


def parse_bool(arg: str) -> bool:
    value = arg.strip().upper()
    if value in ("ON", "1"):
        return True
    if value in ("OFF", "0"):
        return False
    raise ScpiError(-224)


def parse_channel_list(arg: str, valid: Collection[int] | None = None) -> list[int]:
    """``(@101,103:105)`` -> ``[101, 103, 104, 105]``.

    With ``valid``, every channel outside it gives -222 — checked on the bounds
    before expanding, so ``(@1:999999999)`` costs nothing; a ``valid`` that is
    not a ``range`` (a card with gaps) is also checked channel by channel.
    """
    m = re.fullmatch(r"\(\s*@([\d\s,:]+)\)", arg.strip())
    if not m:
        raise ScpiError(-224)
    channels: list[int] = []
    for part in m.group(1).split(","):
        bounds = [b.strip() for b in part.split(":")]
        if len(bounds) > 2 or not all(b.isdigit() for b in bounds):
            raise ScpiError(-224)
        first, last = int(bounds[0]), int(bounds[-1])
        if valid is not None and (first not in valid or last not in valid):
            raise ScpiError(-222)
        step = 1 if last >= first else -1
        expanded = range(first, last + step, step)
        if valid is not None and not isinstance(valid, range) and any(ch not in valid for ch in expanded):
            raise ScpiError(-222)
        channels.extend(expanded)
    return channels


def format_number(value: float) -> str:
    return f"{value:+.8E}"
