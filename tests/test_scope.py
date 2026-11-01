"""The generic oscilloscope: signals, trigger, acquisitions, measurements, binary transfer."""

import math
import socket
import struct

import pytest

from scpi_sim import Oscilloscope, Signal
from scpi_sim.bench import Bench, BenchError
from scpi_sim.instrument import block
from scpi_sim.instruments.scope import parse_signal, signals_option
from scpi_sim.instruments.scope_measure import measure
from scpi_sim.transport import InstrumentServer

NO_VALUE = 9.9e37


def scope(**kwargs):
    kwargs.setdefault("noise", 0)
    kwargs.setdefault("seed", 1)
    return Oscilloscope(**kwargs)


def parse_block(raw):
    assert raw[:1] == b"#"
    digits = int(raw[1:2])
    length = int(raw[2:2 + digits])
    data = raw[2 + digits:2 + digits + length]
    assert len(data) == length
    return data


def waveform(s):
    """Volts by the formula in the programmer's guide."""
    fields = s.process(":WAV:PRE?").split(",")
    yinc, yorigin, yref = float(fields[7]), float(fields[8]), int(fields[9])
    return [(code - yref) * yinc + yorigin for code in parse_block(s.process(":WAV:DATA?"))]


def error(s):
    return s.process(":SYST:ERR?").split(",")[0]


# --- signals -------------------------------------------------------------------------


@pytest.mark.parametrize("signal, t, expected", [
    (Signal("sine", 1e3, 2.0), 0.25e-3, 1.0),
    (Signal("sine", 1e3, 2.0, offset=1.0), 0.75e-3, 0.0),
    (Signal("square", 1e3, 2.0), 0.25e-3, 1.0),
    (Signal("square", 1e3, 2.0), 0.75e-3, -1.0),
    (Signal("square", 1e3, 2.0, duty=0.1), 0.25e-3, -1.0),
    (Signal("triangle", 1e3, 2.0), 0.0, 0.0),
    (Signal("triangle", 1e3, 2.0), 0.25e-3, 1.0),
    (Signal("sawtooth", 1e3, 2.0), 0.0, -1.0),
    (Signal(offset=3.3), 0.1, 3.3),
])
def test_signal_values(signal, t, expected):
    assert signal.sampler()(t) == pytest.approx(expected, abs=1e-9)


def test_signal_rejects_nonsense():
    with pytest.raises(ValueError, match="shape"):
        Signal("noise")
    with pytest.raises(ValueError, match="frequency"):
        Signal("sine", frequency=0)
    with pytest.raises(ValueError, match="duty"):
        Signal("square", duty=1.0)


def test_parse_signal():
    channel, signal = parse_signal("2:square,freq=10k,vpp=3.3,offset=1.65,duty=0.25,phase=90")
    assert channel == 2
    assert signal == Signal("square", 1e4, 3.3, 1.65, 0.25, 90.0)
    assert parse_signal("CH1:sine,freq=1MHz,vpp=500m")[1] == Signal("sine", 1e6, 0.5)
    for bad in ("sine", "x:sine", "1:sine,color=red", "1:wobble", "1:sine,freq=fast"):
        with pytest.raises(ValueError):
            parse_signal(bad)


def test_signals_option():
    assert signals_option({1: {"shape": "sine", "vpp": 2}, "ch2": {"offset": 5}}) == {
        1: Signal("sine", vpp=2), 2: Signal(offset=5)}
    for bad in ("sine", {"x": {}}, {1: "sine"}, {1: {"colour": 1}}):
        with pytest.raises(ValueError):
            signals_option(bad)


def test_unknown_channel_in_signals():
    with pytest.raises(ValueError, match="channel 3"):
        Oscilloscope(channels=2, signals={3: Signal()})
    with pytest.raises(ValueError, match="2 or 4"):
        Oscilloscope(channels=3)


# --- measurements on a known record ---------------------------------------------------


def test_measure_square():
    dt = 1e-6
    volts = [3.0 if (i % 1000) < 250 else 0.0 for i in range(4000)]  # 1 kHz, 25 % duty
    m = measure(volts, dt)
    assert m["vmax"] == 3.0 and m["vmin"] == 0.0 and m["vpp"] == 3.0
    assert m["vtop"] == pytest.approx(3.0, abs=0.03) and m["vbase"] == pytest.approx(0.0, abs=0.03)
    assert m["frequency"] == pytest.approx(1000, rel=1e-3)
    assert m["pwidth"] == pytest.approx(250e-6, rel=1e-2)
    assert m["pduty"] == pytest.approx(0.25, rel=1e-2)
    assert m["nduty"] == pytest.approx(0.75, rel=1e-2)
    assert m["vavg"] == pytest.approx(0.75)


def test_measure_sine():
    volts = [math.sin(2 * math.pi * i / 1000) for i in range(5000)]
    m = measure(volts, 1e-6)
    assert m["vrms"] == pytest.approx(1 / math.sqrt(2), rel=1e-3)
    assert m["vtop"] == pytest.approx(1.0)  # no flat top: the extremes
    assert m["frequency"] == pytest.approx(1000, rel=1e-3)
    assert m["rise"] == pytest.approx(0.295e-3, rel=0.01)  # asin(0.8) / pi of a period


def test_measure_flat_has_no_timing():
    m = measure([1.0] * 100, 1e-6)
    assert m["vpp"] == 0.0 and m["vavg"] == 1.0
    assert m["frequency"] is None and m["rise"] is None and m["pduty"] is None


def test_measure_one_edge_has_no_period():
    m = measure([0.0] * 50 + [1.0] * 50, 1e-6)
    assert m["frequency"] is None
    assert m["vpp"] == 1.0


def test_measure_nothing():
    assert measure([], 1e-6)["vpp"] is None


# --- acquisition and transfer ------------------------------------------------------------


def test_identity_and_defaults():
    s = scope()
    assert s.process("*IDN?").startswith("scpi-sim,SCOPE,SIM00004,")
    assert s.process(":CHAN1:DISP?") == "1" and s.process(":CHAN2:DISP?") == "0"
    assert s.process(":WAV:FORM?") == "BYTE"
    assert s.process(":WAV:SOUR?") == "CHAN1"
    assert s.process(":TRIG:SWE?") == "AUTO"


def test_waveform_matches_the_signal():
    s = scope(signals={1: Signal("sine", 1e3, 4.0)})
    s.process(":TIM:SCAL 2e-4")  # 2 ms on screen: two periods
    s.process(":TRIG:LEV 0")
    volts = waveform(s)
    assert len(volts) == 1000
    assert max(volts) == pytest.approx(2.0, abs=0.04)
    assert min(volts) == pytest.approx(-2.0, abs=0.04)
    # triggered on the rising edge at the middle of the screen
    assert abs(volts[500]) < 0.04 and volts[510] > volts[490]


def test_preamble_format():
    s = scope()
    s.process(":TIM:SCAL 2e-4")
    fields = s.process(":WAV:PRE?").split(",")
    assert len(fields) == 10
    assert fields[:4] == ["+0", "+0", "+1000", "+1"]
    assert float(fields[4]) == pytest.approx(2e-6)
    assert float(fields[5]) == pytest.approx(-1e-3)
    assert float(fields[7]) == pytest.approx(1 / 32)  # 256 steps on 8 divisions
    assert fields[9] == "+128"


def test_x_and_y_queries_agree_with_preamble():
    s = scope()
    s.process(":TIM:SCAL 1e-4")
    fields = s.process(":WAV:PRE?").split(",")
    assert float(s.process(":WAV:XINC?")) == pytest.approx(float(fields[4]))
    assert float(s.process(":WAV:XOR?")) == pytest.approx(float(fields[5]))
    assert s.process(":WAV:XREF?") == "0"
    assert float(s.process(":WAV:YINC?")) == pytest.approx(float(fields[7]))
    assert float(s.process(":WAV:YOR?")) == pytest.approx(float(fields[8]))
    assert s.process(":WAV:YREF?") == "128"


def test_offset_moves_the_center():
    s = scope(signals={1: Signal("sine", 1e3, 2.0, offset=5.0)})
    s.process(":CHAN1:OFFS 5;:TRIG:LEV 5;:TIM:SCAL 2e-4")
    volts = waveform(s)
    assert float(s.process(":WAV:YOR?")) == pytest.approx(5.0)
    assert max(volts) == pytest.approx(6.0, abs=0.04) and min(volts) == pytest.approx(4.0, abs=0.04)


def test_word_format():
    s = scope()
    s.process(":TIM:SCAL 2e-4;:WAV:FORM WORD")
    fields = s.process(":WAV:PRE?").split(",")
    assert fields[0] == "+1" and fields[9] == "+32768"
    assert float(fields[7]) == pytest.approx(1 / 32 / 256)
    raw = parse_block(s.process(":WAV:DATA?"))
    assert len(raw) == 2000
    values = struct.unpack(">1000H", raw)
    volts = [(v - 32768) * float(fields[7]) for v in values]
    assert max(volts) == pytest.approx(1.0, abs=0.04)
    s.process(":WAV:BYT LSBF")
    assert s.process(":WAV:BYT?") == "LSBF"
    assert parse_block(s.process(":WAV:DATA?"))[0] == 0  # the low byte first: 8 significant bits


def test_signed_data():
    s = scope()
    s.process(":WAV:UNS OFF;:TIM:SCAL 2e-4")
    assert s.process(":WAV:UNS?") == "0"
    codes = [b - 256 if b > 127 else b for b in parse_block(s.process(":WAV:DATA?"))]
    assert max(codes) == 32 and min(codes) == -32  # 1 V at 32 steps per volt, 1 V/div


def test_ascii_format():
    s = scope()
    s.process(":WAV:FORM ASC;:TIM:SCAL 2e-4")
    assert s.process(":WAV:PRE?").startswith("+4,")
    volts = [float(v) for v in s.process(":WAV:DATA?").split(",")]
    assert len(volts) == 1000 and max(volts) == pytest.approx(1.0, abs=0.04)


def test_points():
    s = scope()
    s.process(":WAV:POIN 250;:TIM:SCAL 1e-3")
    assert s.process(":WAV:POIN?") == "250"
    assert len(parse_block(s.process(":WAV:DATA?"))) == 250
    assert s.process(":WAV:POIN:MODE?") == "NORM"


def test_raw_points_only_on_a_stopped_scope():
    s = scope()
    s.process(":TIM:SCAL 1e-3;:WAV:POIN:MODE RAW;:WAV:POIN 8000000")
    assert int(s.process(":WAV:POIN?")) == 62_500  # running: the measurement record
    s.process(":STOP")
    assert int(s.process(":WAV:POIN?")) == 1_000_000
    s.process(":WAV:POIN 200000")
    assert len(parse_block(s.process(":WAV:DATA?"))) == 200_000


def test_channel_off_gives_no_data():
    s = scope()
    s.process(":WAV:SOUR CHAN2")
    assert s.process(":WAV:DATA?") is None
    assert error(s) == "-221"
    assert float(s.process(":MEAS:VPP? CHAN2")) == NO_VALUE


def test_binary_block_is_joined_with_text():
    s = scope()
    answer = s.process(":WAV:POIN 100;:WAV:DATA?;:WAV:POIN?")
    assert isinstance(answer, bytes) and answer.endswith(b";100")
    assert len(parse_block(answer)) == 100


def test_block_header_has_eight_digits_as_in_the_guide():
    assert scope().process(":WAV:DATA?").startswith(b"#800001000")


def test_block_helper():
    assert block(b"abc") == b"#13abc"
    assert block(b"x" * 1000).startswith(b"#41000x")


# --- clipping and aliasing -----------------------------------------------------------------


def test_clipping_shows_in_data_and_measurements():
    s = scope(signals={1: Signal("sine", 1e3, 20.0)})
    s.process(":CHAN1:SCAL 1;:TIM:SCAL 2e-4")
    assert float(s.process(":MEAS:VPP?")) == pytest.approx(254 / 32)  # the screen, not the 20 V
    codes = parse_block(s.process(":WAV:DATA?"))
    assert min(codes) == 1 and max(codes) == 255  # the guide: clipped low and clipped high
    s.process(":CHAN1:SCAL 5")
    assert float(s.process(":MEAS:VPP?")) == pytest.approx(20.0, rel=0.02)


def test_too_slow_a_timebase_aliases():
    s = scope(signals={1: Signal("sine", 10e6, 2.0)})
    s.process(":TIM:SCAL 1;:TRIG:LEV 0;:ACQ:POIN?")
    assert float(s.process(":ACQ:SRAT?")) == pytest.approx(1e5)  # 1 Mpoint on 10 s
    frequency = float(s.process(":MEAS:FREQ?"))
    assert frequency == NO_VALUE or frequency < 1e6  # not the 10 MHz that are there


def test_no_period_on_screen_no_frequency():
    s = scope(signals={1: Signal("sine", 1e3, 2.0)})
    s.process(":TIM:SCAL 1e-6")
    assert float(s.process(":MEAS:FREQ?")) == NO_VALUE


def test_rate_divides_by_channels_on():
    s = scope()
    s.process(":TIM:SCAL 1e-6")
    assert float(s.process(":ACQ:SRAT?")) == pytest.approx(2e9)
    s.process(":CHAN2:DISP ON")
    assert float(s.process(":ACQ:SRAT?")) == pytest.approx(1e9)
    s.process(":CHAN3:DISP ON")
    assert float(s.process(":ACQ:SRAT?")) == pytest.approx(5e8)


# --- measurements through commands -------------------------------------------------------------


def test_measurement_commands():
    s = scope(signals={1: Signal("square", 1e3, 3.3, offset=1.65, duty=0.25)})
    s.process(":TRIG:LEV 1.65;:CHAN1:OFFS 1.65;:TIM:SCAL 5e-4")
    assert float(s.process(":MEAS:VPP?")) == pytest.approx(3.3, abs=0.07)
    assert float(s.process(":MEAS:VMAX?")) == pytest.approx(3.3, abs=0.04)
    assert float(s.process(":MEAS:VMIN?")) == pytest.approx(0.0, abs=0.04)
    assert float(s.process(":MEAS:VTOP?")) == pytest.approx(3.3, abs=0.07)
    assert float(s.process(":MEAS:VBAS?")) == pytest.approx(0.0, abs=0.07)
    assert float(s.process(":MEAS:VAMP?")) == pytest.approx(3.3, abs=0.1)
    assert float(s.process(":MEAS:FREQ?")) == pytest.approx(1e3, rel=1e-2)
    assert float(s.process(":MEAS:PER?")) == pytest.approx(1e-3, rel=1e-2)
    assert float(s.process(":MEAS:PWID?")) == pytest.approx(250e-6, rel=2e-2)
    assert float(s.process(":MEAS:NWID?")) == pytest.approx(750e-6, rel=2e-2)
    assert float(s.process(":MEAS:DUTY?")) == pytest.approx(25, rel=2e-2)
    assert float(s.process(":MEAS:NDUT?")) == pytest.approx(75, rel=2e-2)
    assert float(s.process(":MEAS:RIS?")) == pytest.approx(8e-6, rel=0.2)  # 80 % of the 1 % edge
    assert float(s.process(":MEAS:FALL?")) == pytest.approx(8e-6, rel=0.2)
    assert float(s.process(":MEAS:OVER?")) == pytest.approx(0, abs=3)
    assert float(s.process(":MEAS:PRES?")) == pytest.approx(0, abs=3)


def test_measurement_source():
    s = scope(signals={2: Signal("sine", 1e3, 1.0)})
    s.process(":CHAN2:DISP ON;:MEAS:SOUR CHAN2;:TIM:SCAL 5e-4")
    assert s.process(":MEAS:SOUR?") == "CHAN2"
    assert float(s.process(":MEAS:VPP?")) == pytest.approx(1.0, abs=0.07)
    assert float(s.process(":MEAS:VPP? CHAN1")) == pytest.approx(2.0, abs=0.07)
    assert float(s.process(":MEAS:VRMS? DISP,DC,CHAN1")) == pytest.approx(0.707, abs=0.03)
    assert float(s.process(":MEAS:VAV? DISP,CHAN1")) == pytest.approx(0, abs=0.03)


def test_ac_coupling_removes_the_mean():
    s = scope(signals={1: Signal("sine", 1e3, 2.0, offset=3.0)})
    s.process(":CHAN1:COUP AC;:TIM:SCAL 5e-4")
    assert s.process(":CHAN1:COUP?") == "AC"
    assert float(s.process(":MEAS:VAV?")) == pytest.approx(0, abs=0.04)
    s.process(":CHAN1:COUP GND")
    assert float(s.process(":MEAS:VPP?")) == 0


def test_noise_is_visible_and_reproducible():
    def run():
        s = Oscilloscope(noise=0.05, seed=7, signals={1: Signal(offset=0.0)})
        s.process(":STOP")
        return parse_block(s.process(":WAV:DATA?"))
    first = run()
    assert len(set(first)) > 3
    assert first == run()


# --- trigger -----------------------------------------------------------------------------------


def test_slope_selects_the_edge():
    s = scope(signals={1: Signal("sine", 1e3, 2.0)})
    s.process(":TIM:SCAL 2e-4;:TRIG:SLOP NEG")
    assert s.process(":TRIG:SLOP?") == "NEG"
    volts = waveform(s)
    assert volts[510] < volts[490]


def test_level_picks_the_point_on_the_wave():
    s = scope(signals={1: Signal("sine", 1e3, 2.0)})
    s.process(":TIM:SCAL 2e-4;:TRIG:LEV 0.5")
    assert float(s.process(":TRIG:LEV?")) == 0.5
    assert waveform(s)[500] == pytest.approx(0.5, abs=0.04)


def test_level_limits():
    s = scope()
    s.process(":TRIG:LEV 6.1")  # 0.75 x 8 divisions x 1 V/div
    assert error(s) == "-222"


def test_trigger_source():
    s = scope(signals={1: Signal(offset=1.0), 2: Signal("sine", 1e3, 2.0)})
    s.process(":TRIG:SOUR CHAN2;:CHAN2:DISP ON;:TRIG:SWE NORM")
    assert s.process(":TRIG:SOUR?") == "CHAN2"
    s.process(":SING")
    assert s.process("*OPC?") == "1"
    assert s.process(":TER?") == "1"
    assert s.process(":TER?") == "0"


def test_channels_keep_their_phase():
    s = scope(signals={1: Signal("sine", 1e3, 2.0), 2: Signal("sine", 1e3, 2.0, phase=90)})
    s.process(":CHAN2:DISP ON;:TIM:SCAL 2e-4;:STOP")
    one = waveform(s)
    s.process(":WAV:SOUR CHAN2")
    two = waveform(s)
    assert one[500] == pytest.approx(0, abs=0.04)
    assert two[500] == pytest.approx(1, abs=0.04)  # a quarter period ahead


def test_auto_sweep_runs_without_a_trigger():
    s = scope(signals={1: Signal(offset=1.0)})  # DC never crosses a level
    assert len(parse_block(s.process(":WAV:DATA?"))) == 1000
    s.process(":TRIG:LEV 0")
    assert float(s.process(":MEAS:VAV?")) == pytest.approx(1.0)


def test_normal_sweep_keeps_the_old_capture():
    s = scope(signals={1: Signal("sine", 1e3, 2.0)})
    s.process(":TIM:SCAL 2e-4;:TRIG:SWE NORM")
    assert float(s.process(":MEAS:VPP?")) == pytest.approx(2.0, abs=0.07)
    s.inject("signal", channel=1, shape="sine", frequency=1e3, vpp=0.5, offset=3.0)  # no longer through 0 V
    assert float(s.process(":MEAS:VPP?")) == pytest.approx(2.0, abs=0.07)


def test_normal_sweep_without_any_capture():
    s = scope(signals={1: Signal(offset=1.0)})
    s.process(":TRIG:SWE NORM")
    assert s.process(":WAV:DATA?") is None
    assert error(s) == "-230"
    assert float(s.process(":MEAS:VPP?")) == NO_VALUE


def test_single_waits_for_its_trigger():
    s = scope(signals={1: Signal("sine", 1e3, 1.0, offset=5.0)})  # never through 0 V
    s.process(":TRIG:SWE NORM;:SING")
    assert s.process("*OPC?") is None  # the client times out, as on a real scope
    assert s.process(":OPER:COND?") == "8"
    s.process(":TRIG:LEV 5")  # now it fires
    assert s.process("*OPC?") == "1"
    assert s.process(":OPER:COND?") == "0"


def test_force_ends_the_wait():
    s = scope(signals={1: Signal(offset=1.0)})
    s.process(":TRIG:SWE NORM;:SING")
    assert s.process("*OPC?") is None
    s.process(":TRIG:FORC")
    assert s.process("*OPC?") == "1"
    assert float(s.process(":MEAS:VAV?")) == pytest.approx(1.0)


def test_digitize_stops_the_scope():
    s = scope()
    s.process(":CHAN2:DISP ON;:DIG CHAN1")
    assert s.process(":OPER:COND?") == "0"
    assert s.process(":CHAN2:DISP?") == "0"
    assert s.process(":DIG WAT") is None
    assert error(s) == "-224"


def test_digitize_in_auto_does_not_wait():
    s = scope(signals={1: Signal(offset=1.0)})
    s.process(":DIG")
    assert s.process("*OPC?") == "1"


def test_stopped_capture_keeps_its_settings():
    s = scope()
    s.process(":TIM:SCAL 2e-4;:STOP")
    before = s.process(":WAV:PRE?")
    data = s.process(":WAV:DATA?")
    s.process(":CHAN1:SCAL 0.1;:TIM:SCAL 1e-6")
    assert s.process(":WAV:PRE?") == before
    assert s.process(":WAV:DATA?") == data
    s.process(":RUN")
    assert s.process(":WAV:PRE?") != before


def test_autoscale():
    s = scope(signals={1: Signal(), 2: Signal("sine", 5e3, 10.0, offset=2.0)})
    s.process(":AUT")
    assert s.process(":CHAN2:DISP?") == "1"
    assert s.process(":TRIG:SOUR?") == "CHAN2"
    assert float(s.process(":TRIG:LEV?")) == 2.0
    assert float(s.process(":CHAN2:OFFS?")) == 2.0
    assert float(s.process(":MEAS:VPP? CHAN2")) == pytest.approx(10.0, rel=0.02)
    assert float(s.process(":MEAS:FREQ? CHAN2")) == pytest.approx(5e3, rel=0.01)


def test_reset():
    s = scope()
    s.process(":CHAN1:SCAL 0.1;:TIM:SCAL 1e-6;:STOP;:WAV:FORM WORD;*RST")
    assert float(s.process(":CHAN1:SCAL?")) == 1.0
    assert s.process(":WAV:FORM?") == "BYTE"
    assert s.process(":OPER:COND?") == "8"


def test_settings_and_errors():
    s = scope()
    assert s.process(":CHAN1:SCAL 500 mV;:CHAN1:SCAL?") == "+5.00000000E-01"
    assert s.process(":CHAN1:RANG 8;:CHAN1:RANG?") == "+8.00000000E+00"
    assert s.process(":CHAN1:SCAL?") == "+1.00000000E+00"
    assert s.process(":TIM:RANG 1e-3;:TIM:SCAL?") == "+1.00000000E-04"
    assert s.process(":TIM:POS 1e-4;:TIM:POS?") == "+1.00000000E-04"
    assert s.process(":TIM:RANG?") == "+1.00000000E-03"
    assert s.process(":WAV:XOR?") is not None
    s.process(":CHAN1:SCAL 100")
    assert error(s) == "-222"
    s.process(":CHAN9:SCAL 1")
    assert error(s) == "-114"
    s.process(":WAV:SOUR CHAN9")
    assert error(s) == "-224"
    s.process(":WAV:FORM FLOAT")
    assert error(s) == "-224"
    s.process(":TIM:SCAL 1000")
    assert error(s) == "-222"


def test_probe_scales_the_limits():
    s = scope()
    s.process(":CHAN1:PROB 10")
    assert float(s.process(":CHAN1:PROB?")) == 10
    assert float(s.process(":CHAN1:SCAL?")) == 10.0
    s.process(":CHAN1:SCAL 50")
    assert float(s.process(":CHAN1:SCAL?")) == 50.0
    s.process(":CHAN1:PROB 3")
    assert error(s) == "-222"


def test_two_channel_scope():
    s = Oscilloscope(channels=2, noise=0, seed=1)
    s.process(":CHAN3:SCAL 1")
    assert error(s) == "-114"


# --- faults ----------------------------------------------------------------------------------------


def test_fault_no_trigger():
    s = scope()
    s.process(":TRIG:SWE NORM")
    s.inject("no_trigger")
    s.process(":SING")
    assert s.process("*OPC?") is None
    s.clear_faults()
    assert s.process("*OPC?") == "1"


def test_fault_signal():
    s = scope()
    s.process(":TIM:SCAL 5e-4")
    s.inject("signal", channel=1, shape="square", frequency="2000", vpp="1", offset=0)  # text, as from the CLI
    assert s.faults["signal"] == {"channels": "1"}
    assert float(s.process(":MEAS:FREQ?")) == pytest.approx(2000, rel=0.01)
    s.clear_faults()
    assert float(s.process(":MEAS:FREQ?")) == pytest.approx(1000, rel=0.01)


def test_fault_signal_bad_input():
    s = scope()
    for params in ({"channel": 9}, {"channel": 1, "shape": "wobble"}, {"shape": "sine"},
                   {"channel": 1, "duty": 2}):
        with pytest.raises(ValueError):
            s.inject("signal", **params)


def test_fault_offset_and_noise():
    s = scope(signals={1: Signal(offset=1.0)})
    s.inject("offset", value=0.5)
    assert float(s.process(":MEAS:VAV?")) == pytest.approx(1.5, abs=0.04)
    s.clear_faults()
    assert float(s.process(":MEAS:VPP?")) == 0
    s.inject("noise", sigma=0.2)
    assert float(s.process(":MEAS:VPP?")) > 0.5


def test_other_instruments_refuse_scope_faults():
    from scpi_sim import PowerSupply, Multimeter
    with pytest.raises(ValueError, match="not supported"):
        PowerSupply().inject("no_trigger")
    with pytest.raises(ValueError, match="not supported"):
        Multimeter().inject("signal", channel=1)


# --- on the wire ------------------------------------------------------------------------------------


def read_block(stream):
    assert stream.read(1) == b"#"
    digits = int(stream.read(1))
    length = int(stream.read(digits))
    data = stream.read(length)
    assert stream.read(1) == b"\n"
    return data


def test_binary_data_on_the_raw_socket():
    # a sawtooth over all codes: the block contains newlines, a line reader would break
    with InstrumentServer(scope(signals={1: Signal("sawtooth", 1e3, 8.0)}), port=0) as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock, sock.makefile("rwb") as f:
            f.write(b":TIM:SCAL 2e-4;:WAV:POIN 1000\n:WAV:DATA?\n")
            f.flush()
            data = read_block(f)
            assert len(data) == 1000 and b"\n" in data
            f.write(b"*IDN?\n")
            f.flush()
            assert f.readline().startswith(b"scpi-sim,SCOPE")


def test_pyvisa_reads_the_block_over_all_transports():
    pyvisa = pytest.importorskip("pyvisa")
    from scpi_sim import HislipServer, Vxi11Server
    s = scope(signals={1: Signal("sine", 1e3, 2.0)})
    with InstrumentServer(s, port=0) as raw, HislipServer(s, port=0) as hislip, Vxi11Server(s, port=0) as vxi11:
        rm = pyvisa.ResourceManager("@py")
        resources = (f"TCPIP0::127.0.0.1::{raw.port}::SOCKET",
                     f"TCPIP0::127.0.0.1::hislip0,{hislip.port}::INSTR",
                     f"TCPIP0::127.0.0.1,{vxi11.port}::inst0::INSTR")
        for resource in resources:
            inst = rm.open_resource(resource, timeout=5000, read_termination=None)
            if resource.endswith("SOCKET"):
                inst.read_termination, inst.write_termination = "\n", "\n"
            inst.write(":TIM:SCAL 1e-3;:STOP;:WAV:POIN:MODE RAW;:WAV:POIN 100000")
            values = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
            assert len(values) == 100_000
            assert max(values) == 160 and min(values) == 96  # +-1 V at 32 steps per volt around 128
            inst.write(":RUN")
            inst.close()
        rm.close()


# --- bench ----------------------------------------------------------------------------------------------


def test_bench_channel_shows_the_net():
    bench = Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0}, "scope": {"type": "scope", "port": 0, "noise": 0}},
        "loads": {"rail": {"type": "resistor", "ohms": 24}},
        "wiring": ["psu.ch1 -> rail", "scope.ch1 -> rail"],
    })
    psu, s = bench.instruments["psu"], bench.instruments["scope"]
    assert float(s.process(":MEAS:VAV?")) == pytest.approx(0, abs=0.04)
    psu.process("VOLT 3;CURR 1;:OUTP ON")
    assert float(s.process(":MEAS:VAV?")) == pytest.approx(3, abs=0.04)
    s.process(":CHAN1:SCAL 2;:CHAN1:OFFS 3;:TRIG:SWE AUTO")
    assert float(s.process(":MEAS:VMAX?")) == pytest.approx(3, abs=0.1)


def test_bench_signals_option():
    bench = Bench.from_dict({"instruments": {"scope": {
        "type": "scope", "port": 0, "noise": 0, "seed": 1, "channels": 2,
        "signals": {"ch1": {"shape": "square", "frequency": 2000, "vpp": 2}}}}})
    s = bench.instruments["scope"]
    s.process(":TIM:SCAL 5e-4")
    assert float(s.process(":MEAS:FREQ?")) == pytest.approx(2000, rel=0.01)


@pytest.mark.parametrize("instrument, wiring, message", [
    ({"type": "scope", "port": 0}, ["scope.ch9 -> a"], "unknown terminal"),
    ({"type": "scope", "port": 0}, ["scope.input -> a"], "unknown terminal"),
    ({"type": "scope", "port": 0, "signals": "sine"}, [], "signals"),
    ({"type": "scope", "port": 0, "signals": {7: {"shape": "sine"}}}, [], "channel 7"),
    ({"type": "scope", "port": 0, "channels": 3}, [], "2 or 4"),
    ({"type": "scope", "port": 0, "noise": -1}, [], "noise"),
])
def test_bench_errors(instrument, wiring, message):
    with pytest.raises(BenchError, match=message):
        Bench.from_dict({"instruments": {"scope": instrument}, "wiring": wiring})


def test_bench_profile():
    bench = Bench.from_dict({"instruments": {"scope": {"profile": "rigol-ds1054z", "port": 0, "noise": 0,
                                                       "serial": "X", "signals": {1: {"shape": "sine", "vpp": 2}}}}})
    assert bench.instruments["scope"].process("*IDN?").split(",")[2] == "X"
    with pytest.raises(BenchError, match="does not match"):
        Bench.from_dict({"instruments": {"s": {"type": "dmm", "profile": "rigol-ds1054z", "port": 0}}})
