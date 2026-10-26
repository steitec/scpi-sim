import socket
import struct
import threading

import pytest

from helpers import connect, query
from scpi_sim import Multimeter, PowerSupply
from scpi_sim.transport import InstrumentServer
from scpi_sim import __version__


@pytest.fixture
def server():
    with InstrumentServer(Multimeter(noise=0), port=0) as s:
        yield s


def test_idn_over_tcp(server):
    _, f = connect(server.port)
    assert query(f, "*IDN?") == f"scpi-sim,DMM,SIM00001,{__version__}"


def test_command_without_query_sends_nothing(server):
    _, f = connect(server.port)
    f.write(b"CONF:RES\n")
    assert query(f, "CONF?") == '"RES AUTO"'


def test_crlf_and_blank_lines(server):
    _, f = connect(server.port)
    f.write(b"\r\n   \r\n*OPC?\r\n")
    f.flush()
    assert f.readline() == b"1\n"
    assert query(f, "SYST:ERR?") == '0,"No error"'


def test_abrupt_disconnect_does_not_kill_server(server):
    _, live = connect(server.port)
    assert query(live, "*OPC?") == "1"
    s, f = connect(server.port)
    f.write(b"MEAS:VOLT?\n")
    f.flush()
    # close with RST instead of FIN, like a crashed client or a pulled cable
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    f.close()
    s.close()
    assert query(live, "*IDN?").startswith("scpi-sim,DMM")
    _, f2 = connect(server.port)
    assert query(f2, "*OPC?") == "1"


def test_two_clients_share_state():
    with InstrumentServer(PowerSupply(), port=0) as srv:
        _, a = connect(srv.port)
        _, b = connect(srv.port)
        query(a, "VOLT 7;*OPC?")
        assert query(b, "VOLT?") == "+7.00000000E+00"
        for _ in range(50):
            assert query(a, "*IDN?").startswith("scpi-sim,PSU")
            assert query(b, "*OPC?") == "1"


def test_concurrent_clients_get_their_own_answers():
    with InstrumentServer(PowerSupply(channels=8), port=0) as srv:
        errors = []

        def client(n):
            _, f = connect(srv.port)
            for i in range(100):
                answer = query(f, f"*IDN?;:INST:NSEL?;:SOUR{n}:VOLT {i % 30};:SOUR{n}:VOLT?")
                expected = f"scpi-sim,PSU,SIM00002,{__version__};1;{float(i % 30):+.8E}"
                if answer != expected:
                    errors.append((n, answer))

        threads = [threading.Thread(target=client, args=(n,)) for n in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


def test_oversized_line_and_garbage(server):
    _, f = connect(server.port)
    f.write(b"A" * 70_000 + b"\n" + b"\xff\xfe\n")
    f.flush()
    assert query(f, "*OPC?") == "1"
    errors = [query(f, "SYST:ERR?").split(",")[0] for _ in range(3)]
    assert errors == ["-223", "-102", "0"]


def test_port_in_use():
    with InstrumentServer(Multimeter(), port=0) as first:
        with pytest.raises(OSError):
            InstrumentServer(Multimeter(), port=first.port).start()


def test_stop_closes_clients():
    srv = InstrumentServer(Multimeter(), port=0)
    srv.start()
    _, f = connect(srv.port)
    assert query(f, "*OPC?") == "1"
    srv.stop()
    assert f.readline() == b""


def test_localhost_port_zero_same_port_on_every_address():
    # "localhost" resolves to ::1 and 127.0.0.1; with port 0 each must still get the reported port
    with InstrumentServer(Multimeter(), host="localhost", port=0) as srv:
        for family, _, _, _, address in socket.getaddrinfo("localhost", srv.port, type=socket.SOCK_STREAM):
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(address)
                s.sendall(b"*OPC?\n")
                assert s.recv(16) == b"1\n", address
