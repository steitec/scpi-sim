"""keithley-dmm6500, after the Keithley DMM6500 Reference Manual DMM6500-901-01 Rev. A, section 13 (SCPI)."""

import pytest

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

DMM = get_profile("keithley-dmm6500")


def first_error(dmm):
    return dmm.process("SYST:ERR?").split(",")[0]


def test_identity():  # manual example: KEITHLEY INSTRUMENTS,MODEL DMM6500,04089762,1.6.3d
    assert DMM().process("*IDN?") == "KEITHLEY INSTRUMENTS,MODEL DMM6500,04000001,1.6.3d"


def test_function_and_read():
    dmm = DMM(volts=-5.693831e-5, amps=0.25, noise=0)
    assert dmm.process(":SENS:FUNC?") == "VOLT:DC"
    assert dmm.process(":READ?") == "-5.693831E-05"  # the manual's reading format
    dmm.process(':SENS:FUNC "CURR"')
    assert dmm.process(":SENS:FUNC?;:READ?") == "CURR:DC;2.500000E-01"
    dmm.process(":FUNC 'VOLTage'")  # manual example, single quotes
    assert dmm.process(":FUNC?") == "VOLT:DC"


def test_measure_function_and_buffer_arguments():
    dmm = DMM(volts=3.3, ohms=4700, noise=0)
    assert dmm.process(":MEAS:VOLT:DC?") == "3.300000E+00"
    assert dmm.process(':MEAS:RES? "defbuffer1"') == "4.700000E+03"
    assert dmm.process(":FUNC?") == "RES"  # MEAS:<function>? leaves the function selected
    assert dmm.process(':MEAS:VOLT? "myBuffer"') is None  # user buffers are not simulated
    assert first_error(dmm) == "-224"


def test_ranges():
    dmm = DMM(volts=5.0, noise=0)
    dmm.process(":SENS:VOLT:RANG 1")
    assert dmm.process(":SENS:VOLT:RANG?;:SENS:VOLT:RANG:AUTO?") == "1.000000E+00;0"
    assert dmm.process(":READ?") == "9.900000E+37"
    dmm.process(":SENS:VOLT:RANG:AUTO ON")
    assert dmm.process(":READ?") == "5.000000E+00"
    dmm.process(":SENS:CURR:RANG 10E-6")  # manual example: the 10 uA range
    assert dmm.process(":SENS:CURR:RANG?") == "1.000000E-05"


@pytest.mark.parametrize("function, ranges", [
    ("VOLT", "1.000000E-01;1.000000E+03"),
    ("CURR", "1.000000E-05;1.000000E+01"),
    ("RES", "1.000000E+01;1.000000E+08"),
])
def test_model_ranges(function, ranges):
    assert DMM().process(f":SENS:{function}:RANG? MIN;:SENS:{function}:RANG? MAX") == ranges


def test_init_and_trace_data():
    dmm = DMM(volts=1.5, noise=0)
    dmm.process(":INIT")
    assert dmm.process(":TRAC:ACT?") == "1"
    assert dmm.process(':TRAC:DATA? 1,1,"defbuffer1",READ') == "1.500000E+00"
    assert dmm.process(":FETC?") == "1.500000E+00"


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"profile": "keysight-e36312a", "port": 0},
                        "dmm": {"profile": "keithley-dmm6500", "port": 0, "noise": 0}},
        "loads": {"r": {"type": "resistor", "ohms": 100}},
        "wiring": ["psu.ch1 -> r", "dmm.input -> r"],
    })
    b.psu.process("VOLT 5,(@1)")
    b.psu.process("OUTP ON,(@1)")
    assert b.dmm.process(":MEAS:VOLT?") == "5.000000E+00"
