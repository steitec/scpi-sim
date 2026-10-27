"""Data acquisition units: a relay multiplexer with a built-in multimeter on its common.

The built-in multimeter's input is the card's ``common`` terminal; on a bench it
is wired there automatically, so ``daq.101 -> board.3V3`` is all the wiring a
measurement needs. Measuring a channel list sweeps it in ascending order: every
channel of the cards involved is opened, then each channel is closed, read and
opened again -- so a measurement sees one channel only, and the bench's relay
rules (two sources on one net) apply to every step.
"""

from __future__ import annotations

import re
from typing import ClassVar

from ..errors import ScpiError
from ..instrument import Call
from ..instruments.dmm import Multimeter
from ..instruments.mux import Multiplexer
from ..scpi.params import parse_channel_list

_CHANNEL_LIST = re.compile(r"\(\s*@[^)]*\)")


class DaqMultiplexer(Multiplexer):
    kind: ClassVar[str] = "mux"
    profile: ClassVar[str]
    description: ClassVar[str]
    options: ClassVar[dict]
    SPECIFIC_FAULTS = frozenset({"offset", "noise"})  # act on the built-in multimeter
    SLOTS: ClassVar[int]                 # card slots
    CHANNELS_PER_CARD: ClassVar[int]
    DMM_RANGES: ClassVar[dict[str, tuple[str, tuple[float, ...]]]]
    FUNCTIONS: ClassVar[tuple[str, ...]] = ("VOLT", "RES")

    def __init__(self, *, cards: int = 1, noise: float = 50e-6, seed: int | None = None,
                 serial: str | None = None):
        if not 1 <= cards <= self.SLOTS:
            raise ValueError(f"cards must be 1-{self.SLOTS}")
        self.cards = cards
        self.dmm = Multimeter(noise=noise, seed=seed)
        self.dmm.RANGES = self.DMM_RANGES
        channels = {slot * 100 + n for slot in range(1, cards + 1) for n in range(1, self.CHANNELS_PER_CARD + 1)}
        super().__init__(valid_channels=channels, serial=serial)
        self.reset()

    def reset(self) -> None:
        super().reset()
        self.dmm.reset()
        self._config: dict[int, tuple[str, str | None]] = {}  # channel -> (function, range argument)
        self._scan: list[int] = []
        self._readings: list[float] | None = None

    # --- faults go to the built-in multimeter --------------------------------------

    def inject(self, kind: str, **params) -> None:
        if kind in self.SPECIFIC_FAULTS:
            self.dmm.inject(kind, **params)
        else:
            super().inject(kind, **params)

    def clear_faults(self) -> None:
        super().clear_faults()
        self.dmm.clear_faults()

    @property
    def faults(self) -> dict[str, dict]:
        return {**super().faults, **self.dmm.faults}

    # --- helpers --------------------------------------------------------------------

    def _split(self, call: Call, required: bool = True) -> tuple[list[str], list[int] | None]:
        """Parameters before the channel list, and the channel list (sorted, as the instrument scans)."""
        text = ",".join(call.args)
        match = _CHANNEL_LIST.search(text)
        if match is None:
            if required:
                raise ScpiError(-109)
            return [p.strip() for p in text.split(",") if p.strip()], None
        params = [p.strip() for p in text[:match.start()].split(",") if p.strip()]
        if text[match.end():].strip(" ,"):
            raise ScpiError(-108)
        if re.fullmatch(r"\(\s*@\s*\)", match.group()):
            return params, []
        return params, sorted(set(parse_channel_list(match.group(), valid=self.valid_channels)))

    def _configure(self, function: str, call: Call) -> list[int]:
        params, channels = self._split(call)
        if len(params) > 2:
            raise ScpiError(-108)
        range_arg = params[0] if params else None
        self.dmm._configure(function, range_arg)  # validates the range before anything changes
        for channel in channels:
            self._config[channel] = (function, range_arg)
        self._scan = channels
        self._readings = None
        return channels

    def _sweep(self, channels: list[int]) -> list[float]:
        """Close each channel alone, read it, open it; the cards involved end up open."""
        cards = {channel // 100 for channel in channels}
        readings = []
        with self.switch_lock:
            others = frozenset(ch for ch in self.closed if ch // 100 not in cards)
            try:
                for channel in channels:
                    closed = set(others | {channel})
                    if self.before_close is not None:
                        self.before_close(closed)
                    self.closed = closed
                    function, range_arg = self._config.get(channel, ("VOLT", None))
                    self.dmm._configure(function, range_arg)
                    readings.append(self.dmm._read())
            finally:
                self.closed = set(others)
        self._readings = readings
        return readings

    def _check_manual_switching(self, channels: list[int]) -> None:
        busy = {channel // 100 for channel in self._scan}
        if any(channel // 100 in busy for channel in channels):
            raise ScpiError(-221, "Settings conflict;channel is part of the scan list")

    # --- building blocks for the profiles' command handlers ---------------------------
    # (a handler must carry its own @scpi: an undecorated override hides the base command)

    def _close_manual(self, call: Call) -> None:
        """ROUT:CLOS, refused on cards that belong to the scan list."""
        self._check_manual_switching(self._channels(call))
        Multiplexer.close(self, call)

    def _scan_list_block(self) -> str:
        text = "(@" + ",".join(str(ch) for ch in self._scan) + ")"
        return f"#{len(str(len(text)))}{len(text)}{text}"

    def _route_scan(self, call: Call) -> None:
        params, channels = self._split(call)
        if params:
            raise ScpiError(-108)
        self._scan = channels
        self._readings = None

    def _initiate(self) -> None:
        if not self._scan:
            raise ScpiError(-221, "Settings conflict;empty scan list")
        self._sweep(self._scan)

    def _fetch(self) -> list[float]:
        if self._readings is None:
            raise ScpiError(-230)
        return self._readings
