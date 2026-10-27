"""keithley-daq6510, after the Keithley DAQ6510 Reference Manual DAQ6510-901-01 Rev. C, section 12 (SCPI)."""

import pytest

from scpi_sim.bench import Bench, BenchError
from scpi_sim.profiles import get_profile

DAQ = get_profile("keithley-daq6510")


def errors(inst):
    found = []
    while not (error := inst.process("SYST:ERR?")).startswith("0,"):
        found.append(error.split(",")[0])
    return found


def bench(**daq):
    return Bench.from_dict({
        "instruments": {
            "psu": {"type": "psu", "port": 0, "channels": 2},
            "daq": {"profile": "keithley-daq6510", "port": 0, "noise": 0, **daq},
        },
        "loads": {"a": {"type": "resistor", "ohms": 1000}, "b": {"type": "resistor", "ohms": 2200}},
        "wiring": ["psu.ch1 -> a", "psu.ch2 -> b", "daq.101 -> a", "daq.102 -> b"],
    })


def powered(**daq):
    b = bench(**daq)
    b.psu.process("INST:NSEL 1;:VOLT 5;:CURR 1;:OUTP ON")
    b.psu.process("INST:NSEL 2;:VOLT 3.3;:CURR 1;:OUTP ON")
    return b


def test_identity():
    assert DAQ().process("*IDN?") == "KEITHLEY INSTRUMENTS,MODEL DAQ6510,04000001,1.6.3d"


def test_close_without_function_closes_several():  # manual example 1
    b = powered()
    b.daq.process("ROUT:CLOS (@101)")
    assert b.daq.process("ROUT:CLOS?") == "(@101)"
    b.daq.process("ROUT:CLOS (@103)")
    assert b.daq.process("ROUT:CLOS?") == "(@101,103)"
    b.daq.process("ROUT:OPEN (@ALLSLOTS)")
    assert b.daq.process("ROUT:CLOS?") == "(@)"


def test_close_with_function_then_read():  # manual example 2
    b = powered()
    b.daq.process("SENS:FUNC 'VOLT', (@101:102)")
    b.daq.process("SENS:FUNC 'RES', (@110)")
    b.daq.process("ROUT:CLOS (@101)")
    assert b.daq.process("READ?") == "5.000000E+00"
    b.daq.process("ROUT:CLOS (@102)")  # a channel with a function opens the others
    assert b.daq.process("ROUT:CLOS?") == "(@102)"
    assert b.daq.process("READ?") == "3.300000E+00"
    b.daq.process("ROUT:CLOS (@101:102)")  # only one channel when a function is set
    assert errors(b.daq) == ["-221"]


def test_scan_in_listed_order_and_trace_data():
    b = powered()
    b.daq.process("SENS:FUNC 'VOLT', (@101:102)")
    b.daq.process("ROUT:SCAN:CRE (@102,101)")
    assert b.daq.process("ROUT:SCAN?") == "(@102,101)"
    b.daq.process("INIT")
    assert b.daq.process("TRAC:ACT?") == "2"
    assert b.daq.process('TRAC:DATA? 1,2,"defbuffer1",READ') == "3.300000E+00,5.000000E+00"
    assert b.daq.process("ROUT:CLOS?") == "(@)"
    b.daq.process("ROUT:SCAN:CRE (@)")
    assert b.daq.process("ROUT:SCAN?") == "(@)"


def test_channel_outside_the_card():
    b = powered()
    b.daq.process("ROUT:CLOS (@121)")
    b.daq.process("ROUT:CLOS (@201)")
    assert errors(b.daq) == ["-222", "-222"]
    with pytest.raises(BenchError, match="'cards'"):
        bench(cards=3)


def test_second_card():
    b = powered(cards=2)
    b.daq.process("ROUT:CLOS (@201)")
    assert errors(b.daq) == []


def test_offset_fault_on_internal_dmm():
    b = powered()
    b.daq.inject("offset", value=0.5)
    b.daq.process("ROUT:CLOS (@101)")
    assert b.daq.process("READ?") == "5.500000E+00"
