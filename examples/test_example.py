"""Testing instrument code against a simulated bench.

    pytest examples --scpi-bench examples/bench.yaml

``measure_rail`` stands for your own test-station code; the tests run it
against simulated instruments, including a dead multimeter.
"""

import pytest
import pyvisa


def open_instrument(rm, resource):
    return rm.open_resource(resource, read_termination="\n", write_termination="\n", timeout=500)


def measure_rail(dmm, mux, channel: int) -> float:
    """Your code: route a rail to the multimeter and measure it."""
    # *OPC? waits until the relay has switched -- the multimeter is a different
    # connection, so a plain write could still be in flight when it measures
    mux.query(f"ROUT:OPEN:ALL;:ROUT:CLOS (@{channel});*OPC?")
    return float(dmm.query("MEAS:VOLT:DC?"))


@pytest.fixture
def station(scpi_bench):
    rm = pyvisa.ResourceManager("@py")
    psu, dmm, mux = (open_instrument(rm, scpi_bench.resource(name)) for name in ("psu", "dmm", "mux"))
    psu.query("SOUR1:VOLT 12;:SOUR1:CURR 1;:SOUR2:VOLT 3.3;:SOUR2:CURR 1;:OUTP1 ON;:OUTP2 ON;*OPC?")
    yield psu, dmm, mux
    for instrument in (psu, dmm, mux):
        instrument.close()
    rm.close()


def test_rails_within_limits(station):
    psu, dmm, mux = station
    assert measure_rail(dmm, mux, 101) == pytest.approx(12.0, rel=0.01)
    assert measure_rail(dmm, mux, 102) == pytest.approx(3.3, rel=0.01)
    assert float(psu.query("INST:NSEL 1;:MEAS:CURR?")) == pytest.approx(0.5)


def test_rail_in_current_limit_is_caught(station):
    psu, dmm, mux = station
    psu.query("SOUR2:CURR 0.05;*OPC?")  # the 3.3 V rail draws 0.1 A -> current limit
    assert measure_rail(dmm, mux, 102) < 3.3 * 0.9


def test_dead_multimeter_raises_timeout(station, scpi_bench):
    dmm, mux = station[1], station[2]
    scpi_bench.dmm.inject("no_response")
    with pytest.raises(pyvisa.errors.VisaIOError):
        measure_rail(dmm, mux, 101)
