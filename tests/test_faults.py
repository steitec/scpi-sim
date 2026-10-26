import time

import pytest

from helpers import connect, query
from scpi_sim import Multimeter, PowerSupply
from scpi_sim.transport import InstrumentServer


def wait_until_used(instrument, kind, timeout=5.0):
    """Wait until the server thread has used the fault up (instead of a fixed sleep)."""
    end = time.monotonic() + timeout
    while kind in instrument.faults:
        assert time.monotonic() < end, f"{kind} fault was not used"
        time.sleep(0.005)


def test_no_response_executes_but_stays_silent():
    p = PowerSupply()
    p.inject("no_response", count=1)
    assert p.process("VOLT 5;VOLT?") is None
    assert p.process("VOLT?") == "+5.00000000E+00"


def test_no_response_ignores_messages_without_answer():
    p = PowerSupply()
    p.inject("no_response", count=1)
    p.process("VOLT 5")  # nothing to suppress, does not use up the fault
    assert p.process("VOLT?") is None
    assert p.faults == {}


def test_idn_and_clear():
    d = Multimeter()
    d.inject("idn", value="ACME,X,1,1")
    assert d.process("*IDN?") == "ACME,X,1,1"
    d.process("*RST;*CLS")
    assert d.process("*IDN?") == "ACME,X,1,1"
    d.clear_faults()
    assert d.process("*IDN?").startswith("scpi-sim,DMM")


def test_error_pushed_once():
    d = Multimeter()
    d.inject("error", code=-300, message="Relay stuck")
    assert d.process("SYST:ERR?") == '-300,"Relay stuck"'
    assert d.process("SYST:ERR?") == '0,"No error"'


def test_error_with_standard_message():
    d = Multimeter()
    d.inject("error", code=-222)
    assert d.process("SYST:ERR?") == '-222,"Data out of range"'


def test_offset_and_noise_dmm_only():
    d = Multimeter(volts=5, noise=0)
    d.inject("offset", value=0.2)
    assert d.process("MEAS:VOLT?") == "+5.20000000E+00"
    d.clear_faults()
    d.inject("noise", sigma=0.01)
    assert d.process("MEAS:VOLT?") != "+5.00000000E+00"
    with pytest.raises(ValueError, match="offset"):
        PowerSupply().inject("offset", value=1)


def test_offset_does_not_shift_overload():
    d = Multimeter(volts=5, noise=0)
    d.inject("offset", value=0.2)
    assert d.process("MEAS:VOLT? 1") == "+9.90000000E+37"


def test_faults_listing():
    d = Multimeter()
    d.inject("delay", seconds=0.5)
    d.inject("no_response", count=2)
    assert d.faults == {"delay": {"seconds": 0.5, "count": None}, "no_response": {"count": 2}}


@pytest.mark.parametrize("kind,params", [
    ("bogus", {}), ("delay", {}), ("delay", {"seconds": 0}), ("noise", {"sigma": -1}),
    ("no_response", {"cnt": 1}), ("no_response", {"count": 0}), ("error", {}), ("idn", {}),
])
def test_bad_fault_rejected(kind, params):
    with pytest.raises(ValueError):
        Multimeter().inject(kind, **params)


def test_disconnect_over_tcp():
    with InstrumentServer(Multimeter(), port=0) as srv:
        _, f = connect(srv.port)
        srv.instrument.inject("disconnect")
        f.write(b"*IDN?\n")
        f.flush()
        assert f.readline() == b""  # closed by the instrument
        _, f2 = connect(srv.port)
        assert query(f2, "*OPC?") == "1"  # one-shot


def test_delay_over_tcp():
    with InstrumentServer(Multimeter(), port=0) as srv:
        _, f = connect(srv.port)
        srv.instrument.inject("delay", seconds=0.3, count=1)
        t = time.monotonic()
        assert query(f, "*OPC?") == "1"
        assert time.monotonic() - t >= 0.25  # Windows clocks tick in ~15 ms steps
        t = time.monotonic()
        assert query(f, "*OPC?") == "1"
        assert time.monotonic() - t < 0.2


def test_delay_does_not_block_other_clients():
    with InstrumentServer(Multimeter(), port=0) as srv:
        _, slow = connect(srv.port)
        _, fast = connect(srv.port)
        srv.instrument.inject("delay", seconds=1.0, count=1)
        slow.write(b"*IDN?\n")
        slow.flush()
        wait_until_used(srv.instrument, "delay")
        t = time.monotonic()
        assert query(fast, "*OPC?") == "1"
        assert time.monotonic() - t < 0.5
        assert slow.readline().startswith(b"scpi-sim,DMM")


def test_fault_hits_next_query_from_any_client():
    with InstrumentServer(Multimeter(), port=0) as srv:
        _, a = connect(srv.port)
        _, b = connect(srv.port)
        srv.instrument.inject("no_response", count=1)
        b.write(b"*IDN?\n")
        b.flush()
        wait_until_used(srv.instrument, "no_response")
        assert query(a, "*OPC?") == "1"  # b's query used the fault up


@pytest.mark.parametrize("kind,params", [
    ("delay", {"seconds": float("nan")}), ("delay", {"seconds": float("inf")}),
    ("offset", {"value": float("nan")}), ("noise", {"sigma": float("inf")}),
    ("no_response", {"count": float("inf")}), ("no_response", {"count": "abc"}),
])
def test_non_finite_values_rejected(kind, params):
    with pytest.raises(ValueError):
        Multimeter().inject(kind, **params)


def test_delay_on_one_bench_instrument_does_not_stall_the_others():
    import yaml
    from helpers import BASIC
    from scpi_sim.bench import Bench
    with Bench.from_dict(yaml.safe_load(BASIC)) as b:
        _, dmm = connect(b.port("dmm"))
        _, psu = connect(b.port("psu"))
        b.dmm.inject("delay", seconds=1.0, count=1)
        dmm.write(b"*IDN?\n")
        dmm.flush()
        wait_until_used(b.dmm, "delay")
        t = time.monotonic()
        assert query(psu, "*OPC?") == "1"
        assert time.monotonic() - t < 0.5
        assert dmm.readline().startswith(b"scpi-sim,DMM")


def test_string_numbers_are_converted():
    d = Multimeter()
    d.inject("no_response", count="2")
    d.inject("offset", value="0.5")
    assert d.faults == {"no_response": {"count": 2}, "offset": {"value": 0.5}}
