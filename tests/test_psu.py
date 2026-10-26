from scpi_sim.instruments import PowerSupply
from scpi_sim import __version__


def test_output_off_reads_zero():
    p = PowerSupply()
    p.process("VOLT 12")
    assert p.process("MEAS:VOLT?;:OUTP:MODE?") == "+0.00000000E+00;OFF"


def test_no_load_cv():
    p = PowerSupply()
    p.process("VOLT 12;:OUTP ON")
    assert p.process("MEAS:VOLT?;:MEAS:CURR?;:OUTP:MODE?") == "+1.20000000E+01;+0.00000000E+00;CV"


def test_resistive_load_cv_and_cc():
    p = PowerSupply(load_ohms=24)
    p.process("VOLT 12;CURR 1;:OUTP ON")
    assert p.process("MEAS:CURR?") == "+5.00000000E-01"
    p.process("CURR 0.25")
    assert p.process("MEAS:VOLT?;:OUTP:MODE?") == "+6.00000000E+00;CC"


def test_long_form_with_optional_nodes():
    p = PowerSupply()
    p.process("SOURce:VOLTage:LEVel:IMMediate:AMPLitude 3.3")
    assert p.process("VOLT?") == "+3.30000000E+00"


def test_channels_and_suffix():
    p = PowerSupply(channels=3)
    p.process("INST:NSEL 2;:VOLT 5;:OUTP2 ON")
    assert p.process("OUTP2?;:OUTP1?;:INST?") == "1;0;CH2"
    p.process("OUTP4 ON")
    assert p.process("SYST:ERR?").startswith("-114")


def test_inst_select_by_name():
    p = PowerSupply(channels=2)
    p.process("INST CH2")
    assert p.process("INST:NSEL?") == "2"
    p.process("INST CH3")
    assert p.process("SYST:ERR?").startswith("-222")


def test_limits():
    p = PowerSupply()
    p.process("VOLT 31")
    assert p.process("SYST:ERR?").startswith("-222")
    assert p.process("VOLT? MAX;:CURR? MAX") == "+3.00000000E+01;+3.00000000E+00"


def test_rst():
    p = PowerSupply()
    p.process("VOLT 5;:OUTP ON;*RST")
    assert p.process("VOLT?;:CURR?;:OUTP?") == "+0.00000000E+00;+1.00000000E-01;0"


def test_idn():
    assert PowerSupply().process("*IDN?") == f"scpi-sim,PSU,SIM00002,{__version__}"


def test_extra_parameter_not_silently_ignored():
    p = PowerSupply()
    p.process("VOLT 5,1")
    assert p.process("SYST:ERR?") == '-108,"Parameter not allowed"'
    p.process("OUTP ON,OFF")
    assert p.process("SYST:ERR?") == '-108,"Parameter not allowed"'
    assert p.process("VOLT?;:OUTP?") == "+0.00000000E+00;0"


def test_short_circuit_goes_into_current_limit():
    p = PowerSupply(load_ohms=0)
    p.process("VOLT 1;:OUTP ON")
    assert p.process("MEAS:VOLT?;:MEAS:CURR?;:OUTP:MODE?") == "+0.00000000E+00;+1.00000000E-01;CC"
    p.process("VOLT 0")
    assert p.process("MEAS:CURR?;:OUTP:MODE?") == "+0.00000000E+00;CV"
    assert p.process("SYST:ERR?") == '0,"No error"'


def test_negative_load_rejected():
    import pytest
    with pytest.raises(ValueError):
        PowerSupply(load_ohms=-5)


def test_negative_zero_reads_back_positive():
    p = PowerSupply()
    p.process("VOLT -0")
    assert p.process("VOLT?") == "+0.00000000E+00"


def test_query_default():
    p = PowerSupply()
    assert p.process("VOLT? DEF;:CURR? DEF") == "+0.00000000E+00;+1.00000000E-01"


def test_source_suffix_selects_channel():
    p = PowerSupply(channels=2)
    p.process("SOUR2:VOLT 7;:SOURCE2:CURR 0.5")
    assert p.process("SOUR2:VOLT?;:SOUR2:CURR?;:SOUR1:VOLT?") == "+7.00000000E+00;+5.00000000E-01;+0.00000000E+00"
    assert p.process("INST:NSEL?") == "1"
    p.process("SOUR3:VOLT 1")
    assert p.process("SYST:ERR?").startswith("-114")
