"""siglent-sdm3065x, after the Siglent SDM3065X Remote Manual RC06036-E01A."""

import pytest

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

SDM = get_profile("siglent-sdm3065x")


def test_identity():
    assert SDM().process("*IDN?") == "Siglent Technologies,SDM3065X,SDM36FAD000001,1.01.01.25"


def test_conf_query_has_no_resolution():  # manual: "VOLT +2.00000000E-01"
    dmm = SDM(volts=0.1, noise=0)
    dmm.process("CONF:VOLT:DC 0.2")
    assert dmm.process("CONF?") == '"VOLT +2.00000000E-01"'


@pytest.mark.parametrize("function, ranges", [
    ("VOLT", "+2.00000000E-01;+1.00000000E+03"),
    ("CURR", "+2.00000000E-04;+1.00000000E+01"),
    ("RES", "+2.00000000E+02;+1.00000000E+08"),
])
def test_model_ranges(function, ranges):
    assert SDM().process(f"{function}:RANG? MIN;:{function}:RANG? MAX") == ranges


def test_measure_read_fetch_and_function():
    dmm = SDM(volts=1.5, noise=0)
    assert dmm.process("MEAS:VOLT:DC? 2") == "+1.50000000E+00"
    assert dmm.process("MEAS:VOLT:DC? 0.2") == "+9.90000000E+37"
    dmm.process('FUNC "VOLT"')
    dmm.process("VOLT:RANG:AUTO ON")
    dmm.process("INIT")
    assert dmm.process("FETC?") == "+1.50000000E+00"
    assert dmm.process("FUNC?") == '"VOLT"'


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"profile": "siglent-spd3303x", "port": 0},
                        "dmm": {"profile": "siglent-sdm3065x", "port": 0, "noise": 0}},
        "loads": {"r": {"type": "resistor", "ohms": 50}},
        "wiring": ["psu.ch1 -> r", "dmm.input -> r"],
    })
    b.psu.process("CH1:VOLT 5;:CH1:CURR 1")
    b.psu.process("OUTP CH1,ON")
    assert b.dmm.process("MEAS:VOLT:DC?") == "+5.00000000E+00"
