"""Base class for simulated instruments.

Subclasses register commands with the ``@scpi`` decorator; this class does the
dispatching and implements what every IEEE 488.2 / SCPI instrument has: the
common commands, the error queue and the status registers.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, ClassVar

from . import __version__
from .errors import ScpiError
from .faults import validate as validate_fault
from .scpi.params import format_number, parse_number
from .scpi.parser import parse_message
from .scpi.pattern import HeaderPattern

# Standard Event Status Register bits
OPC, QYE, DDE, EXE, CME, PON = 1, 4, 8, 16, 32, 128


@dataclass(frozen=True)
class Call:
    args: tuple[str, ...]
    suffixes: tuple[int | None, ...]

    def arg(self, i: int) -> str:
        if i >= len(self.args):
            raise ScpiError(-109)
        return self.args[i]

    def optional_arg(self, i: int, default: str | None = None) -> str | None:
        return self.args[i] if i < len(self.args) else default


def scpi(pattern: str, max_args: int | None = None) -> Callable:
    """Register a method as the handler for a SCPI header pattern.

    More than ``max_args`` parameters give -108; the default is 0 for a query
    and 1 for a setting command.
    """
    def decorate(method: Callable) -> Callable:
        method._scpi_pattern = HeaderPattern(pattern)
        method._scpi_max_args = max_args if max_args is not None else (0 if pattern.endswith("?") else 1)
        return method
    return decorate


def _format(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, (list, tuple)):
        return ",".join(_format(v) for v in value)
    return str(value)


def _event_bit(code: int) -> int:
    if -199 <= code <= -100:
        return CME
    if -299 <= code <= -200:
        return EXE
    if -399 <= code <= -300:
        return DDE
    if -499 <= code <= -400:
        return QYE
    return 0


class Instrument:
    model: ClassVar[str] = "GENERIC"
    serial: ClassVar[str] = "SIM00000"
    ERROR_QUEUE_SIZE = 20
    # faults that only make sense for some instruments
    SPECIFIC_FAULTS: ClassVar[frozenset[str]] = frozenset()

    _commands: ClassVar[list[tuple[HeaderPattern, str, int]]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        commands, seen = [], set()
        for klass in cls.__mro__:
            for name, attr in vars(klass).items():
                if name in seen:
                    continue
                seen.add(name)
                pattern = getattr(attr, "_scpi_pattern", None)
                if pattern is not None:
                    commands.append((pattern, name, attr._scpi_max_args))
        cls._commands = commands

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._errors: list[ScpiError] = []
        self._esr = PON
        self._ese = 0
        self._sre = 0
        self._faults: dict[str, dict] = {}
        # called after every message, outside the lock (a bench updates its devices here)
        self.listeners: list[Callable[[], None]] = []

    @property
    def idn(self) -> str:
        return f"scpi-sim,{self.model},{self.serial},{__version__}"

    def reset(self) -> None:
        """Bring device settings to their *RST state (not the status system)."""

    def push_error(self, error: ScpiError) -> None:
        with self._lock:
            self._esr |= _event_bit(error.code)
            if len(self._errors) < self.ERROR_QUEUE_SIZE:
                self._errors.append(error)
            elif self._errors[-1].code != -350:
                self._errors[-1] = ScpiError(-350)

    # --- fault injection ---------------------------------------------------------

    def inject(self, kind: str, **params) -> None:
        """Arm a fault (see scpi_sim.faults); raises ValueError for bad input."""
        if kind in ("offset", "noise") and kind not in self.SPECIFIC_FAULTS:
            raise ValueError(f"fault {kind!r} is not supported by the {self.model}")
        params = validate_fault(kind, params)
        with self._lock:
            if kind == "error":
                self.push_error(ScpiError(params["code"], params["message"]))
            else:
                self._faults[kind] = params

    def clear_faults(self) -> None:
        with self._lock:
            self._faults.clear()

    @property
    def faults(self) -> dict[str, dict]:
        with self._lock:
            return {kind: dict(params) for kind, params in self._faults.items()}

    def take_fault(self, kind: str) -> dict | None:
        """Return an armed fault's parameters and use it up once (count / one-shot)."""
        with self._lock:
            params = self._faults.get(kind)
            if params is None:
                return None
            if kind == "disconnect":
                del self._faults[kind]
            elif params.get("count") is not None:
                params["count"] -= 1
                if params["count"] <= 0:
                    del self._faults[kind]
            return dict(params)

    def process(self, line: str) -> str | None:
        """Execute one program message; return the joined query responses."""
        responses = []
        with self._lock:
            for command in parse_message(line):
                if isinstance(command, ScpiError):
                    self.push_error(command)
                    continue
                try:
                    response = self._dispatch(command.header, command.is_query, command.args)
                except ScpiError as error:
                    self.push_error(error)
                    continue
                except Exception as error:  # a bug in a device model must not kill the server
                    self.push_error(ScpiError(-300, f"{type(error).__name__}: {error}"))
                    continue
                if response is not None:
                    responses.append(response)
            if responses and self.take_fault("no_response") is not None:
                responses = []
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:  # listeners report their own problems
                pass
        return ";".join(responses) if responses else None

    def _dispatch(self, header: tuple[str, ...], is_query: bool, args: tuple[str, ...]) -> str | None:
        for pattern, name, max_args in self._commands:
            suffixes = pattern.match(header, is_query)
            if suffixes is not None:
                if len(args) > max_args:
                    raise ScpiError(-108)
                return _format(getattr(self, name)(Call(args, suffixes)))
        raise ScpiError(-113)

    # --- IEEE 488.2 common commands -----------------------------------------

    @scpi("*IDN?")
    def _idn(self, call: Call) -> str:
        fault = self._faults.get("idn")
        return fault["value"] if fault else self.idn

    @scpi("*RST", max_args=0)
    def _rst(self, call: Call) -> None:
        self.reset()

    @scpi("*CLS", max_args=0)
    def _cls(self, call: Call) -> None:
        self._errors.clear()
        self._esr = 0

    @scpi("*ESR?")
    def _esr_query(self, call: Call) -> int:
        value, self._esr = self._esr, 0
        return value

    @scpi("*ESE")
    def _ese_set(self, call: Call) -> None:
        self._ese = round(parse_number(call.arg(0), minimum=0, maximum=255, default=0))

    @scpi("*ESE?")
    def _ese_query(self, call: Call) -> int:
        return self._ese

    @scpi("*SRE")
    def _sre_set(self, call: Call) -> None:
        self._sre = round(parse_number(call.arg(0), minimum=0, maximum=255, default=0))

    @scpi("*SRE?")
    def _sre_query(self, call: Call) -> int:
        return self._sre

    @scpi("*STB?")
    def _stb(self, call: Call) -> int:
        stb = 0
        if self._errors:
            stb |= 4
        if self._esr & self._ese:
            stb |= 32
        if stb & self._sre & ~64:
            stb |= 64
        return stb

    @scpi("*OPC", max_args=0)
    def _opc(self, call: Call) -> None:
        self._esr |= OPC  # every operation completes immediately

    @scpi("*OPC?")
    def _opc_query(self, call: Call) -> int:
        return 1

    @scpi("*WAI", max_args=0)
    def _wai(self, call: Call) -> None:
        pass

    @scpi("*TST?")
    def _tst(self, call: Call) -> int:
        return 0

    # --- SCPI SYSTem subsystem ----------------------------------------------

    @scpi("SYSTem:ERRor[:NEXT]?")
    def _syst_err(self, call: Call) -> str:
        return str(self._errors.pop(0) if self._errors else ScpiError(0))

    @scpi("SYSTem:ERRor:COUNt?")
    def _syst_err_count(self, call: Call) -> int:
        return len(self._errors)

    @scpi("SYSTem:VERSion?")
    def _syst_version(self, call: Call) -> str:
        return "1999.0"
