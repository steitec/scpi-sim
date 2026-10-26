import pytest

from helpers import connect, query
from scpi_sim import Multimeter, PowerSupply
from scpi_sim.transport import InstrumentServer


@pytest.fixture
def server():
    with InstrumentServer(Multimeter(noise=0), port=0) as s:
        yield s


def test_idn_over_tcp(server):
    _, f = connect(server.port)
    assert query(f, "*IDN?") == "scpi-sim,DMM,SIM00001,0.1.0"


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
    s, f = connect(server.port)
    f.write(b"MEAS:VOLT?\n")
    f.flush()
    s.close()
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


def test_oversized_line_and_garbage(server):
    _, f = connect(server.port)
    f.write(b"A" * 70_000 + b"\n" + b"\xff\xfe\n")
    f.flush()
    assert query(f, "*OPC?") == "1"
    errors = {query(f, "SYST:ERR?").split(",")[0] for _ in range(2)}
    assert "-223" in errors


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
