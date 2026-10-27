"""keysight-e36312a, after the Keysight E36300 Series Programming Guide E36311-90008, Edition 3."""

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

E36312A = get_profile("keysight-e36312a")


def errors(psu):
    found = []
    while not (error := psu.process("SYST:ERR?")).startswith("0,"):
        found.append(error.split(",")[0])
    return found


def num(value):
    return f"{value:+.8E}"


def test_identity():
    assert E36312A().process("*IDN?") == "Keysight Technologies,E36312A,MY57000001,2.1.0-1.0.4-1.12"


def test_channel_list_settings_and_queries():
    psu = E36312A()
    psu.process("VOLT 5,(@1)")
    psu.process("VOLT 20, (@2:3)")  # manual example spelling
    assert psu.process("VOLT? (@1:3)") == ",".join(map(num, (5, 20, 20)))
    psu.process("CURR 0.5,(@2)")
    assert psu.process("CURR? (@1,2)") == ",".join(map(num, (5, 0.5)))  # *RST current 5 A on P6V


def test_limits_per_output():
    psu = E36312A()
    psu.process("VOLT 6.18,(@1)")
    psu.process("VOLT 25.75,(@2)")
    psu.process("CURR 5.15,(@1)")
    psu.process("CURR 1.03,(@3)")
    assert errors(psu) == []
    psu.process("VOLT 7,(@1)")
    psu.process("CURR 2,(@2)")
    assert errors(psu) == ["-222", "-222"]
    assert psu.process("VOLT? MAX,(@1:2)") == ",".join(map(num, (6.18, 25.75)))
    assert psu.process("CURR? MIN,(@1)") == num(0.001)


def test_bad_channel_list_gives_no_partial_answer():
    psu = E36312A()
    assert psu.process("VOLT? (@1:4)") is None
    psu.process("VOLT 1,(@1:4)")
    assert errors(psu) == ["-222", "-222"]
    assert psu.process("VOLT? (@1)") == num(0)


def test_select_by_name_and_number():
    psu = E36312A()
    psu.process("INST P25V")
    assert psu.process("INST?;:INST:NSEL?") == "P25V;2"
    psu.process("INST CH3")
    assert psu.process("INST?") == "N25V"
    psu.process("VOLT 12")
    assert psu.process("VOLT? (@3)") == num(12)
    psu.process("INST:NSEL 1")
    assert psu.process("INST?") == "P6V"


def test_apply():
    psu = E36312A()
    psu.process("APPL P6V,5,1")
    assert psu.process("APPL? P6V") == '"5.000000,1.000000"'
    psu.process("APPL CH2,12")  # one value: the voltage
    assert psu.process("APPL? CH2") == '"12.000000,1.000000"'
    assert psu.process("INST?") == "P25V"  # APPLy selects the output
    assert psu.process("APPL?") == '"12.000000,1.000000"'


def test_outputs_and_measurements_with_lists():  # Review Focus 3
    psu = E36312A(load_ohms=100)
    psu.process("VOLT 5,(@1:3)")
    psu.process("OUTP ON,(@2)")
    assert psu.process("OUTP? (@1:3)") == "0,1,0"
    assert psu.process("MEAS:VOLT? (@1:3)") == ",".join(map(num, (0, 5, 0)))
    assert psu.process("MEAS:CURR? (@2)") == num(0.05)
    psu.process("INST P25V")
    assert psu.process("OUTP?") == "1"
    assert psu.process("MEAS:VOLT? CH2") == num(5)


def test_reset():
    psu = E36312A()
    psu.process("VOLT 5,(@1);:OUTP ON,(@1);*RST")
    assert psu.process("VOLT? (@1);:OUTP? (@1);:CURR? (@1:3)") == f"{num(0)};0;{num(5)},{num(1)},{num(1)}"


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"profile": "keysight-e36312a", "port": 0},
                        "dmm": {"type": "dmm", "port": 0, "noise": 0}},
        "loads": {"r": {"type": "resistor", "ohms": 10}},
        "wiring": ["psu.ch2 -> r", "dmm.input -> r"],
    })
    b.psu.process("VOLT 12,(@2)")
    b.psu.process("OUTP ON,(@2)")
    assert b.dmm.process("MEAS:VOLT?") == "+1.00000000E+01"  # *RST current limit 1 A into 10 ohms


def test_output_mode_not_in_the_manual():
    psu = E36312A()
    psu.process("OUTP:MODE?")
    assert errors(psu) == ["-113"]
