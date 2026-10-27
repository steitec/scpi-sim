"""rs-hmp4040, after the R&S HMP Series User Manual 1178.6833.02 - 05, chapter 8."""

import pytest

from scpi_sim.profiles import get_profile

HMP4040 = get_profile("rs-hmp4040")


def errors(psu):
    found = []
    while not (error := psu.process("SYST:ERR?")).startswith("0,"):
        found.append(error.split(",")[0])
    return found


def test_identity():
    assert HMP4040().process("*IDN?") == "Rohde&Schwarz,HMP4040,3629.6776.04/100001,2.70"
    assert HMP4040(serial="3629.6776.04/123456").process("*IDN?").split(",")[2] == "3629.6776.04/123456"


def test_select_channel_both_ways():  # manual example "Selecting a channel"
    psu = HMP4040()
    psu.process("INST OUT2")
    assert psu.process("INST?") == "OUT2"
    assert psu.process("INST:NSEL?") == "2"
    psu.process("INST:NSEL 4")
    assert psu.process("INST?") == "OUT4"
    for name in ("OUTPut3", "OUTP3", "OUT3"):
        psu.process(f"INST {name}")
        assert psu.process("INST:NSEL?") == "3"
    psu.process("INST OUT5")
    psu.process("INST:NSEL 5")
    assert errors(psu) and psu.process("INST?") == "OUT3"


def test_voltage_example():  # manual example "Configuring the output voltage"
    psu = HMP4040()
    psu.process("INST OUT1")
    psu.process("VOLT 10")
    assert psu.process("VOLT?") == "10.000"
    assert psu.process("VOLT? MIN") == "0.000"
    assert psu.process("VOLT? MAX") == "32.050"
    psu.process("VOLT MAX")
    assert psu.process("VOLT?") == "32.050"


def test_current_example():  # manual example "Configuring the current limit"
    psu = HMP4040()
    psu.process("INST OUT1")
    psu.process("CURR 2")
    assert psu.process("CURR?") == "2.000"
    assert psu.process("CURR? MAX") == "10.010"


def test_limits():
    psu = HMP4040()
    psu.process("VOLT 32.05;:CURR 10.01")
    assert errors(psu) == []
    psu.process("VOLT 33")
    psu.process("CURR 11")
    assert errors(psu) == ["-222", "-222"]


def test_apply():  # manual APPLy example
    psu = HMP4040()
    psu.process("INST OUT1")
    psu.process("APPL 6,2")
    assert psu.process("APPL?") == "6.000,2.000"
    psu.process("INST OUT2")
    assert psu.process("APPL?") == "1.000,1.000"  # *RST values of APPLy


def test_reset_values():
    psu = HMP4040()
    psu.process("APPL 5,3;*RST")
    assert psu.process("VOLT?;:CURR?") == "1.000;1.000"


def test_outputs_selected_then_general():  # manual example "Activating the channels"
    psu = HMP4040(load_ohms=100)
    psu.process("INST OUT1")
    psu.process("VOLT 12")
    psu.process("CURR 0.1")
    psu.process("OUTP:SEL 1")
    psu.process("INST OUT2")
    psu.process("VOLT 12")
    psu.process("CURR 0.2")
    psu.process("OUTP:SEL 1")
    assert psu.process("MEAS:VOLT?") == "0.000"  # selected, but the general output is still off
    psu.process("OUTP:GEN 1")
    assert psu.process("MEAS:VOLT?") == "12.000"
    psu.process("INST OUT1")
    assert psu.process("MEAS:CURR?") == "0.100"  # 12 V into 100 ohms wants 0.12 A: current limit
    assert psu.process("OUTP?") == "1"
    psu.process("OUTP:GEN 0")
    assert psu.process("OUTP?") == "0"
    psu.process("INST OUT2")
    assert psu.process("MEAS:VOLT?") == "0.000"


def test_outp_activates_channel_and_output():
    psu = HMP4040(load_ohms=10)
    psu.process("INST OUT1")
    psu.process("APPL 5,1")
    psu.process("OUTP 1")
    assert psu.process("OUTP?") == "1"
    assert psu.process("MEAS:CURR?") == "0.500"
    psu.process("OUTP 0")
    assert psu.process("OUTP?;:MEAS:VOLT?") == "0;0.000"


def test_idn_fault():
    psu = HMP4040()
    psu.inject("idn", value="HAMEG,HMP4040,000000,1.0")
    assert psu.process("*IDN?") == "HAMEG,HMP4040,000000,1.0"


@pytest.mark.parametrize("option", ["channels", "max_volts", "max_amps", "limits"])
def test_sizing_is_fixed(option):
    with pytest.raises(TypeError):
        HMP4040(**{option: 1})
