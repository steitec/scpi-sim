"""HiSLIP (``TCPIP::host::hislip0::INSTR``): IVI-6.1 version 1.0, synchronous mode.

Each client opens two connections to the same port: the synchronous channel
(program messages and answers) and the asynchronous channel (device clear,
status byte, locks). The sub-address picks the instrument: ``hislip<N>`` by
position or ``hislip_<name>``.

Not supported: overlapped mode, HiSLIP 2.0 (TLS, authentication), service
requests. A shared lock request is granted as an exclusive lock.
"""

from __future__ import annotations

import asyncio
import itertools
import struct

from .instrument import Instrument
from .session import LOCKS, Disconnect, Session, find_instrument
from .transport import TcpServer

HEADER = struct.Struct(">2sBBIQ")  # prologue "HS", type, control code, parameter, payload length
VERSION = 0x0100  # 1.0
VENDOR_ID = 0x5353  # "SS"
MAX_MESSAGE = 1 << 20  # largest message (header included) the server accepts

(INITIALIZE, INITIALIZE_RESPONSE, FATAL_ERROR, ERROR, ASYNC_LOCK, ASYNC_LOCK_RESPONSE, DATA, DATA_END,
 DEVICE_CLEAR_COMPLETE, DEVICE_CLEAR_ACKNOWLEDGE, ASYNC_REMOTE_LOCAL_CONTROL, ASYNC_REMOTE_LOCAL_RESPONSE,
 TRIGGER, INTERRUPTED, ASYNC_INTERRUPTED, ASYNC_MAX_MSG_SIZE, ASYNC_MAX_MSG_SIZE_RESPONSE, ASYNC_INITIALIZE,
 ASYNC_INITIALIZE_RESPONSE, ASYNC_DEVICE_CLEAR, ASYNC_SERVICE_REQUEST, ASYNC_STATUS_QUERY, ASYNC_STATUS_RESPONSE,
 ASYNC_DEVICE_CLEAR_ACKNOWLEDGE, ASYNC_LOCK_INFO, ASYNC_LOCK_INFO_RESPONSE) = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25)

# fatal error codes
POORLY_FORMED_HEADER, INVALID_INITIALIZATION = 1, 3
# error codes
UNRECOGNIZED_MESSAGE_TYPE, MESSAGE_TOO_LARGE = 1, 4
# lock response control codes
LOCK_FAILURE, LOCK_SUCCESS, LOCK_ERROR = 0, 1, 3


def message(kind: int, control: int = 0, parameter: int = 0, payload: bytes = b"") -> bytes:
    return HEADER.pack(b"HS", kind, control, parameter, len(payload)) + payload


class _Fatal(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(text)
        self.code, self.text = code, text


class _HislipSession:
    def __init__(self, session_id: int, instrument: Instrument, sync: asyncio.StreamWriter):
        self.id = session_id
        self.instrument = instrument
        self.session = Session(instrument)
        self.sync = sync
        self.async_: asyncio.StreamWriter | None = None
        self.client_max = MAX_MESSAGE  # largest message the client accepts (AsyncMaxMsgSize)
        self.sending: asyncio.Task | None = None  # an answer waiting for its delay fault

    def close(self) -> None:
        LOCKS.release(self.instrument, self)
        for writer in (self.sync, self.async_):
            if writer is not None:
                writer.close()


class HislipServer(TcpServer):
    """HiSLIP for one or more instruments on one TCP port (default 4880)."""

    def __init__(self, instruments: dict[str, Instrument] | Instrument, host: str = "127.0.0.1",
                 port: int = 4880):
        super().__init__(host, port, name="scpi-sim-hislip")
        if isinstance(instruments, Instrument):
            instruments = {"hislip0": instruments}
        self.instruments = dict(instruments)
        self._sessions: dict[int, _HislipSession] = {}
        self._ids = (n & 0xFFFF or 1 for n in itertools.count(1))

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = None
        try:
            kind, control, parameter, payload = await self._receive(reader)
            if kind == INITIALIZE:
                session = self._initialize(parameter, payload, writer)
                writer.write(message(INITIALIZE_RESPONSE, 0, (VERSION << 16) | session.id))
                await writer.drain()
                await self._sync_channel(session, reader)
            elif kind == ASYNC_INITIALIZE:
                session = self._sessions.get(parameter & 0xFFFF)
                if session is None or session.async_ is not None:
                    raise _Fatal(INVALID_INITIALIZATION, "unknown session id")
                session.async_ = writer
                writer.write(message(ASYNC_INITIALIZE_RESPONSE, 0, VENDOR_ID))
                await writer.drain()
                await self._async_channel(session, reader)
            else:
                raise _Fatal(INVALID_INITIALIZATION, "expected Initialize or AsyncInitialize")
        except _Fatal as fatal:
            writer.write(message(FATAL_ERROR, fatal.code, 0, fatal.text.encode()))
            await writer.drain()
        except (asyncio.IncompleteReadError, Disconnect):
            pass
        finally:
            if session is not None:
                self._end(session)

    def _end(self, session: _HislipSession) -> None:
        """Either channel closed (or a disconnect fault): end the whole session."""
        if self._sessions.pop(session.id, None) is not None:
            if session.sending is not None:
                session.sending.cancel()
            session.close()

    async def _receive(self, reader: asyncio.StreamReader) -> tuple[int, int, int, bytes]:
        prologue, kind, control, parameter, length = HEADER.unpack(await reader.readexactly(HEADER.size))
        if prologue != b"HS":
            raise _Fatal(POORLY_FORMED_HEADER, "message does not start with HS")
        if length > MAX_MESSAGE:
            raise _Fatal(POORLY_FORMED_HEADER, "payload too large")
        return kind, control, parameter, await reader.readexactly(length)

    def _initialize(self, parameter: int, payload: bytes, writer: asyncio.StreamWriter) -> _HislipSession:
        name = payload.decode("ascii", errors="replace")
        instrument = find_instrument(self.instruments, name, "hislip", name_prefix="hislip_")
        if instrument is None:
            raise _Fatal(INVALID_INITIALIZATION, f"unknown sub-address {name!r}")
        session_id = next(self._ids)
        while session_id in self._sessions:
            session_id = next(self._ids)
        session = _HislipSession(session_id, instrument, writer)
        self._sessions[session_id] = session
        return session

    async def _sync_channel(self, session: _HislipSession, reader: asyncio.StreamReader) -> None:
        writer = session.sync
        while True:
            kind, control, parameter, payload = await self._receive(reader)
            if kind in (DATA, DATA_END):
                session.session.write(payload, end=kind == DATA_END)  # Disconnect ends the session
                if kind == DATA_END and session.session.has_output:
                    session.sending = asyncio.ensure_future(self._answer(session, parameter))
                    await asyncio.wait([session.sending])  # returns, not raises, when a device clear cancels it
                    session.sending = None
            elif kind == TRIGGER:
                pass  # no instrument model reacts to a group execute trigger
            elif kind == DEVICE_CLEAR_COMPLETE:
                session.session.clear()
                writer.write(message(DEVICE_CLEAR_ACKNOWLEDGE, 0))  # feature bitmap: no overlap
            else:
                writer.write(message(ERROR, UNRECOGNIZED_MESSAGE_TYPE, 0, b"unsupported message type"))
            await writer.drain()

    async def _answer(self, session: _HislipSession, message_id: int) -> None:
        """Send the answer once it is ready (a delay fault holds it back); a device clear cancels this."""
        while session.session.has_output:
            if await session.session.wait_output(0.05):
                data = session.session.read()
                chunk = max(1, session.client_max - HEADER.size)
                for start in range(0, len(data), chunk):
                    last = start + chunk >= len(data)
                    session.sync.write(message(DATA_END if last else DATA, 0, message_id, data[start:start + chunk]))
                await session.sync.drain()
                return

    async def _async_channel(self, session: _HislipSession, reader: asyncio.StreamReader) -> None:
        writer = session.async_
        while True:
            kind, control, parameter, payload = await self._receive(reader)
            if kind == ASYNC_MAX_MSG_SIZE:
                if len(payload) == 8:
                    session.client_max = max(HEADER.size + 1, struct.unpack(">Q", payload)[0])
                writer.write(message(ASYNC_MAX_MSG_SIZE_RESPONSE, 0, 0, struct.pack(">Q", MAX_MESSAGE)))
            elif kind == ASYNC_DEVICE_CLEAR:
                if session.sending is not None:
                    session.sending.cancel()
                session.session.clear()
                writer.write(message(ASYNC_DEVICE_CLEAR_ACKNOWLEDGE, 0))
            elif kind == ASYNC_STATUS_QUERY:
                writer.write(message(ASYNC_STATUS_RESPONSE, session.session.status_byte()))
            elif kind == ASYNC_LOCK:
                writer.write(message(ASYNC_LOCK_RESPONSE, await self._lock(session, control, parameter)))
            elif kind == ASYNC_LOCK_INFO:
                held = LOCKS.holder(session.instrument) is not None
                writer.write(message(ASYNC_LOCK_INFO_RESPONSE, int(held), int(held)))
            elif kind == ASYNC_REMOTE_LOCAL_CONTROL:
                writer.write(message(ASYNC_REMOTE_LOCAL_RESPONSE, 0))
            else:
                writer.write(message(ERROR, UNRECOGNIZED_MESSAGE_TYPE, 0, b"unsupported message type"))
            await writer.drain()

    async def _lock(self, session: _HislipSession, control: int, parameter: int) -> int:
        if control == 1:  # request; parameter: timeout in ms
            ok = await LOCKS.acquire(session.instrument, session, parameter / 1000)
            return LOCK_SUCCESS if ok else LOCK_FAILURE
        if control == 0:  # release
            return LOCK_SUCCESS if LOCKS.release(session.instrument, session) else LOCK_ERROR
        return LOCK_ERROR
