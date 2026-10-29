"""VXI-11 (``TCPIP::host::inst0::INSTR``): ONC RPC servers for the instruments.

- ``Vxi11Server``: core channel (DEVICE_CORE) and abort channel (DEVICE_ASYNC)
  on one TCP port. The LAN device name picks the instrument: ``inst<N>`` by
  position or the instrument name.
- ``PortMapper``: the ONC RPC port mapper (program 100000, version 2) on TCP and
  UDP, usually port 111, so clients find the core port without being told.

Not supported: the interrupt channel (no SRQ), ``device_docmd``, RPC over UDP
for the core channel.
"""

from __future__ import annotations

import asyncio
import itertools
import struct

from .instrument import Instrument
from .session import LOCKS, Disconnect, Session, find_instrument
from .transport import TcpServer

PMAP_PROG, PMAP_VERS = 100000, 2
PMAP_NULL, PMAP_GETPORT, PMAP_DUMP = 0, 3, 4
IPPROTO_TCP, IPPROTO_UDP = 6, 17

DEVICE_CORE_PROG, DEVICE_ASYNC_PROG, DEVICE_INTR_PROG, DEVICE_VERS = 0x0607AF, 0x0607B0, 0x0607B1, 1
CREATE_LINK, DEVICE_WRITE, DEVICE_READ, DEVICE_READSTB, DEVICE_TRIGGER, DEVICE_CLEAR = 10, 11, 12, 13, 14, 15
DEVICE_REMOTE, DEVICE_LOCAL, DEVICE_LOCK, DEVICE_UNLOCK, DEVICE_ENABLE_SRQ = 16, 17, 18, 19, 20
DEVICE_DOCMD, DESTROY_LINK, CREATE_INTR_CHAN, DESTROY_INTR_CHAN = 22, 23, 25, 26
DEVICE_ABORT = 1

# device error codes
NO_ERROR, DEVICE_NOT_ACCESSIBLE, INVALID_LINK, OPERATION_NOT_SUPPORTED = 0, 3, 4, 8
CHANNEL_NOT_ESTABLISHED, LOCKED_BY_ANOTHER_LINK, NO_LOCK_HELD, IO_TIMEOUT = 6, 11, 12, 15

OP_FLAG_WAITLOCK, OP_FLAG_END, OP_FLAG_TERMCHAR_SET = 1, 8, 128
RX_REQCNT, RX_CHR, RX_END = 1, 2, 4

MAX_RECV_SIZE = 1 << 20
MAX_RECORD = 16 << 20  # refuse larger RPC records instead of buffering them

# RPC reply status
_SUCCESS, _PROG_UNAVAIL, _PROG_MISMATCH, _PROC_UNAVAIL, _GARBAGE_ARGS = 0, 1, 2, 3, 4


class XdrError(ValueError):
    """Arguments that do not decode (RPC GARBAGE_ARGS)."""


class Unpacker:
    """Just enough XDR decoding for the VXI-11 and port mapper arguments."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data, self.offset = data, offset

    def _take(self, size: int) -> bytes:
        if self.offset + size > len(self.data):
            raise XdrError("arguments too short")
        chunk = self.data[self.offset:self.offset + size]
        self.offset += size
        return chunk

    def uint(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def int(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def bool(self) -> bool:
        return self.uint() != 0

    def opaque(self) -> bytes:
        size = self.uint()
        data = self._take(size)
        self._take(-size % 4)
        return data


def pack(*values: int) -> bytes:
    """XDR-encode signed/unsigned 32-bit integers (negative values as int, others as uint)."""
    return b"".join(struct.pack(">i" if v < 0 else ">I", v) for v in values)


def pack_opaque(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data + b"\0" * (-len(data) % 4)


def _parse_call(record: bytes) -> tuple[int, int, int, int, Unpacker] | int:
    """(xid, prog, vers, proc, args) of an RPC call; an int xid if it is not a valid call."""
    u = Unpacker(record)
    xid = u.uint()
    if u.uint() != 0 or u.uint() != 2:  # CALL, RPC version 2
        return xid
    prog, vers, proc = u.uint(), u.uint(), u.uint()
    for _ in range(2):  # credentials and verifier
        u.uint()
        u.opaque()
    return xid, prog, vers, proc, u


def _reply(xid: int, status: int, body: bytes = b"") -> bytes:
    """An accepted RPC reply with a null verifier."""
    return pack(xid, 1, 0, 0, 0, status) + body


def _denied(xid: int) -> bytes:
    """MSG_DENIED, RPC_MISMATCH (supported version 2..2)."""
    return pack(xid, 1, 1, 0, 2, 2)


class _RpcServer(TcpServer):
    """ONC RPC over TCP with record marking; subclasses implement ``_call``."""

    programs: dict[int, int] = {}  # program -> version

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = self._connected(writer)
        try:
            while True:
                record = bytearray()
                while True:
                    header = struct.unpack(">I", await reader.readexactly(4))[0]
                    record += await reader.readexactly(header & 0x7FFFFFFF)
                    if len(record) > MAX_RECORD:
                        return
                    if header & 0x80000000:
                        break
                answer = await self._dispatch(bytes(record), connection)
                if answer is None:
                    continue
                writer.write(struct.pack(">I", 0x80000000 | len(answer)) + answer)
                await writer.drain()
        except (asyncio.IncompleteReadError, Disconnect):
            return
        finally:
            self._disconnected(connection)

    async def _dispatch(self, record: bytes, connection) -> bytes | None:
        try:
            call = _parse_call(record)
        except XdrError:
            return None  # not even an xid: nothing to answer
        if isinstance(call, int):
            return _denied(call)
        xid, prog, vers, proc, args = call
        if prog not in self.programs:
            return _reply(xid, _PROG_UNAVAIL)
        if vers != self.programs[prog]:
            return _reply(xid, _PROG_MISMATCH, pack(self.programs[prog], self.programs[prog]))
        if proc == 0:  # NULL procedure of every program
            return _reply(xid, _SUCCESS)
        try:
            body = await self._call(prog, proc, args, connection)
        except XdrError:
            return _reply(xid, _GARBAGE_ARGS)
        if body is None:
            return _reply(xid, _PROC_UNAVAIL)
        return _reply(xid, _SUCCESS, body)

    def _connected(self, writer: asyncio.StreamWriter):
        return None

    def _disconnected(self, connection) -> None:
        pass

    async def _call(self, prog: int, proc: int, args: Unpacker, connection) -> bytes | None:
        raise NotImplementedError


class PortMapper(_RpcServer):
    """The ONC RPC port mapper (version 2) on TCP and UDP; answers GETPORT and DUMP."""

    programs = {PMAP_PROG: PMAP_VERS}

    def __init__(self, host: str = "127.0.0.1", port: int = 111):
        super().__init__(host, port, name="scpi-sim-portmapper")
        self.mappings: dict[tuple[int, int, int], int] = {}  # (prog, vers, protocol) -> port
        self._udp: list[asyncio.DatagramTransport] = []

    def register(self, prog: int, vers: int, protocol: int, port: int) -> None:
        self.mappings[(prog, vers, protocol)] = port

    async def _call(self, prog: int, proc: int, args: Unpacker, connection) -> bytes | None:
        if proc == PMAP_GETPORT:
            key = (args.uint(), args.uint(), args.uint())
            args.uint()  # port: ignored in a query
            return pack(self.mappings.get(key, 0))
        if proc == PMAP_DUMP:
            body = b"".join(pack(1, *key, port) for key, port in self.mappings.items())
            return body + pack(0)
        return None

    async def _open(self) -> None:
        await super()._open()
        loop = asyncio.get_running_loop()
        try:
            for server in self._servers:
                address = server.sockets[0].getsockname()[0]
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _UdpPortMapper(self), local_addr=(address, self.port))
                self._udp.append(transport)
        except BaseException:
            await self._close()
            raise

    async def _close(self) -> None:
        for transport in self._udp:
            transport.close()
        self._udp.clear()
        await super()._close()


class _UdpPortMapper(asyncio.DatagramProtocol):
    def __init__(self, mapper: PortMapper):
        self.mapper = mapper
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address) -> None:
        asyncio.ensure_future(self._answer(data, address))

    async def _answer(self, data: bytes, address) -> None:
        answer = await self.mapper._dispatch(data, None)
        if answer is not None and self.transport is not None and not self.transport.is_closing():
            self.transport.sendto(answer, address)


class _Link:
    def __init__(self, link_id: int, instrument: Instrument, connection: set):
        self.id = link_id
        self.instrument = instrument
        self.session = Session(instrument)
        self.connection = connection


class Vxi11Server(_RpcServer):
    """VXI-11 core and abort channel for one or more instruments on one TCP port."""

    programs = {DEVICE_CORE_PROG: DEVICE_VERS, DEVICE_ASYNC_PROG: DEVICE_VERS}

    def __init__(self, instruments: dict[str, Instrument] | Instrument, host: str = "127.0.0.1", port: int = 0):
        super().__init__(host, port, name="scpi-sim-vxi11")
        if isinstance(instruments, Instrument):
            instruments = {"inst0": instruments}
        self.instruments = dict(instruments)
        self._links: dict[int, _Link] = {}
        self._ids = itertools.count(1)

    def _connected(self, writer: asyncio.StreamWriter) -> set:
        return set()  # link ids created on this connection

    def _disconnected(self, connection) -> None:
        for link_id in connection or ():
            self._destroy(link_id)

    def _destroy(self, link_id: int) -> bool:
        link = self._links.pop(link_id, None)
        if link is None:
            return False
        link.connection.discard(link_id)
        LOCKS.release(link.instrument, link)
        return True

    async def _call(self, prog: int, proc: int, args: Unpacker, connection) -> bytes | None:
        if prog == DEVICE_ASYNC_PROG:
            if proc != DEVICE_ABORT:
                return None
            args.int()
            return pack(NO_ERROR)  # every operation completes at once: nothing to abort
        if proc == CREATE_LINK:
            return await self._create_link(args, connection)
        if proc == CREATE_INTR_CHAN:
            return pack(OPERATION_NOT_SUPPORTED)
        if proc == DESTROY_INTR_CHAN:
            return pack(CHANNEL_NOT_ESTABLISHED)
        handler = {
            DEVICE_WRITE: self._write, DEVICE_READ: self._read, DEVICE_READSTB: self._readstb,
            DEVICE_TRIGGER: self._generic, DEVICE_CLEAR: self._clear, DEVICE_REMOTE: self._generic,
            DEVICE_LOCAL: self._generic, DEVICE_LOCK: self._lock, DEVICE_UNLOCK: self._unlock,
            DEVICE_ENABLE_SRQ: self._enable_srq, DEVICE_DOCMD: self._docmd, DESTROY_LINK: self._destroy_link,
        }.get(proc)
        if handler is None:
            return None
        link = self._links.get(args.int())
        if link is None:
            failure = {DEVICE_READ: pack(INVALID_LINK, 0) + pack_opaque(b""), DEVICE_WRITE: pack(INVALID_LINK, 0),
                       DEVICE_READSTB: pack(INVALID_LINK, 0), DEVICE_DOCMD: pack(INVALID_LINK) + pack_opaque(b"")}
            return failure.get(proc, pack(INVALID_LINK))
        return await handler(link, args)

    async def _create_link(self, args: Unpacker, connection: set) -> bytes:
        args.int()  # client id
        lock_device, lock_timeout = args.bool(), args.uint()
        device = args.opaque().decode("ascii", errors="replace")
        instrument = find_instrument(self.instruments, device, "inst")
        if instrument is None:
            return pack(DEVICE_NOT_ACCESSIBLE, 0, 0, 0)
        link = _Link(next(self._ids), instrument, connection)
        if lock_device and not await LOCKS.acquire(instrument, link, lock_timeout / 1000):
            return pack(LOCKED_BY_ANOTHER_LINK, 0, 0, 0)
        self._links[link.id] = link
        connection.add(link.id)
        return pack(NO_ERROR, link.id, self.port, MAX_RECV_SIZE)

    async def _may_use(self, link: _Link, flags: int, lock_timeout: int) -> bool:
        """False if another link holds the lock (after waiting, with the wait-lock flag)."""
        if not LOCKS.blocked(link.instrument, link):
            return True
        if flags & OP_FLAG_WAITLOCK:
            return await LOCKS.wait_free(link.instrument, link, lock_timeout / 1000)
        return False

    async def _write(self, link: _Link, args: Unpacker) -> bytes:
        args.uint()  # io timeout: writing never blocks
        lock_timeout, flags, data = args.uint(), args.int(), args.opaque()
        if not await self._may_use(link, flags, lock_timeout):
            return pack(LOCKED_BY_ANOTHER_LINK, 0)
        link.session.write(data, end=bool(flags & OP_FLAG_END))  # Disconnect closes the connection
        return pack(NO_ERROR, len(data))

    async def _read(self, link: _Link, args: Unpacker) -> bytes:
        size, io_timeout, lock_timeout, flags, term_char = (
            args.uint(), args.uint(), args.uint(), args.int(), args.int())
        if not await self._may_use(link, flags, lock_timeout):
            return pack(LOCKED_BY_ANOTHER_LINK, 0) + pack_opaque(b"")
        if not await link.session.wait_output(io_timeout / 1000):
            return pack(IO_TIMEOUT, 0) + pack_opaque(b"")
        size = max(1, min(size, MAX_RECV_SIZE))
        data = link.session.read(size)
        reason = 0
        if flags & OP_FLAG_TERMCHAR_SET:
            end = data.find(bytes([term_char & 0xFF]))
            if end >= 0:
                link.session.unread(data[end + 1:])  # what follows the term char stays for the next read
                data = data[:end + 1]
                reason |= RX_CHR
        if not link.session.has_output:
            reason |= RX_END
        elif len(data) == size:
            reason |= RX_REQCNT
        return pack(NO_ERROR, reason) + pack_opaque(data)

    async def _readstb(self, link: _Link, args: Unpacker) -> bytes:
        flags, lock_timeout = args.int(), args.uint()
        if not await self._may_use(link, flags, lock_timeout):
            return pack(LOCKED_BY_ANOTHER_LINK, 0)
        return pack(NO_ERROR, link.session.status_byte())

    async def _generic(self, link: _Link, args: Unpacker) -> bytes:
        flags, lock_timeout = args.int(), args.uint()
        if not await self._may_use(link, flags, lock_timeout):
            return pack(LOCKED_BY_ANOTHER_LINK)
        return pack(NO_ERROR)

    async def _clear(self, link: _Link, args: Unpacker) -> bytes:
        flags, lock_timeout = args.int(), args.uint()
        if not await self._may_use(link, flags, lock_timeout):
            return pack(LOCKED_BY_ANOTHER_LINK)
        link.session.clear()
        return pack(NO_ERROR)

    async def _lock(self, link: _Link, args: Unpacker) -> bytes:
        flags, lock_timeout = args.int(), args.uint()
        timeout = lock_timeout / 1000 if flags & OP_FLAG_WAITLOCK else 0
        if not await LOCKS.acquire(link.instrument, link, timeout):
            return pack(LOCKED_BY_ANOTHER_LINK)
        return pack(NO_ERROR)

    async def _unlock(self, link: _Link, args: Unpacker) -> bytes:
        return pack(NO_ERROR if LOCKS.release(link.instrument, link) else NO_LOCK_HELD)

    async def _enable_srq(self, link: _Link, args: Unpacker) -> bytes:
        args.bool()
        args.opaque()
        return pack(NO_ERROR)  # accepted; scpi-sim never raises a service request

    async def _docmd(self, link: _Link, args: Unpacker) -> bytes:
        return pack(OPERATION_NOT_SUPPORTED) + pack_opaque(b"")

    async def _destroy_link(self, link: _Link, args: Unpacker) -> bytes:
        self._destroy(link.id)
        return pack(NO_ERROR)


def portmapper_mappings(mapper: PortMapper, vxi11_port: int) -> None:
    """Register the VXI-11 core and abort channel (TCP) with a port mapper."""
    mapper.register(DEVICE_CORE_PROG, DEVICE_VERS, IPPROTO_TCP, vxi11_port)
    mapper.register(DEVICE_ASYNC_PROG, DEVICE_VERS, IPPROTO_TCP, vxi11_port)

