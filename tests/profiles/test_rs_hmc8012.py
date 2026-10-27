"""rs-hmc8012, after the R&S HMC8012 SCPI Programmer's Manual (version 01, 2016)."""

import pytest

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

HMC = get_profile("rs-hmc8012")


def first_error(dmm):
    return dmm.process("SYST:ERR?").split(",")[0]


def test_identity():  # manual example: HAMEG,HMC8012,12345,01.000
    assert HMC().process("*IDN?") == "HAMEG,HMC8012,100001,01.000"


def test_measure_with_range_and_overload():
    dmm = HMC(volts=5.0, noise=0)
    assert dmm.process("MEAS:VOLT:DC?") == "5.00000000E+00"
    assert dmm.process("MEAS:VOLT:DC? 4") == "9.90000000E+37"  # the manual's overload answer
    assert dmm.process("MEAS? 40") == "5.00000000E+00"


@pytest.mark.parametrize("function, low, high", [
    ("VOLT", "4.00000000E-01", "7.50000000E+02"),
    ("CURR", "2.00000000E-02", "1.00000000E+01"),
    ("RES", "4.00000000E+02", "2.50000000E+08"),
])
def test_model_ranges(function, low, high):
    dmm = HMC()
    assert dmm.process(f"{function}:RANG? MIN;:{function}:RANG? MAX") == f"{low};{high}"
    dmm.process(f"{function}:RANG DEF")  # DEF is the smallest range on this model
    assert dmm.process(f"{function}:RANG:UPP?") == low


def test_function_unquoted_and_query_without_quotes():
    dmm = HMC(amps=0.01, noise=0)
    dmm.process("FUNC CURR")
    assert dmm.process("FUNC?") == "CURR"
    assert dmm.process("READ?") == "1.00000000E-02"
    assert dmm.process("FETC?") == "1.00000000E-02"


def test_conf_query_left_out():  # the manual documents no format for DC functions
    dmm = HMC()
    assert dmm.process("CONF?") is None
    assert first_error(dmm) == "-113"


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"profile": "rs-hmp4040", "port": 0},
                        "dmm": {"profile": "rs-hmc8012", "port": 0, "noise": 0}},
        "loads": {"r": {"type": "resistor", "ohms": 50}},
        "wiring": ["psu.ch2 -> r", "dmm.input -> r"],
    })
    b.psu.process("INST OUT2")
    b.psu.process("APPL 12,1")
    b.psu.process("OUTP 1")
    assert b.dmm.process("MEAS:VOLT:DC?") == "1.20000000E+01"
