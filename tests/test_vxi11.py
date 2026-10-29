import socket
import struct
import time

import pyvisa
import pytest

from scpi_sim import Multimeter, PowerSupply
from scpi_sim import __version__
from scpi_sim.vxi11 import (DEVICE_CORE_PROG, IPPROTO_TCP, PMAP_PROG, PortMapper, Unpacker, Vxi11Server, pack,
                            pack_opaque, portmapper_mappings)


@pytest.fixture
def rm():
    manager = pyvisa.ResourceManager("@py")
    yield manager
    manager.close()


@pytest.fixture
def dmm():
    return Multimeter(noise=0)


@pytest.fixture
def server(dmm):
    with Vxi11Server(dmm) as s:
        yield s


def open_instr(rm, port, device="inst0"):
    r = rm.open_resource(f"TCPIP0::127.0.0.1,{port}::{device}::INSTR")
    r.timeout = 1000
    r.read_termination = "\n"
    return r


class RpcClient:
    """A bare ONC RPC client over TCP, to test what PyVISA does not exercise."""

    def __init__(self, port, prog, vers=1):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.prog, self.vers, self.xid = prog, vers, 0

    def call(self, proc, body=b"", prog=None, vers=None):
        self.xid += 1
        record = pack(self.xid, 0, 2, prog or self.prog, vers or self.vers, proc, 0, 0, 0, 0) + body
        self.sock.sendall(struct.pack(">I", 0x80000000 | len(record)) + record)
        return self.receive()

    def receive(self):
        header = struct.unpack(">I", self._exact(4))[0]
        u = Unpacker(self._exact(header & 0x7FFFFFFF))
        assert u.uint() == self.xid
        assert u.uint() == 1  # REPLY
        return u

    def _exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def accepted(self, u):
        assert u.uint() == 0  # MSG_ACCEPTED
        u.uint(), u.opaque()  # verifier
        return u.uint()  # accept status

    def close(self):
        self.sock.close()


def create_link(client, device="inst0", lock=False, lock_timeout=0):
    u = client.call(10, pack(1, int(lock), lock_timeout) + pack_opaque(device.encode()))
    assert client.accepted(u) == 0
    return u.int(), u.int()  # error, link


def write(client, link, data, flags=8, lock_timeout=0):
    u = client.call(11, pack(link, 1000, lock_timeout, flags) + pack_opaque(data))
    assert client.accepted(u) == 0
    return u.int()


def read(client, link, size=1000, flags=0, term=0, io_timeout=500):
    u = client.call(12, pack(link, size, io_timeout, 0, flags, term))
    assert client.accepted(u) == 0
    return u.int(), u.int(), u.opaque()


@pytest.fixture
def rpc(server):
    clients = []

    def make():
        clients.append(RpcClient(server.port, DEVICE_CORE_PROG))
        return clients[-1]
    yield make
    for c in clients:
        c.close()


def test_pyvisa_query(rm, server):
    r = open_instr(rm, server.port)
    assert r.query("*IDN?") == f"scpi-sim,DMM,SIM00001,{__version__}"
    assert float(r.query("MEAS:VOLT?")) == 5.0


def test_read_without_answer_times_out(rm, server):
    r = open_instr(rm, server.port)
    r.write("CONF:RES")
    with pytest.raises(pyvisa.VisaIOError) as error:
        r.read()
    assert error.value.error_code == pyvisa.constants.StatusCode.error_timeout
    assert r.query("CONF?") == '"RES AUTO"'


def test_status_byte_has_mav_while_answer_waits(rm, server):
    r = open_instr(rm, server.port)
    assert r.read_stb() == 0
    r.write("*IDN?")
    assert r.read_stb() == 16
    r.read()
    r.write("BOGUS")
    assert r.read_stb() == 4  # error queue not empty


def test_new_query_interrupts_unread_answer(rm, server):
    r = open_instr(rm, server.port)
    r.write("*IDN?")
    assert r.query("*OPC?") == "1"
    assert r.query("SYST:ERR?") == '-410,"Query INTERRUPTED"'


def test_device_clear_drops_answer_keeps_errors(rm, server):
    r = open_instr(rm, server.port)
    r.write("BOGUS")
    r.write("*IDN?")
    r.clear()
    assert r.read_stb() == 4
    assert r.query("SYST:ERR?") == '-113,"Undefined header"'


def test_delay_fault(rm, server, dmm):
    r = open_instr(rm, server.port)
    dmm.inject("delay", seconds=0.3, count=1)
    start = time.monotonic()
    assert r.query("*OPC?") == "1"
    assert time.monotonic() - start >= 0.3


def test_delay_longer_than_timeout(rm, server, dmm):
    r = open_instr(rm, server.port)
    r.timeout = 200
    dmm.inject("delay", seconds=1, count=1)
    with pytest.raises(pyvisa.VisaIOError):
        r.query("*OPC?")


def test_no_response_fault(rm, server, dmm):
    r = open_instr(rm, server.port)
    dmm.inject("no_response", count=1)
    with pytest.raises(pyvisa.VisaIOError):
        r.query("*IDN?")
    assert r.query("*OPC?") == "1"


def test_disconnect_fault_closes_connection(rpc, dmm):
    c = rpc()
    _, link = create_link(c)
    dmm.inject("disconnect")
    with pytest.raises((ConnectionError, OSError)):
        write(c, link, b"*IDN?\n")
    c2 = rpc()
    _, link2 = create_link(c2)
    assert write(c2, link2, b"*OPC?\n") == 0
    assert read(c2, link2)[2] == b"1\n"


def test_named_instruments():
    psu, dmm = PowerSupply(), Multimeter(noise=0)
    with Vxi11Server({"psu": psu, "dmm": dmm}) as s:
        for device, model in (("inst0", "PSU"), ("inst1", "DMM"), ("psu", "PSU"), ("DMM", "DMM")):
            c = RpcClient(s.port, DEVICE_CORE_PROG)
            error, link = create_link(c, device)
            assert error == 0
            write(c, link, b"*IDN?\n")
            assert read(c, link)[2].split(b",")[1] == model.encode()
            c.close()
        c = RpcClient(s.port, DEVICE_CORE_PROG)
        assert create_link(c, "inst2")[0] == 3
        assert create_link(c, "mux")[0] == 3
        c.close()


def test_read_in_pieces_and_termchar(rpc):
    c = rpc()
    _, link = create_link(c)
    write(c, link, b"*IDN?\n")
    error, reason, data = read(c, link, size=8)
    assert (error, reason, data) == (0, 1, b"scpi-sim")  # RX_REQCNT
    error, reason, data = read(c, link, flags=128, term=ord(","))
    assert (reason, data) == (2, b",")  # RX_CHR
    error, reason, data = read(c, link)
    assert reason == 4 and data.endswith(b"\n")  # RX_END


def test_write_in_pieces(rpc):
    c = rpc()
    _, link = create_link(c)
    write(c, link, b"*ID", flags=0)
    write(c, link, b"N?", flags=8)
    assert read(c, link)[2].startswith(b"scpi-sim,DMM")


def test_invalid_link(rpc):
    c = rpc()
    assert write(c, link=999, data=b"*IDN?\n") == 4
    assert read(c, 999)[0] == 4


def test_locks_between_links(rpc):
    a, b = rpc(), rpc()
    _, la = create_link(a)
    _, lb = create_link(b)
    assert a.accepted(u := a.call(18, pack(la, 0, 0))) == 0 and u.int() == 0  # device_lock
    assert write(b, lb, b"*OPC?\n") == 11
    assert write(b, lb, b"*OPC?\n", flags=8 | 1, lock_timeout=100) == 11
    assert b.accepted(u := b.call(18, pack(lb, 0, 0))) == 0 and u.int() == 11
    assert b.accepted(u := b.call(19, pack(lb))) == 0 and u.int() == 12  # unlock without holding
    assert write(a, la, b"*OPC?\n") == 0
    assert a.accepted(u := a.call(19, pack(la))) == 0 and u.int() == 0
    assert write(b, lb, b"*OPC?\n") == 0


def test_lock_released_when_connection_closes(rpc):
    a, b = rpc(), rpc()
    _, la = create_link(a, lock=True)
    _, lb = create_link(b)
    assert write(b, lb, b"*OPC?\n") == 11
    a.close()
    assert write(b, lb, b"*OPC?\n", flags=8 | 1, lock_timeout=2000) == 0


def test_unsupported_procedures(rpc):
    c = rpc()
    _, link = create_link(c)
    assert c.accepted(u := c.call(22, pack(link, 0, 0, 0, 0, 0, 0) + pack_opaque(b""))) == 0 and u.int() == 8
    assert c.accepted(u := c.call(25, pack(0, 0, 0, 0, 0))) == 0 and u.int() == 8
    assert c.accepted(c.call(99)) == 3  # PROC_UNAVAIL
    assert c.accepted(c.call(0, prog=123456)) == 1  # PROG_UNAVAIL
    assert c.accepted(c.call(0, vers=2)) == 2  # PROG_MISMATCH
    assert c.accepted(c.call(14, pack(link, 0, 0, 0))) == 0  # trigger
    assert c.accepted(u := c.call(23, pack(link))) == 0 and u.int() == 0  # destroy_link
    assert write(c, link, b"*OPC?\n") == 4


def test_abort_channel_on_same_port(server):
    c = RpcClient(server.port, 0x0607B0)
    assert c.accepted(u := c.call(1, pack(1))) == 0 and u.int() == 0
    c.close()


def test_garbage_args(rpc):
    c = rpc()
    assert c.accepted(c.call(11, b"\0\0")) == 4


@pytest.fixture
def portmapper(server):
    with PortMapper(port=0) as mapper:
        portmapper_mappings(mapper, server.port)
        yield mapper


def test_portmapper_getport_tcp(portmapper, server):
    c = RpcClient(portmapper.port, PMAP_PROG, 2)
    assert c.accepted(u := c.call(3, pack(DEVICE_CORE_PROG, 1, IPPROTO_TCP, 0))) == 0
    assert u.uint() == server.port
    assert c.accepted(u := c.call(3, pack(123, 1, IPPROTO_TCP, 0))) == 0 and u.uint() == 0
    assert c.accepted(u := c.call(4)) == 0
    entries = []
    while u.bool():
        entries.append((u.uint(), u.uint(), u.uint(), u.uint()))
    assert (DEVICE_CORE_PROG, 1, IPPROTO_TCP, server.port) in entries
    c.close()


def test_portmapper_getport_udp(portmapper, server):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(3)
        s.sendto(pack(7, 0, 2, PMAP_PROG, 2, 3, 0, 0, 0, 0, DEVICE_CORE_PROG, 1, IPPROTO_TCP, 0),
                 ("127.0.0.1", portmapper.port))
        u = Unpacker(s.recv(100))
    assert [u.uint() for _ in range(7)] == [7, 1, 0, 0, 0, 0, server.port]


def test_pyvisa_finds_core_port_through_portmapper(monkeypatch, rm, server, portmapper):
    from pyvisa_py.protocols import rpc
    monkeypatch.setattr(rpc, "PMAP_PORT", portmapper.port)  # 111 needs privileges
    r = rm.open_resource("TCPIP0::127.0.0.1::inst0::INSTR")
    r.read_termination = "\n"
    assert r.query("*IDN?").startswith("scpi-sim,DMM")
    r.close()
