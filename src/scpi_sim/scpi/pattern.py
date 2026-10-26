"""SCPI header patterns as written in programming manuals.

Syntax:

- keywords are separated by ``:``; the uppercase letters of a keyword are its
  short form, the whole keyword is its long form (``MEASure`` matches ``MEAS``
  and ``MEASURE``, but not ``MEASU``)
- ``[...]`` marks an optional node: ``[SOURce:]VOLTage[:LEVel]``
- a trailing ``#`` on a keyword allows a numeric suffix: ``OUTPut#[:STATe]``
  matches ``OUTP2`` (suffix 2) and ``OUTP`` (no suffix, reported as ``None``)
- a trailing ``?`` makes the pattern a query
- ``*XXX`` patterns are IEEE 488.2 common commands, matched as a whole
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NODE = re.compile(r"\[:?([A-Za-z]+#?):?\]|([A-Za-z]+#?)")


@dataclass(frozen=True)
class _Keyword:
    short: str
    long: str
    suffix: bool
    optional: bool

    def match(self, token: str) -> tuple[bool, int | None]:
        if self.suffix:
            m = re.fullmatch(r"([A-Z]+)(\d*)", token)
            if not m or m.group(1) not in (self.short, self.long):
                return False, None
            return True, int(m.group(2)) if m.group(2) else None
        return token in (self.short, self.long), None


class HeaderPattern:
    def __init__(self, pattern: str):
        self.pattern = pattern
        self.is_query = pattern.endswith("?")
        body = pattern[:-1] if self.is_query else pattern
        self._common = body.upper() if body.startswith("*") else None
        self._keywords: list[_Keyword] = []
        if self._common is None:
            pos = 0
            for m in _NODE.finditer(body):
                if body[pos:m.start()].strip(":"):
                    raise ValueError(f"bad SCPI pattern: {pattern!r}")
                pos = m.end()
                name = m.group(1) or m.group(2)
                suffix = name.endswith("#")
                name = name.rstrip("#")
                short = re.match(r"[A-Z0-9]*", name).group(0)
                self._keywords.append(
                    _Keyword(short, name.upper(), suffix, m.group(1) is not None)
                )
            if body[pos:].strip(":") or not self._keywords:
                raise ValueError(f"bad SCPI pattern: {pattern!r}")

    def match(self, header: tuple[str, ...], is_query: bool) -> tuple[int | None, ...] | None:
        """Return the numeric suffixes of ``#`` keywords, or None if no match."""
        if is_query != self.is_query:
            return None
        if self._common is not None:
            return () if len(header) == 1 and header[0].upper() == self._common else None
        return self._match(0, tuple(t.upper() for t in header), 0)

    def _match(self, k: int, header: tuple[str, ...], t: int) -> tuple[int | None, ...] | None:
        if k == len(self._keywords):
            return () if t == len(header) else None
        keyword = self._keywords[k]
        if t < len(header):
            ok, suffix = keyword.match(header[t])
            if ok:
                rest = self._match(k + 1, header, t + 1)
                if rest is not None:
                    return ((suffix,) if keyword.suffix else ()) + rest
        if keyword.optional:
            rest = self._match(k + 1, header, t)
            if rest is not None:
                return ((None,) if keyword.suffix else ()) + rest
        return None

    def __repr__(self) -> str:
        return f"HeaderPattern({self.pattern!r})"
