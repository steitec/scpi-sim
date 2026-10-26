import pytest

from scpi_sim.instruments import Multiplexer
from scpi_sim import __version__


def test_close_query_state():
    m = Multiplexer()
    m.process("ROUT:CLOS (@101,103:104)")
    assert m.process("ROUT:CLOS? (@101:104)") == "1,0,1,1"
    assert m.process("ROUT:OPEN? (@102)") == "1"
    assert m.process("ROUT:CLOS:STAT?") == "(@101,103,104)"


def test_open_some():
    m = Multiplexer()
    m.process("ROUT:CLOS (@101:103);:ROUT:OPEN (@102)")
    assert m.process("ROUT:CLOS:STAT?") == "(@101,103)"


def test_invalid_channel_is_atomic():
    m = Multiplexer(channels=16)
    m.process("ROUT:CLOS (@101,117)")
    assert m.process("SYST:ERR?").startswith("-222")
    assert m.process("ROUT:CLOS:STAT?") == "(@)"


def test_open_all_and_rst():
    m = Multiplexer()
    m.process("ROUT:CLOS (@101:105);:ROUT:OPEN:ALL")
    assert m.process("ROUT:CLOS:STAT?") == "(@)"
    m.process("ROUT:CLOS (@101);*RST")
    assert m.process("ROUT:CLOS:STAT?") == "(@)"


def test_channel_count_validated():
    with pytest.raises(ValueError):
        Multiplexer(channels=100)
    with pytest.raises(ValueError):
        Multiplexer(channels=0)


def test_idn():
    assert Multiplexer().process("*IDN?") == f"scpi-sim,MUX,SIM00003,{__version__}"


def test_huge_channel_range_rejected_quickly():
    import time
    m = Multiplexer()
    start = time.monotonic()
    m.process("ROUT:CLOS (@101:3000000)")
    assert time.monotonic() - start < 0.05
    assert m.process("SYST:ERR?").startswith("-222")
