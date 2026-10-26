from scpi_sim.instruments import Multimeter


def test_measure_voltage_exact():
    assert Multimeter(volts=3.3, noise=0).process("MEAS:VOLT:DC?") == "+3.30000000E+00"


def test_measure_current_and_resistance():
    d = Multimeter(amps=0.25, ohms=4700, noise=0)
    assert d.process("MEAS:CURR?;:MEAS:RES?") == "+2.50000000E-01;+4.70000000E+03"


def test_fixed_range_overload():
    assert Multimeter(volts=5.0, noise=0).process("MEAS:VOLT? 1") == "+9.90000000E+37"


def test_fixed_range_fits():
    assert Multimeter(volts=5.0, noise=0).process("MEAS:VOLT? 5") == "+5.00000000E+00"


def test_auto_range_overload_above_largest():
    assert Multimeter(volts=1500, noise=0).process("MEAS:VOLT?") == "+9.90000000E+37"


def test_min_range():
    assert Multimeter(volts=0.5, noise=0).process("MEAS:VOLT? MIN") == "+9.90000000E+37"


def test_range_too_large():
    d = Multimeter(noise=0)
    d.process("MEAS:VOLT? 2000")
    assert d.process("SYST:ERR?").startswith("-222")


def test_conf_and_read():
    d = Multimeter(ohms=470, noise=0)
    d.process("CONF:RES")
    assert d.process("CONF?") == '"RES AUTO"'
    assert d.process("READ?") == "+4.70000000E+02"


def test_conf_fixed_range_reported():
    d = Multimeter(noise=0)
    d.process("CONF:VOLT:DC 10")
    assert d.process("CONF?") == '"VOLT +1.00000000E+01"'


def test_measure_reconfigures_and_rst():
    d = Multimeter(noise=0)
    d.process("MEAS:CURR? 1")
    assert d.process("CONF?") == '"CURR +1.00000000E+00"'
    d.process("*RST")
    assert d.process("CONF?") == '"VOLT AUTO"'


def test_noise_is_seeded_and_small():
    a, b = Multimeter(seed=1), Multimeter(seed=1)
    ra, rb = a.process("MEAS:VOLT?"), b.process("MEAS:VOLT?")
    assert ra == rb and ra != "+5.00000000E+00" and abs(float(ra) - 5.0) < 0.01


def test_python_side_input_change():
    d = Multimeter(noise=0)
    d.volts = 1.8
    assert d.process("MEAS:VOLT?") == "+1.80000000E+00"


def test_idn():
    assert Multimeter().process("*IDN?") == "scpi-sim,DMM,SIM00001,0.1.0"


def test_measure_accepts_resolution_argument():
    d = Multimeter(volts=3.3, noise=0)
    assert d.process("MEAS:VOLT? 10,0.001") == "+3.30000000E+00"
    assert d.process("SYST:ERR?") == '0,"No error"'


def test_negative_range_uses_magnitude():
    d = Multimeter(volts=-3.0, noise=0)
    assert d.process("MEAS:VOLT? -10") == "-3.00000000E+00"
    assert d.process("SYST:ERR?") == '0,"No error"'


def test_negative_overload_is_negative():
    assert Multimeter(volts=-5.0, noise=0).process("MEAS:VOLT? 1") == "-9.90000000E+37"
