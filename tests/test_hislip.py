import socket
import struct
import time

import pyvisa
import pytest

from scpi_sim import Multimeter, PowerSupply
from scpi_sim import __version__
from scpi_sim.hislip import HEADER, HislipServer, message


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
    with HislipServer(dmm, port=0) as s:
        yield s


def open_instr(rm, port, device="hislip0"):
    r = rm.open_resource(f"TCPIP0::127.0.0.1::{device},{port}::INSTR")
    r.timeout = 1000
    r.read_termination = "\n"
    return r


class Channel:
    """One raw HiSLIP connection."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)

    def send(self, kind, control=0, parameter=0, payload=b""):
        self.sock.sendall(message(kind, control, parameter, payload))

    def receive(self):
        prologue, kind, control, parameter, length = HEADER.unpack(self._exact(HEADER.size))
        assert prologue == b"HS"
        return kind, control, parameter, self._exact(length)

    def _exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def close(self):
        self.sock.close()


@pytest.fixture
def connect(server):
    channels = []

    def make(sub_address=b"hislip0", port=None):
        sync = Channel(port or server.port)
        sync.send(0, 0, (0x0100 << 16) | 0x5858, sub_address)
        kind, control, parameter, _ = sync.receive()
        assert kind == 1 and parameter >> 16 == 0x0100
        asyn = Channel(port or server.port)
        asyn.send(17, 0, parameter & 0xFFFF)
        assert asyn.receive()[0] == 18
        channels.extend((sync, asyn))
        return sync, asyn
    yield make
    for c in channels:
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


def test_status_byte(rm, server):
    r = open_instr(rm, server.port)
    assert r.read_stb() == 0
    r.write("BOGUS")
    assert r.read_stb() == 4


def test_device_clear_cancels_delayed_answer(rm, server, dmm):
    r = open_instr(rm, server.port)
    dmm.inject("delay", seconds=0.5, count=1)
    r.write("*IDN?")
    r.clear()
    time.sleep(0.6)
    assert r.query("*OPC?") == "1"  # the delayed *IDN? answer never arrives


def test_delay_fault(rm, server, dmm):
    r = open_instr(rm, server.port)
    dmm.inject("delay", seconds=0.3, count=1)
    start = time.monotonic()
    assert r.query("*OPC?") == "1"
    assert time.monotonic() - start >= 0.3


def test_disconnect_fault(rm, server, dmm):
    r = open_instr(rm, server.port)
    dmm.inject("disconnect")
    with pytest.raises(Exception):
        r.query("*IDN?")
    r2 = open_instr(rm, server.port)
    assert r2.query("*OPC?") == "1"


def test_named_instruments(rm):
    with HislipServer({"psu": PowerSupply(), "dmm": Multimeter(noise=0)}, port=0) as s:
        for device, model in (("hislip0", "PSU"), ("hislip1", "DMM"), ("hislip_psu", "PSU"), ("hislip_dmm", "DMM")):
            r = open_instr(rm, s.port, device)
            assert r.query("*IDN?").split(",")[1] == model
            r.close()


def test_unknown_sub_address_is_fatal(server):
    c = Channel(server.port)
    c.send(0, 0, 0x01005858, b"hislip7")
    kind, control, _, text = c.receive()
    assert (kind, control) == (2, 3) and b"hislip7" in text
    c.close()


def test_bad_prologue_is_fatal(server):
    c = Channel(server.port)
    c.sock.sendall(b"XX" + bytes(14))
    assert c.receive()[:2] == (2, 1)
    c.close()


def test_async_initialize_unknown_session(server):
    c = Channel(server.port)
    c.send(17, 0, 4711)
    assert c.receive()[:2] == (2, 3)
    c.close()


def test_data_answer_carries_message_id(connect):
    sync, _ = connect()
    sync.send(6, 0, 0xFFFFFF00, b"*ID")
    sync.send(7, 0, 0xFFFFFF02, b"N?\n")
    kind, _, parameter, payload = sync.receive()
    assert (kind, parameter) == (7, 0xFFFFFF02) and payload.startswith(b"scpi-sim,DMM")


def test_answer_split_by_client_max_size(connect):
    sync, asyn = connect()
    asyn.send(15, 0, 0, struct.pack(">Q", HEADER.size + 10))
    kind, _, _, payload = asyn.receive()
    assert kind == 16 and struct.unpack(">Q", payload)[0] >= 4096
    sync.send(7, 0, 1, b"*IDN?")
    parts = []
    while True:
        kind, _, _, payload = sync.receive()
        assert len(payload) <= 10
        parts.append(payload)
        if kind == 7:
            break
        assert kind == 6
    assert b"".join(parts) == f"scpi-sim,DMM,SIM00001,{__version__}\n".encode()


def test_device_clear_handshake(connect):
    sync, asyn = connect()
    asyn.send(19)
    assert asyn.receive()[0] == 23
    sync.send(8, 0)  # DeviceClearComplete, feature bitmap 0
    assert sync.receive()[:2] == (9, 0)


def test_locks(connect):
    _, a = connect()
    _, b = connect()
    a.send(24)
    assert a.receive()[:3] == (25, 0, 0)
    a.send(4, 1, 0)  # request, no wait
    assert a.receive()[:2] == (5, 1)
    b.send(4, 1, 100)
    start = time.monotonic()
    assert b.receive()[:2] == (5, 0)
    assert time.monotonic() - start >= 0.1
    b.send(24)
    assert b.receive()[:3] == (25, 1, 1)
    b.send(4, 0, 0)  # release without holding
    assert b.receive()[:2] == (5, 3)
    a.send(4, 0, 0)
    assert a.receive()[:2] == (5, 1)
    b.send(4, 1, 0)
    assert b.receive()[:2] == (5, 1)


def test_lock_released_when_session_ends(connect):
    sync, a = connect()
    _, b = connect()
    a.send(4, 1, 0)
    assert a.receive()[:2] == (5, 1)
    sync.close()
    b.send(4, 1, 2000)
    assert b.receive()[:2] == (5, 1)


def test_unknown_messages_get_error(connect):
    sync, asyn = connect()
    sync.send(200)
    assert sync.receive()[:2] == (3, 1)
    asyn.send(201)
    assert asyn.receive()[:2] == (3, 1)
    asyn.send(10, 1)  # AsyncRemoteLocalControl
    assert asyn.receive()[0] == 11
