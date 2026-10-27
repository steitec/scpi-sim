"""Station code for an R&S HMP4040 and a Keysight DAQ970A, tested against their profiles.

    pytest examples/profiles --scpi-bench examples/profiles/bench.yaml

``power_up`` and ``measure_rails`` are written exactly as for the real
instruments; only the resource strings come from the simulated bench.
"""

import pytest
import pyvisa


def open_instrument(rm, resource):
    return rm.open_resource(resource, read_termination="\n", write_termination="\n", timeout=1000)


def power_up(psu, rails):
    """Set every channel, select it, then switch all selected outputs on at once."""
    for channel, (volts, amps) in rails.items():
        psu.write(f"INST OUT{channel}")
        psu.write(f"APPL {volts},{amps}")
        psu.write("OUTP:SEL ON")
    psu.write("OUTP:GEN ON")
    psu.query("*OPC?")


def measure_rails(daq, channels):
    readings = daq.query(f"MEAS:VOLT:DC? (@{','.join(map(str, channels))})")
    return [float(value) for value in readings.split(",")]


@pytest.fixture
def station(scpi_bench):
    rm = pyvisa.ResourceManager("@py")
    psu, daq = (open_instrument(rm, scpi_bench.resource(name)) for name in ("psu", "daq"))
    yield psu, daq
    psu.close()
    daq.close()
    rm.close()


def test_identities(station):
    psu, daq = station
    assert psu.query("*IDN?").startswith("Rohde&Schwarz,HMP4040,")
    assert daq.query("*IDN?").startswith("Keysight Technologies,DAQ970A,")


def test_rails_come_up(station):
    psu, daq = station
    power_up(psu, {1: (12, 1), 2: (5, 1)})
    assert measure_rails(daq, [101, 102]) == pytest.approx([12, 5], rel=1e-3)
    psu.write("INST OUT1")
    assert float(psu.query("MEAS:CURR?")) == pytest.approx(0.5, abs=0.002)


def test_current_limit_pulls_a_rail_down(station):
    psu, daq = station
    power_up(psu, {1: (12, 0.25), 2: (5, 1)})  # 12 V into 24 ohms wants 0.5 A
    assert measure_rails(daq, [101]) == pytest.approx([6], rel=1e-3)
