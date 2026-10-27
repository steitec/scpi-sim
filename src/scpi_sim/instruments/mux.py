"""A generic relay multiplexer card in slot 1 (channels 101, 102, ...)."""

from __future__ import annotations

import contextlib
from collections.abc import Collection
from typing import Callable, ContextManager

from ..instrument import Call, Instrument, scpi
from ..scpi.params import parse_channel_list


class Multiplexer(Instrument):
    model = "MUX"
    serial = "SIM00003"

    def __init__(self, *, channels: int = 16, valid_channels: Collection[int] | None = None,
                 serial: str | None = None):
        """``valid_channels`` (e.g. several cards: 101-120, 201-220) replaces ``channels``."""
        super().__init__(serial=serial)
        if valid_channels is not None:
            self.valid_channels: Collection[int] = frozenset(valid_channels)
        else:
            if not 1 <= channels <= 99:
                raise ValueError("channels must be between 1 and 99")
            self.valid_channels = range(101, 101 + channels)
        self.closed: set[int] = set()
        # called with the would-be set of closed channels before ROUT:CLOS changes
        # anything; may raise ScpiError to refuse (used by the bench)
        self.before_close: Callable[[set[int]], None] | None = None
        # held around check + switch; a bench shares one lock between all its multiplexers
        self.switch_lock: ContextManager = contextlib.nullcontext()
        # ``closed`` is only ever replaced, never changed in place, so other
        # threads (the bench) can read it without this instrument's lock

    def reset(self) -> None:
        super().reset()
        self.closed = set()

    def common_pin(self, channel: int) -> str:
        """The terminal a closed ``channel`` connects to (one common for the whole card)."""
        return "common"

    @property
    def common_pins(self) -> tuple[str, ...]:
        return ("common",)

    def _channels(self, call: Call) -> list[int]:
        return parse_channel_list(call.arg(0), valid=self.valid_channels)

    @scpi("ROUTe:CLOSe")
    def close(self, call: Call) -> None:
        channels = set(self._channels(call))
        with self.switch_lock:
            closed = self.closed | channels
            if self.before_close is not None:
                self.before_close(closed)
            self.closed = closed

    @scpi("ROUTe:OPEN")
    def open(self, call: Call) -> None:
        channels = set(self._channels(call))
        with self.switch_lock:
            self.closed = self.closed - channels

    @scpi("ROUTe:CLOSe?", max_args=1)
    def close_query(self, call: Call) -> list[bool]:
        return [ch in self.closed for ch in self._channels(call)]

    @scpi("ROUTe:OPEN?", max_args=1)
    def open_query(self, call: Call) -> list[bool]:
        return [ch not in self.closed for ch in self._channels(call)]

    @scpi("ROUTe:CLOSe:STATe?")
    def close_state(self, call: Call) -> str:
        return "(@" + ",".join(str(ch) for ch in sorted(self.closed)) + ")"

    @scpi("ROUTe:OPEN:ALL", max_args=0)
    def open_all(self, call: Call) -> None:
        with self.switch_lock:
            self.closed = set()
