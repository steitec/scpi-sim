"""siglent-spd3303x, after the Siglent SPD3303X Quick Start QS0503X-E01B, chapter 3 (remote control)."""

import pytest

from scpi_sim.bench import Bench
from scpi_sim.profiles import get_profile

SPD = get_profile("siglent-spd3303x")


def errors(psu):
    found = []
    while not (error := psu.process("SYST:ERR?")).startswith("0 "):
        found.append(error.split(",")[0])
    return found


def test_identity_has_five_fields():
    assert SPD().process("*IDN?") == "Siglent Technologies,SPD3303X,SPD3XIDD4R0001,1.01.01.02.05,V3.0"
    assert SPD().process("SYST:VERS?") == "1.01.01.02.05"


def test_channel_prefixed_settings():  # manual examples
    psu = SPD()
    psu.process("CH1:CURRent 0.5")
    assert psu.process("CH1:CURRent?") == "0.500"
    psu.process("CH1:VOLTage 25")
    assert psu.process("CH1:VOLTage?") == "25.000"
    psu.process("CH2:VOLT 3.3")
    assert psu.process("CH2:VOLT?") == "3.300"


def test_selected_channel():
    psu = SPD()
    psu.process("INSTrument CH2")
    assert psu.process("INSTrument?") == "CH2"
    psu.process("VOLT 10")
    assert psu.process("CH2:VOLT?") == "10.000"
    psu.process("INST CH3")  # CH3 cannot be selected
    assert errors(psu) == ["-224"]


def test_limits():
    psu = SPD()
    psu.process("CH1:VOLT 32;:CH1:CURR 3.2")
    assert errors(psu) == []
    psu.process("CH1:VOLT 33")
    psu.process("CH2:CURR 3.3")
    psu.process("CH3:VOLT 3")  # CH3 is fixed (front-panel switch)
    assert errors(psu) == ["-222", "-222", "-114"]


def test_output_and_measurements():
    psu = SPD(load_ohms=10)
    psu.process("CH1:VOLT 5;:CH1:CURR 1")
    psu.process("OUTPut CH1,ON")
    assert psu.process("MEAS:CURR? CH1") == "0.500"
    assert psu.process("MEAS:VOLT? CH1") == "5.000"
    assert psu.process("MEAS:POWE? CH1") == "2.500"
    psu.process("INST CH1")
    assert psu.process("MEAS:VOLT?") == "5.000"
    psu.process("OUTP CH1,OFF")
    assert psu.process("MEAS:VOLT? CH1") == "0.000"


def test_channel_3_is_a_fixed_5v_output():
    psu = SPD(load_ohms=10)
    psu.process("OUTP CH3,ON")
    assert psu.output(3)[:2] == (5.0, 0.5)


def test_status_register():
    psu = SPD(load_ohms=1)
    assert psu.process("SYST:STAT?") == "0x0004"  # independent mode, both off
    psu.process("CH1:VOLT 5;:CH1:CURR 1")
    psu.process("OUTP CH1,ON")  # 5 V into 1 ohm: current limit
    psu.process("OUTP CH2,ON")
    assert psu.process("SYST:STAT?") == "0x0035"


def test_on_a_bench():
    b = Bench.from_dict({
        "instruments": {"psu": {"profile": "siglent-spd3303x", "port": 0},
                        "dmm": {"type": "dmm", "port": 0, "noise": 0}},
        "loads": {"r": {"type": "resistor", "ohms": 100}},
        "wiring": ["psu.ch2 -> r", "dmm.input -> r"],
    })
    b.psu.process("CH2:VOLT 12;:CH2:CURR 1")
    b.psu.process("OUTP CH2,ON")
    assert b.dmm.process("MEAS:VOLT?") == "+1.20000000E+01"


def test_empty_error_queue_answers_like_the_manual():  # "Typical Return 0 No Error"
    assert SPD().process("SYST:ERR?") == "0 No Error"


def test_generic_commands_not_in_the_manual():
    psu = SPD()
    for command in ("OUTP:MODE?", "INST:NSEL 3", "INST:NSEL?", "OUTP?", "OUTP2?"):
        assert psu.process(command) is None
    assert psu.process("SYST:ERR?").startswith("-113,")


@pytest.mark.parametrize("command", ["*RST", "*CLS", "*ESR?", "*ESE 1", "*ESE?", "*SRE 1", "*SRE?",
                                     "*STB?", "*OPC", "*OPC?", "*WAI", "*TST?"])
def test_common_commands_not_in_the_manual(command):  # the manual lists only *IDN?, *SAV, *RCL
    psu = SPD()
    assert psu.process(command) is None
    assert errors(psu) == ["-113"]
