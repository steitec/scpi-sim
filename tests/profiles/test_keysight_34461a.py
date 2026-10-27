"""keysight-34461a, after the Keysight Truevolt Series Operating and Service Guide 34460-90901, Edition 7."""

import pytest

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

DMM = get_profile("keysight-34461a")


def first_error(dmm):
    return dmm.process("SYST:ERR?").split(",")[0]


def test_identity():
    assert DMM().process("*IDN?") == "Keysight Technologies,34461A,MY53200001,A.03.03-03.15-03.03-00.52-04-01"


def test_measure_and_overload():
    dmm = DMM(volts=5.0, noise=0)
    assert dmm.process("MEAS:VOLT:DC?") == "+5.00000000E+00"
    assert dmm.process("MEAS:VOLT:DC? 1") == "+9.90000000E+37"
    assert dmm.process("MEAS:VOLT:DC? 10,0.001") == "+5.00000000E+00"


def test_conf_query_has_range_and_resolution():  # default: 1 ppm of range, Resolution Table (34461A, 1 PLC)
    dmm = DMM(volts=5.0, noise=0)
    dmm.process("CONF:VOLT:DC 10")
    assert dmm.process("CONF?") == '"VOLT +1.00000000E+01,+1.00000000E-05"'
    dmm.process("CONF:VOLT:DC 10,0.001")
    assert dmm.process("CONF?") == '"VOLT +1.00000000E+01,+1.00000000E-03"'
    dmm.process("CONF:VOLT:DC")  # autorange: the range that fits 5 V
    assert dmm.process("CONF?") == '"VOLT +1.00000000E+01,+1.00000000E-05"'


def test_conf_read_and_init_fetch():
    dmm = DMM(volts=2.5, noise=0)
    dmm.process("CONF:VOLT:DC 10")
    assert dmm.process("READ?") == "+2.50000000E+00"
    dmm.process("INIT")
    assert dmm.process("FETC?") == "+2.50000000E+00"


def test_fetch_without_readings_is_an_error():
    dmm = DMM()
    assert dmm.process("FETC?") is None
    assert first_error(dmm) == "-230"


def test_function_select():
    dmm = DMM(amps=0.02, noise=0)
    assert dmm.process("FUNC?") == '"VOLT"'
    dmm.process('FUNC "CURR:DC"')
    assert dmm.process("FUNC?") == '"CURR"'
    assert dmm.process("READ?") == "+2.00000000E-02"
    dmm.process('FUNC "TEMP"')
    assert first_error(dmm) == "-224"


def test_sense_ranges():
    dmm = DMM(volts=5.0, noise=0)
    dmm.process("VOLT:DC:RANG 1")
    assert dmm.process("VOLT:DC:RANG?;:VOLT:DC:RANG:AUTO?") == "+1.00000000E+00;0"
    assert dmm.process("READ?") == "+9.90000000E+37"
    assert dmm.process("VOLT:RANG? MAX;:VOLT:RANG? MIN") == "+1.00000000E+03;+1.00000000E-01"
    dmm.process("SENS:VOLT:DC:RANG:AUTO ON")
    assert dmm.process("READ?") == "+5.00000000E+00"
    dmm.process("CURR:RANG 3")
    assert dmm.process("FUNC?") == '"VOLT"'  # a range of another function does not switch
    dmm.process("CONF:CURR")
    dmm.process("CURR:DC:RANG 3")
    assert dmm.process("CONF?") == '"CURR +3.00000000E+00,+3.00000000E-06"'


@pytest.mark.parametrize("function, ranges", [
    ("VOLT", "+1.00000000E-01;+1.00000000E+03"),
    ("CURR", "+1.00000000E-04;+1.00000000E+01"),
    ("RES", "+1.00000000E+02;+1.00000000E+08"),
])
def test_model_ranges(function, ranges):
    assert DMM().process(f"{function}:RANG? MIN;:{function}:RANG? MAX") == ranges


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0},
                        "dmm": {"profile": "keysight-34461a", "port": 0, "noise": 0}},
        "loads": {"main": {"type": "resistor", "ohms": 100}},
        "wiring": ["psu.ch1 -> main", "dmm.input -> main"],
    })
    b.psu.process("VOLT 3.3;CURR 1;:OUTP ON")
    assert b.dmm.process("MEAS:VOLT:DC?") == "+3.30000000E+00"


@pytest.mark.parametrize("resolution, expected", [
    ("MIN", "+3.00000000E-06"),  # best: 0.3 ppm of range (100 PLC), Resolution Table
    ("MAX", "+1.00000000E-03"),  # worst: 100 ppm of range (0.02 PLC)
    ("DEF", "+1.00000000E-05"),  # default: 1 ppm of range (1 PLC)
])
def test_conf_resolution_min_max(resolution, expected):  # review: MIN/MAX were treated as DEF
    dmm = DMM(volts=5.0, noise=0)
    dmm.process(f"CONF:VOLT:DC 10,{resolution}")
    assert dmm.process("CONF?") == f'"VOLT +1.00000000E+01,{expected}"'
    dmm.process(f"MEAS:CURR:DC? 1,{resolution.lower()}imum" if resolution != "DEF" else "MEAS:CURR:DC? 1,DEF")
    factor = {"MIN": 3e-7, "MAX": 1e-4, "DEF": 1e-6}[resolution]
    assert dmm.process("CONF?") == f'"CURR +1.00000000E+00,{dmm.format_number(factor)}"'
