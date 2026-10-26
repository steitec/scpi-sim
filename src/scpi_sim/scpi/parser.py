"""Split a SCPI program message into commands.

Compound messages (``SOUR:VOLT 5;CURR 1``) follow the SCPI-1999 path rule: a
command without a leading colon continues at the node of the previous one
(``SOUR:CURR``), a leading colon starts again at the root, and common
commands (``*OPC``) leave the current path alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import ScpiError

_HEADER = re.compile(r"\*[A-Za-z]+\??|[A-Za-z][A-Za-z0-9]*(?::[A-Za-z][A-Za-z0-9]*)*\??")


@dataclass(frozen=True)
class Command:
    header: tuple[str, ...]
    is_query: bool
    args: tuple[str, ...]


def _split(text: str, sep: str) -> list[str]:
    """Split at ``sep`` outside double quotes and parentheses."""
    parts, current, quoted, depth = [], [], False, 0
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == sep and not quoted and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _balanced(text: str) -> bool:
    quoted, depth = False, 0
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return not quoted and depth == 0


def _parse_unit(unit: str, path: tuple[str, ...]) -> Command:
    header, _, rest = unit.partition(" ")
    if "\t" in header:
        header, _, more = header.partition("\t")
        rest = more + " " + rest
    absolute = header.startswith(":")
    if absolute:
        header = header[1:]
    if not _HEADER.fullmatch(header) or not _balanced(rest):
        raise ScpiError(-102)
    is_query = header.endswith("?")
    tokens = tuple(header.rstrip("?").upper().split(":"))
    if not tokens[0].startswith("*") and not absolute:
        tokens = path + tokens
    rest = rest.strip()
    args: tuple[str, ...] = ()
    if rest:
        args = tuple(a.strip() for a in _split(rest, ","))
        if any(not a for a in args):
            raise ScpiError(-102)
    return Command(tokens, is_query, args)


def parse_message(line: str) -> list[Command | ScpiError]:
    """Parse one program message; failed units are returned as ScpiError in place."""
    units = _split(line, ";")
    if units and not units[-1].strip():
        units.pop()  # one trailing ";" (or an empty line) is fine
    result: list[Command | ScpiError] = []
    path: tuple[str, ...] = ()
    for unit in units:
        unit = unit.strip()
        try:
            if not unit:
                raise ScpiError(-102)
            command = _parse_unit(unit, path)
        except ScpiError as error:
            result.append(error)
            continue
        result.append(command)
        if not command.header[0].startswith("*"):
            path = command.header[:-1]
    return result
