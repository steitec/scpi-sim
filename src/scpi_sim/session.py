"""What VXI-11 links and HiSLIP sessions share: message handling, output buffer, locks.

A client writes program messages; complete lines (or a write that ends the
message) run through ``Instrument.process()``. Answers wait in an output
buffer until the client reads them, like on a real instrument with a message
exchange protocol (unlike the raw socket, which sends every answer at once).
"""

from __future__ import annotations

import asyncio
import threading
import time
import weakref

from .errors import ScpiError
from .instrument import Instrument
from .transport import MAX_LINE


class Disconnect(Exception):
    """The ``disconnect`` fault fired: the transport closes the connection."""


class Session:
    """One client connection to one instrument."""

    def __init__(self, instrument: Instrument):
        self.instrument = instrument
        self._input = bytearray()
        self._discarding = False  # inside an oversized message, dropping bytes up to the next newline
        self._output = bytearray()
        self._ready_at = 0.0  # monotonic time from which the output may be read (delay fault)

    def write(self, data: bytes, end: bool = True) -> None:
        """Take bytes from the client; ``end``: they end the program message. Raises Disconnect."""
        self._input += data
        while True:
            newline = self._input.find(b"\n")
            if newline < 0:
                break
            raw = bytes(self._input[:newline])
            del self._input[:newline + 1]
            if self._discarding:
                self._discarding = False
                continue
            self._run(raw)
        if end and self._input:
            raw = bytes(self._input)
            self._input.clear()
            if self._discarding:
                self._discarding = False
            else:
                self._run(raw)
        elif len(self._input) > MAX_LINE:
            if not self._discarding:
                self.instrument.push_error(ScpiError(-223))
            self._discarding = True
            self._input.clear()

    def _run(self, raw: bytes) -> None:
        if len(raw) > MAX_LINE:
            self.instrument.push_error(ScpiError(-223))
            return
        line = raw.decode("utf-8", errors="replace").rstrip("\r")
        if not line.strip():
            return
        if self.instrument.take_fault("disconnect") is not None:
            raise Disconnect
        if self._output:  # IEEE 488.2: a new message before the answer was read
            self._output.clear()
            self._ready_at = 0.0
            self.instrument.push_error(ScpiError(-410))
        response = self.instrument.process(line)
        if response is None:
            return
        delay = self.instrument.take_fault("delay")
        if delay is not None:
            self._ready_at = time.monotonic() + delay["seconds"]
        self._output += response.encode() + b"\n"

    @property
    def has_output(self) -> bool:
        return bool(self._output)

    async def wait_output(self, timeout: float) -> bool:
        """Wait until output can be read; False after ``timeout`` seconds without any."""
        deadline = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            if self._output and now >= self._ready_at:
                return True
            if now >= deadline:
                return False
            wake = deadline if not self._output else min(deadline, self._ready_at)
            await asyncio.sleep(min(wake - now, 0.05))

    def read(self, size: int | None = None) -> bytes:
        """Take up to ``size`` bytes of output (all without ``size``)."""
        size = len(self._output) if size is None else size
        data = bytes(self._output[:size])
        del self._output[:size]
        return data

    def unread(self, data: bytes) -> None:
        """Put bytes taken by ``read()`` back in front of the output."""
        self._output[:0] = data

    def status_byte(self) -> int:
        return self.instrument.status_byte(mav=bool(self._output))

    def clear(self) -> None:
        """Device clear: drop pending input and output (error queue and settings stay)."""
        self._input.clear()
        self._output.clear()
        self._discarding = False
        self._ready_at = 0.0


class Locks:
    """Exclusive instrument locks, shared by all transports (owners are any objects)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._owners: weakref.WeakKeyDictionary[Instrument, object] = weakref.WeakKeyDictionary()

    def try_acquire(self, instrument: Instrument, owner: object) -> bool:
        with self._guard:
            holder = self._owners.get(instrument)
            if holder is not None and holder is not owner:
                return False
            self._owners[instrument] = owner
            return True

    async def acquire(self, instrument: Instrument, owner: object, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for the lock."""
        deadline = time.monotonic() + timeout
        while not self.try_acquire(instrument, owner):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)
        return True

    def release(self, instrument: Instrument, owner: object) -> bool:
        """Release the lock if ``owner`` holds it; False if it does not."""
        with self._guard:
            if self._owners.get(instrument) is not owner:
                return False
            del self._owners[instrument]
            return True

    def holder(self, instrument: Instrument) -> object | None:
        with self._guard:
            return self._owners.get(instrument)

    def blocked(self, instrument: Instrument, owner: object) -> bool:
        """True if someone else holds the lock."""
        holder = self.holder(instrument)
        return holder is not None and holder is not owner

    async def wait_free(self, instrument: Instrument, owner: object, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds until nobody else holds the lock."""
        deadline = time.monotonic() + timeout
        while self.blocked(instrument, owner):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)
        return True


LOCKS = Locks()


def find_instrument(instruments: dict[str, Instrument], device: str, index_prefix: str,
                    name_prefix: str = "") -> Instrument | None:
    """The instrument a LAN device name selects: ``<index_prefix><N>`` (position) or ``<name_prefix><name>``."""
    device = device.strip().lower()
    if device.startswith(index_prefix) and device[len(index_prefix):].isdigit():
        index = int(device[len(index_prefix):])
        return list(instruments.values())[index] if index < len(instruments) else None
    if name_prefix and device.startswith(name_prefix):
        device = device[len(name_prefix):]
    return instruments.get(device)
