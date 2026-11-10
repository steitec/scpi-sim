"""Check a board's power-on with an oscilloscope: arm, switch on, read the capture.

    pytest examples/power_on --scpi-bench examples/power_on/bench.yaml
"""

import time

import pytest
import pyvisa

VOLTS_PER_AMP = 0.1
MAX_INRUSH_AMPS = 3.0


@pytest.fixture
def station(scpi_bench):
    rm = pyvisa.ResourceManager("@py")
    psu, scope = (rm.open_resource(scpi_bench.resource(name), read_termination="\n",
                                   write_termination="\n", timeout=2000) for name in ("psu", "scope"))
    psu.query("OUTP OFF;*OPC?")
    yield psu, scope
    psu.close()
    scope.close()
    rm.close()


def wait(seconds):
    """Wait by the simulator's clock, time.monotonic(). On Windows it moves in steps of
    up to 16 ms, so a plain time.sleep() can end before the simulator sees any time pass."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(0.001)


def power_on_capture(psu, scope):
    """Arm a single capture on the rising edge of the current, then switch the board on."""
    scope.write(":CHAN1:DISP ON;:CHAN1:SCAL 0.05;:CHAN1:OFFS 0.15")
    scope.write(":CHAN2:DISP ON;:CHAN2:SCAL 1;:CHAN2:OFFS 2")
    scope.write(":TIM:SCAL 2e-3;:TIM:POS 8e-3")
    scope.write(":TRIG:SOUR CHAN1;:TRIG:SLOP POS;:TRIG:LEV 0.02")
    scope.query(":SING;:OPER:COND?")  # a query: each instrument has its own connection, so wait until it is armed
    psu.write("VOLT 12;CURR 3;:OUTP ON")
    end = time.monotonic() + 2
    while int(scope.query(":OPER:COND?")) & 8:  # bit 3: still running
        assert time.monotonic() < end, "no trigger: the board drew no current"


def waveform(scope, channel):
    """(seconds after the trigger, volts) of a channel."""
    scope.write(f":WAV:SOUR CHAN{channel};:WAV:FORM BYTE")
    _, _, _, _, xinc, xorigin, _, yinc, yorigin, yref = scope.query(":WAV:PRE?").split(",")
    codes = scope.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    return ([float(xorigin) + i * float(xinc) for i in range(len(codes))],
            [(code - float(yref)) * float(yinc) + float(yorigin) for code in codes])


def mean_amps(times, volts, start, end):
    """The current between two times: one sample alone is at the mercy of the noise."""
    window = [v for t, v in zip(times, volts) if start <= t <= end]
    return sum(window) / len(window) / VOLTS_PER_AMP


def test_inrush_stays_below_the_limit(station):
    psu, scope = station
    power_on_capture(psu, scope)
    times, volts = waveform(scope, 1)
    assert max(volts) / VOLTS_PER_AMP == pytest.approx(2.2, abs=0.2)
    assert max(volts) / VOLTS_PER_AMP < MAX_INRUSH_AMPS
    assert min(v for t, v in zip(times, volts) if t < 0) == pytest.approx(0, abs=0.02)  # nothing before the edge


def test_too_much_inrush_is_seen(station, scpi_bench):
    psu, scope = station
    scpi_bench.board.inrush_amps = 2.8
    power_on_capture(psu, scope)
    _, volts = waveform(scope, 1)
    assert max(volts) / VOLTS_PER_AMP == pytest.approx(2.8, abs=0.2)


def test_cpu_start_shows_as_a_load_step(station):
    psu, scope = station
    power_on_capture(psu, scope)
    times, volts = waveform(scope, 1)
    assert mean_amps(times, volts, 3e-3, 4e-3) == pytest.approx(0.3, abs=0.05)
    assert mean_amps(times, volts, 8e-3, 12e-3) == pytest.approx(1.3, abs=0.05)


def test_rail_comes_up_after_the_inrush(station):
    psu, scope = station
    power_on_capture(psu, scope)
    times, volts = waveform(scope, 2)
    rising = next(t for t, v in zip(times, volts) if v > 0.33)
    settled = next(t for t, v in zip(times, volts) if v > 2.97)
    assert 1e-3 < rising < 3e-3
    assert settled - rising == pytest.approx(2.4e-3, abs=0.3e-3)
    assert volts[-1] == pytest.approx(3.3, abs=0.1)


def test_ripple_on_the_running_rail(station):
    psu, scope = station
    psu.query("VOLT 12;CURR 3;:OUTP ON;*OPC?")
    wait(0.01)  # the rail has settled after 5 ms; the ripple starts then
    scope.write(":CHAN1:DISP OFF;:CHAN2:DISP ON;:CHAN2:COUP AC;:CHAN2:SCAL 0.01;:CHAN2:OFFS 0")
    scope.write(":TIM:SCAL 2e-6;:TIM:POS 0;:TRIG:SOUR CHAN2;:TRIG:SWE AUTO;:RUN")
    assert float(scope.query(":MEAS:VPP? CHAN2")) == pytest.approx(0.03, abs=0.025)


def test_no_edge_without_power(station):
    psu, scope = station
    scope.write(":TRIG:SOUR CHAN1;:TRIG:SLOP POS;:TRIG:LEV 0.02;:SING")
    assert int(scope.query(":OPER:COND?")) & 8  # still waiting
