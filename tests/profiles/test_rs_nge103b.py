"""rs-nge103b, after the R&S NGE100B User Manual 5601.1343.02 - 09, chapter 7."""

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

NGE103B = get_profile("rs-nge103b")


def errors(psu):
    found = []
    while not (error := psu.process("SYST:ERR?")).startswith("0,"):
        found.append(error.split(",")[0])
    return found


def test_identity():  # manual example: Rohde&Schwarz,NGE103B,5601.1414k03/100421,1.20
    assert NGE103B().process("*IDN?") == "Rohde&Schwarz,NGE103B,5601.1414k03/100001,1.55"


def test_select_channel():  # manual example "Selecting a channel"
    psu = NGE103B()
    psu.process("INST OUT1")
    assert psu.process("INST?") == "OUTPut1"
    psu.process("INST:NSEL 3")
    assert psu.process("INST:NSEL?") == "3"
    psu.process("INST OUT4")
    psu.process("INST:NSEL 4")
    assert errors(psu) == ["-222", "-222"]


def test_voltage_and_current_examples():
    psu = NGE103B()
    psu.process("INST OUT1")
    psu.process("VOLT 10")
    assert psu.process("VOLT?") == "10.000"
    assert psu.process("VOLT? MIN") == "0.000"
    assert psu.process("VOLT? MAX") == "32.200"
    psu.process("CURR 2")
    assert psu.process("CURR?") == "2.000"
    assert psu.process("CURR? MIN") == "0.003"
    assert psu.process("CURR? MAX") == "3.025"


def test_limits():
    psu = NGE103B()
    psu.process("VOLT 32.2;:CURR 3.025;:CURR 0.003")
    assert errors(psu) == []
    psu.process("VOLT 32.3")
    psu.process("CURR 3.1")
    psu.process("CURR 0.001")
    assert errors(psu) == ["-222", "-222", "-222"]


def test_apply():
    psu = NGE103B()
    psu.process("INST OUT1")
    psu.process("APPL 6,2")
    assert psu.process("APPL?") == "6.000,2.000"


def test_outputs_and_their_queries():  # manual example "Activating the channels"
    psu = NGE103B(load_ohms=60)
    psu.process("INST OUT1")
    psu.process("VOLT 6")
    psu.process("CURR 0.1")
    psu.process("OUTP:SEL 1")
    psu.process("INST OUT2")
    psu.process("VOLT 12")
    psu.process("CURR 0.2")
    psu.process("OUTP:SEL 1")
    assert psu.process("OUTP:SEL?;:OUTP:GEN?") == "1;0"
    psu.process("OUTP:GEN 1")
    assert psu.process("OUTP:GEN?;:OUTP?") == "1;1"
    assert psu.process("MEAS:CURR?") == "0.200"
    assert psu.process("MEAS?") == "1.200E+01"
    assert psu.process("MEAS:VOLT?") == "1.200E+01"
    assert psu.process("MEAS:POW?") == "2.40E+00"
    assert psu.process("OUTP:MODE?") == "CV"


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"profile": "rs-nge103b", "port": 0}},
        "loads": {"a": {"type": "resistor", "ohms": 5}},
        "wiring": ["psu.ch3 -> a"],
    })
    b.psu.process("INST OUT3")
    b.psu.process("APPL 5,3")
    b.psu.process("OUTP ON")
    assert b.psu.process("MEAS:CURR?") == "1.000"
