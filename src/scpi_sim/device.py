"""Custom devices under test that live on a bench.

A device has named terminals. An *input* terminal draws current from its net
(``current``); an *output* terminal drives its net like a supply (``voltage``).
After every instrument message the bench calls ``update()``, so a device can
notice its input voltage rising and start its own timeline (boot, inrush).

Methods the bench calls (``current``, ``voltage``, ``update``) run in the
instruments' threads: they must not block. ``current()`` must not call
``input_voltage()`` -- the input voltage is what is being computed from it.
"""

from __future__ import annotations

import re
import threading
from typing import Callable, ClassVar

_TERMINAL = re.compile(r"[A-Za-z0-9_]+")


class Console:
    """Text channel of a device, e.g. its serial console.

    Text written while nobody listens is dropped, like on a real serial line.
    A sink that raises is detached.
    """

    def __init__(self) -> None:
        self._sinks: list[Callable[[str], None]] = []
        self._lock = threading.Lock()

    def attach(self, sink: Callable[[str], None]) -> None:
        with self._lock:
            self._sinks.append(sink)

    def detach(self, sink: Callable[[str], None]) -> None:
        with self._lock:
            if sink in self._sinks:
                self._sinks.remove(sink)

    def write(self, text: str) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink(text)
            except Exception:
                self.detach(sink)


class Device:
    terminals: ClassVar[dict[str, str]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name, kind in cls.terminals.items():
            if not isinstance(name, str) or not _TERMINAL.fullmatch(name):
                raise TypeError(f"{cls.__name__}: invalid terminal name {name!r} (letters, digits, '_')")
            if kind not in ("input", "output"):
                raise TypeError(f"{cls.__name__}: terminal {name!r} must be 'input' or 'output', not {kind!r}")

    def __init__(self) -> None:
        self.console = Console()
        self._input_voltage: Callable[[str], float] = lambda terminal: 0.0

    def attach(self, input_voltage: Callable[[str], float]) -> None:
        """Called by the bench: how to look up the voltage at an input terminal."""
        self._input_voltage = input_voltage

    def input_voltage(self, terminal: str) -> float:
        return self._input_voltage(terminal)

    def current(self, terminal: str, volts: float) -> float:
        """Amps drawn on input ``terminal`` at ``volts``."""
        return 0.0

    def voltage(self, terminal: str) -> float | None:
        """Volts driven on output ``terminal``; None leaves the net floating."""
        return None

    def update(self) -> None:
        """Something on the bench may have changed (called after every instrument message)."""

    def console_line(self, line: str) -> None:
        """A line typed on the device's console."""
