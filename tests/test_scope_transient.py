"""Waveforms from devices: one-shot transients and periodic signals on wired scope channels."""

import time

import pytest

from scpi_sim import Device, Oscilloscope, Signal, Transient
from scpi_sim.bench import Bench

RAMP = ((0.0, 0.0), (1e-3, 12.0))


def scope(**kwargs):
    return Oscilloscope(noise=0, seed=1, **kwargs)


def armed(trigger_level=6.0):
    s = scope()
    s.process(f":CHAN1:SCAL 2;:CHAN1:OFFS 6;:TIM:SCAL 5e-4;:TRIG:LEV {trigger_level}")
    return s


def trace(s, count=1000, number=1):
    capture = s.current_capture()
    return s.read_volts(capture, number, 0, count, capture.window / count), capture


def event(*points, ripple=None):
    """A transient that has just finished: its edges lie between the last look and now."""
    time.sleep(0.001)
    transient = Transient(start=time.monotonic(), points=points or RAMP, ripple=ripple)
    time.sleep(transient.duration + 0.001)
    return transient


# --- the shape -----------------------------------------------------------------------------------


def test_values_between_points():
    t = Transient(start=100.0, points=((0, 0), (1, 10), (2, 10), (2, 4)))
    assert t.at(-1) == 0
    assert t.at(0.5) == pytest.approx(5)
    assert t.at(1.5) == 10
    assert t.at(2) == 4  # a step: the new value counts
    assert t.at(99) == 4
    sampler = t.sampler()
    assert sampler(100.5) == pytest.approx(5)
    assert sampler(0) == 0


def test_ripple_starts_when_the_shape_has_settled():
    t = Transient(start=0.0, points=((0, 0), (1, 5)), ripple=Signal("square", frequency=1, vpp=2, rise=1e-6))
    values = [t.sampler()(x) for x in (0.5, 1.25, 1.75)]
    assert values == pytest.approx([2.5, 6, 4])
    assert t.at(1.25) == 5


def test_properties_for_autoscale():
    t = Transient(start=0.0, points=((0, 2), (0.5, 12), (1, 4)))
    assert (t.vpp, t.offset, t.mean, t.duration) == (10, 7, 4, 1)


@pytest.mark.parametrize("points", [(), ((1, 0), (0, 1)), ((-1, 0),), ((0, float("nan")),), ((float("inf"), 1),)])
def test_bad_points(points):
    with pytest.raises(ValueError):
        Transient(start=0.0, points=points)


@pytest.mark.parametrize("slope, after, until, expected", [
    ("POS", 0, 10, 1.5),
    ("NEG", 0, 10, 3.5),
    ("EITH", 0, 10, 1.5),
    ("EITH", 2, 10, 3.5),
    ("POS", 2, 10, None),
    ("POS", 0, 1, None),
])
def test_crossing(slope, after, until, expected):
    t = Transient(start=0.0, points=((1, 0), (2, 10), (3, 10), (4, 0)))
    assert t.crossing(5, slope, after, until) == (None if expected is None else pytest.approx(expected))


def test_crossing_of_a_step():
    t = Transient(start=0.0, points=((1, 0), (1, 10)))
    assert t.crossing(5, "POS", 0, 2) == 1


# --- the trigger ---------------------------------------------------------------------------------


def test_single_waits_for_the_event_and_captures_it():
    s = armed()
    s.inputs[1] = lambda: 0.0
    s.process(":SING")
    assert s.process("*OPC?") is None  # nothing has happened yet
    event_now = event()
    s.inputs[1] = lambda: event_now
    assert s.process("*OPC?") == "1"
    volts, capture = trace(s)
    assert capture.triggered
    assert volts[0] == pytest.approx(0, abs=0.1)  # before the event, seen in the pretrigger
    assert volts[500] == pytest.approx(6, abs=0.2)  # the trigger point is the middle of the screen
    assert volts[-1] == pytest.approx(12, abs=0.1)  # after the event, though it is computed ahead


def test_event_before_arming_does_not_trigger():
    s = armed()
    old = event()
    s.inputs[1] = lambda: old
    time.sleep(0.002)
    s.process(":SING")
    assert s.process("*OPC?") is None


def test_capture_keeps_the_event_when_the_input_changes():
    s = armed()
    s.process(":SING")
    first = event()
    s.inputs[1] = lambda: first
    assert s.process("*OPC?") == "1"
    s.inputs[1] = lambda: 0.0
    volts, _ = trace(s)
    assert volts[-1] == pytest.approx(12, abs=0.1)


def test_normal_sweep_retriggers_on_each_new_event():
    s = armed()
    s.process(":TRIG:SWE NORM")
    first = event()
    s.inputs[1] = lambda: first
    _, one = trace(s)
    assert one.triggered
    _, same = trace(s)
    assert same.delay == one.delay  # no new edge: the old capture stays
    second = event()
    s.inputs[1] = lambda: second
    _, two = trace(s)
    assert two.delay > one.delay


def test_auto_sweep_shows_the_present_after_the_event():
    s = armed()
    done = Transient(start=time.monotonic() - 10, points=RAMP)
    s.inputs[1] = lambda: done
    s.process(":RUN")
    volts, capture = trace(s)
    assert not capture.triggered
    assert volts[0] == pytest.approx(12, abs=0.1)
    assert s.status == "auto"


def test_falling_slope_and_level_out_of_reach():
    s = armed()
    s.process(":TRIG:SLOP NEG;:SING")
    up = event()
    s.inputs[1] = lambda: up
    assert s.process("*OPC?") is None
    down = event((0.0, 12.0), (1e-3, 0.0))
    s.inputs[1] = lambda: down
    assert s.process("*OPC?") == "1"

    s = armed(trigger_level=13)
    s.process(":SING")
    up = event()
    s.inputs[1] = lambda: up
    assert s.process("*OPC?") is None


def test_no_trigger_fault_and_gnd_coupling():
    s = armed()
    s.inject("no_trigger")
    s.process(":SING")
    up = event()
    s.inputs[1] = lambda: up
    assert s.process("*OPC?") is None

    s = armed()
    s.process(":CHAN1:COUP GND;:SING")
    up = event()
    s.inputs[1] = lambda: up
    assert s.process("*OPC?") is None


def test_ac_coupling_moves_the_final_value_to_zero():
    s = scope()
    s.process(":CHAN1:COUP AC;:CHAN1:SCAL 2;:CHAN1:OFFS -6;:TIM:SCAL 5e-4;:TRIG:LEV -6;:SING")
    up = event()
    s.inputs[1] = lambda: up
    assert s.process("*OPC?") == "1"
    volts, _ = trace(s)
    assert volts[0] == pytest.approx(-12, abs=0.1)
    assert volts[-1] == pytest.approx(0, abs=0.1)


def test_other_channel_is_on_the_same_time_axis():
    s = armed()
    s.process(":CHAN2:DISP ON;:CHAN2:SCAL 2;:CHAN2:OFFS 6;:SING")
    up = event()
    later = Transient(start=up.start + 1e-3, points=RAMP)
    s.inputs[1], s.inputs[2] = (lambda: up), (lambda: later)
    assert s.process("*OPC?") == "1"
    one, _ = trace(s, number=1)
    two, _ = trace(s, number=2)
    assert one[-1] == pytest.approx(12, abs=0.1)
    assert two[500] == pytest.approx(0, abs=0.1)
    assert two[-1] > 1


def test_periodic_trigger_with_a_transient_on_another_channel():
    s = scope()
    s.process(":CHAN2:DISP ON;:CHAN2:SCAL 2;:CHAN2:OFFS 6;:TIM:SCAL 5e-4;:TRIG:LEV 0")
    done = Transient(start=time.monotonic() - 10, points=RAMP)
    s.inputs[2] = lambda: done
    volts, capture = trace(s, number=2)
    assert capture.triggered  # channel 1 keeps its sine
    assert volts[0] == pytest.approx(12, abs=0.1)


def test_autoscale_fits_the_event():
    s = scope()
    tr = Transient(start=time.monotonic() - 10, points=((0, 0), (0.01, 12)))
    s.inputs[1] = lambda: tr
    s.process(":CHAN2:DISP OFF")
    s.process(":AUT")
    assert float(s.process(":CHAN1:SCAL?")) * 8 >= 12
    assert float(s.process(":CHAN1:OFFS?")) == pytest.approx(6)
    assert float(s.process(":TRIG:LEV?")) == pytest.approx(6)
    assert 0.01 <= float(s.process(":TIM:SCAL?")) * 10 < 0.05


def test_measurements_come_from_the_samples():
    s = armed()
    s.process(":TIM:SCAL 2e-4;:SING")
    up = event()
    s.inputs[1] = lambda: up
    assert s.process("*OPC?") == "1"
    assert float(s.process(":MEAS:VMAX?")) == pytest.approx(12, abs=0.2)
    assert float(s.process(":MEAS:RIS?")) == pytest.approx(0.8e-3, rel=0.1)


# --- devices on a bench --------------------------------------------------------------------------


class Board(Device):
    """Rail with a power-on ramp and ripple; ``steady`` has only ripple, ``plain`` only a voltage."""

    terminals = {"vin": "input", "rail": "output", "steady": "output", "plain": "output"}

    def __init__(self):
        super().__init__()
        self.event = None

    def update(self):
        powered = self.input_voltage("vin") > 1
        if powered and self.event is None:
            self.event = Transient(start=time.monotonic(), points=((0, 0), (1e-3, 5)))
        elif not powered and self.event is not None:
            self.event = None

    def voltage(self, terminal):
        return 3.3

    def waveform(self, terminal):
        if terminal == "rail":
            return self.event
        if terminal == "steady":
            return Signal("sine", frequency=1e5, vpp=0.2, offset=3.3)
        return None


class BrokenBoard(Device):
    terminals = {"out": "output"}

    def waveform(self, terminal):
        return "not a waveform"


@pytest.fixture
def bench():
    return Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0}, "scope": {"type": "scope", "port": 0, "noise": 0}},
        "devices": {"board": {"class": f"{__name__}:Board"}},
        "wiring": ["psu.ch1 -> board.vin", "scope.ch1 -> board.rail", "scope.ch2 -> board.steady",
                   "scope.ch3 -> board.plain"],
    })


def test_single_catches_the_power_on_of_a_device(bench):
    psu, s = bench.instruments["psu"], bench.instruments["scope"]
    s.process(":CHAN1:SCAL 1;:CHAN1:OFFS 2.5;:TIM:SCAL 5e-4;:TRIG:LEV 2.5;:SING")
    assert s.process("*OPC?") is None
    psu.process("VOLT 5;CURR 1;:OUTP ON")
    time.sleep(0.002)
    assert s.process("*OPC?") == "1"
    volts, capture = trace(s)
    assert capture.triggered
    assert volts[0] == pytest.approx(0, abs=0.1)
    assert volts[-1] == pytest.approx(5, abs=0.1)


def test_device_can_give_a_periodic_signal(bench):
    s = bench.instruments["scope"]
    s.process(":TRIG:SOUR CHAN2;:TRIG:LEV 3.3;:CHAN2:DISP ON;:CHAN2:SCAL 0.05;:CHAN2:OFFS 3.3;:TIM:SCAL 2e-5")
    assert float(s.process(":MEAS:FREQ? CHAN2")) == pytest.approx(1e5, rel=0.01)
    assert float(s.process(":MEAS:VPP? CHAN2")) == pytest.approx(0.2, abs=0.01)


def test_device_without_waveform_shows_its_voltage(bench):
    s = bench.instruments["scope"]
    s.process(":CHAN3:DISP ON;:CHAN3:SCAL 1;:TIM:SCAL 1e-3")
    assert float(s.process(":MEAS:VAV? CHAN3")) == pytest.approx(3.3, abs=0.05)


def test_wrong_waveform_type_is_an_instrument_error():
    bench = Bench.from_dict({
        "instruments": {"scope": {"type": "scope", "port": 0, "noise": 0}},
        "devices": {"board": {"class": f"{__name__}:BrokenBoard"}},
        "wiring": ["scope.ch1 -> board.out"],
    })
    s = bench.instruments["scope"]
    assert s.process(":MEAS:VAV?") is None
    assert s.process("SYST:ERR?").startswith("-300")
