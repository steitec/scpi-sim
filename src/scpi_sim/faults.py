"""Faults that can be injected into a running instrument.

=============  ===================================  ==========================================
kind           parameters                           effect
=============  ===================================  ==========================================
no_response    count=1                              the next ``count`` answers are not sent
delay          seconds (> 0), count=None (forever)  each answer is sent ``seconds`` later
disconnect     -                                    the next message closes the connection
error          code (< 0), message=None             one entry pushed into the error queue
idn            value                                ``*IDN?`` answers ``value``
offset         value                                multimeter only: added to every reading
noise          sigma (>= 0)                         multimeter only: replaces the noise
=============  ===================================  ==========================================
"""

from __future__ import annotations

from typing import Any, Callable

_REQUIRED = object()


def _count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or int(value) != value or value < 1:
        raise ValueError("must be a whole number >= 1")
    return int(value)


def _positive(value: Any) -> float:
    if isinstance(value, bool) or float(value) <= 0:
        raise ValueError("must be > 0")
    return float(value)


def _non_negative(value: Any) -> float:
    if isinstance(value, bool) or float(value) < 0:
        raise ValueError("must be >= 0")
    return float(value)


def _negative_int(value: Any) -> int:
    if isinstance(value, bool) or int(value) != value or value >= 0:
        raise ValueError("must be a negative error code")
    return int(value)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("must be a number")
    return float(value)


def _text(value: Any) -> str:
    return str(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


# kind -> {parameter: (default or _REQUIRED, converter)}
FAULTS: dict[str, dict[str, tuple[Any, Callable[[Any], Any]]]] = {
    "no_response": {"count": (1, _count)},
    "delay": {"seconds": (_REQUIRED, _positive), "count": (None, _count)},
    "disconnect": {},
    "error": {"code": (_REQUIRED, _negative_int), "message": (None, _optional_text)},
    "idn": {"value": (_REQUIRED, _text)},
    "offset": {"value": (_REQUIRED, _number)},
    "noise": {"sigma": (_REQUIRED, _non_negative)},
}
FAULT_KINDS = tuple(FAULTS)


def validate(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return the complete, converted parameters, or raise ValueError."""
    spec = FAULTS.get(kind)
    if spec is None:
        raise ValueError(f"unknown fault {kind!r} (allowed: {', '.join(FAULT_KINDS)})")
    allowed = ", ".join(spec) or "none"
    unknown = set(params) - set(spec)
    if unknown:
        raise ValueError(f"fault {kind!r}: unknown parameter {sorted(unknown)[0]!r} (parameters: {allowed})")
    result = {}
    for name, (default, convert) in spec.items():
        if name not in params:
            if default is _REQUIRED:
                raise ValueError(f"fault {kind!r}: missing parameter {name!r} (parameters: {allowed})")
            result[name] = default
            continue
        try:
            result[name] = convert(params[name])
        except (TypeError, ValueError) as error:
            raise ValueError(f"fault {kind!r}: {name} {error}") from None
    return result
