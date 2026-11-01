"""The oscilloscope profiles: each one read back with the formula from its own manual."""

import struct

import pytest

from scpi_sim import Signal
from scpi_sim.profiles import PROFILES, get_profile

SINE = {1: Signal("sine", 1e3, 2.0)}
SCOPES = [name for name, cls in PROFILES.items() if cls.kind == "scope"]


def make(name, signals=None):
    return get_profile(name)(signals=signals or SINE, noise=0, seed=1)


def parse_block(raw):
    assert raw[:1] == b"#"
    digits = int(raw[1:2])
    length = int(raw[2:2 + digits])
    data = raw[2 + digits:2 + digits + length]
    assert len(data) == length
    return data


def test_five_scopes():
    assert set(SCOPES) == {"keysight-dsox2024a", "rigol-ds1054z", "tektronix-tbs2104b", "rs-rtb2004",
                           "siglent-sds1104x-e"}


@pytest.mark.parametrize("command, name", [
    (":WAV:DATA?", "tektronix-tbs2104b"), (":WAV:DATA?", "rs-rtb2004"), (":WAV:DATA?", "siglent-sds1104x-e"),
    ("CURV?", "rigol-ds1054z"), ("CURV?", "keysight-dsox2024a"), ("CHAN1:DATA?", "rigol-ds1054z"),
    ("C1:WF? DAT2", "keysight-dsox2024a"), ("C1:VDIV?", "rs-rtb2004"), (":MEAS:ITEM? VPP", "keysight-dsox2024a"),
    (":TIM:SCAL?", "tektronix-tbs2104b"),
])
def test_dialects_do_not_mix(command, name):
    scope = make(name)
    assert scope.process(command) is None
    assert scope.process("*ESR?") == str(128 | 32)  # power-on and command error


@pytest.mark.parametrize("name", SCOPES)
def test_faults_work_on_every_profile(name):
    scope = make(name)
    scope.inject("no_trigger")
    scope.clear_faults()
    scope.inject("signal", channel=2, shape="sine")
    assert scope.faults["signal"] == {"channels": "2"}
    assert scope.process("*OPC?") == "1"


def test_measure_value_uses_each_channel():
    for name in SCOPES:
        scope = make(name)
        assert scope.measure_value(1, "vpp") < 9e37
        assert scope.measure_value(2, "vpp") == 9.9e37  # channel 2 is off


# --- Rigol DS1054Z --------------------------------------------------------------------------


def rigol_volts(scope):
    """DS1000Z Programming Guide, :WAVeform:DATA?: (byte - YORigin - YREFerence) x YINCrement."""
    raw = parse_block(scope.process(":WAV:DATA?"))
    yinc = float(scope.process(":WAV:YINC?"))
    yorigin, yref = int(scope.process(":WAV:YOR?")), int(scope.process(":WAV:YREF?"))
    return [(byte - yorigin - yref) * yinc for byte in raw]


def test_rigol_identity_and_defaults():
    scope = make("rigol-ds1054z")
    assert scope.process("*IDN?") == "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA000000001,00.04.03.SP2"
    assert scope.process(":CHAN1:SCAL?") == "1.000000e+00"
    assert scope.process(":CHAN1:PROB?") == "1.000000e+01"
    assert scope.process(":TIM:SCAL?") == "1.000000e-06"
    assert scope.process(":WAV:MODE?") == "NORM" and scope.process(":WAV:FORM?") == "BYTE"
    assert scope.process(":WAV:STAR?") == "1" and scope.process(":WAV:STOP?") == "1200"
    assert scope.process(":TRIG:SWE?") == "AUTO" and scope.process(":SYST:GAM?") == "12"
    assert scope.process(":ACQ:MDEP?") == "AUTO" and scope.process(":TRIG:MODE?") == "EDGE"
    assert scope.process(":TIM:MODE?") == "MAIN" and scope.process(":ACQ:TYPE?") == "NORM"


def test_rigol_guide_formula():
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.0002;:CHAN1:SCAL 0.5")
    assert scope.process(":TIM:SCAL?") == "2.000000e-04"
    raw = scope.process(":WAV:DATA?")
    assert raw[:11] == b"#9000001200"  # the TMC header: #9 and nine digits
    volts = rigol_volts(scope)
    assert len(volts) == 1200
    assert max(volts) == pytest.approx(1.0, abs=0.03) and min(volts) == pytest.approx(-1.0, abs=0.03)
    assert float(scope.process(":WAV:YINC?")) == pytest.approx(0.5 / 25)  # guide: VerticalScale/25
    assert float(scope.process(":WAV:XINC?")) == pytest.approx(2e-6)  # guide: TimeScale/100


def test_rigol_offset_moves_the_trace_up():
    scope = make("rigol-ds1054z", {1: Signal("sine", 1e3, 2.0, offset=3.0)})
    scope.process(":CHAN1:OFFS -3;:TRIG:EDG:LEV 3;:TIM:SCAL 0.0002;:CHAN1:SCAL 0.5")
    assert scope.process(":WAV:YOR?") == str(round(-3 / (0.5 / 25)))
    volts = rigol_volts(scope)
    assert max(volts) == pytest.approx(4.0, abs=0.03) and min(volts) == pytest.approx(2.0, abs=0.03)
    assert float(scope.process(":MEAS:ITEM? VAMP,CHAN1")) == pytest.approx(2.0, abs=0.06)


def test_rigol_preamble():
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.0002")
    fields = scope.process(":WAV:PRE?").split(",")
    assert fields[:4] == ["0", "0", "1200", "1"] and fields[6] == "0" and fields[9] == "127"
    assert float(fields[4]) == pytest.approx(2e-6) and float(fields[5]) == pytest.approx(-1.2e-3)
    scope.process(":WAV:FORM WORD;:WAV:MODE RAW")
    assert scope.process(":WAV:PRE?").startswith("1,2,")
    scope.process(":WAV:FORM ASC")
    assert scope.process(":WAV:PRE?").startswith("2,2,")


def test_rigol_scales_are_1_2_5():
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.00023;:CHAN1:SCAL 0.7")
    assert scope.process(":TIM:SCAL?") == "2.000000e-04"
    assert scope.process(":CHAN1:SCAL?") == "5.000000e-01"
    scope.process(":CHAN1:SCAL 0.001")  # below 10 mV/div with the default 10X probe
    assert scope.process(":SYST:ERR?").startswith("-222")
    scope.process(":CHAN1:PROB 1;:CHAN1:SCAL 0.001")
    assert scope.process(":CHAN1:SCAL?") == "1.000000e-03"


def test_rigol_raw_needs_stop():
    scope = make("rigol-ds1054z")
    scope.process(":WAV:MODE RAW")
    assert scope.process(":WAV:DATA?") is None
    assert scope.process(":SYST:ERR?").startswith("-221")


def test_rigol_guide_example_1():
    """Example 1 in the guide: 120 kpts in one read."""
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.001;:ACQ:MDEP 120000")
    scope.process(":STOP")
    scope.process(":WAV:SOUR CHAN1")
    scope.process(":WAV:MODE RAW")
    scope.process(":WAV:FORM BYTE")
    scope.process(":WAV:STAR 1")
    scope.process(":WAV:STOP 120000")
    assert len(parse_block(scope.process(":WAV:DATA?"))) == 120_000
    assert float(scope.process(":ACQ:SRAT?")) == pytest.approx(1e7)  # depth = rate x 12 divisions x scale


def test_rigol_guide_example_2():
    """Example 2 in the guide: 300 kpts as WORD in three batches, continuous."""
    scope = make("rigol-ds1054z", {1: Signal("sine", 1e3, 2.0)})
    scope.process(":TIM:SCAL 0.001;:CHAN1:SCAL 0.5;:ACQ:MDEP 1200000;:STOP")
    scope.process(":WAV:MODE RAW;:WAV:FORM WORD")
    scope.process(":WAV:STAR 1;:WAV:STOP 125001")
    assert scope.process(":WAV:DATA?") is None  # one point over the limit
    assert scope.process(":SYST:ERR?").startswith("-222")
    parts = []
    for start, stop in ((1, 125000), (125001, 250000), (250001, 300000)):
        scope.process(f":WAV:STAR {start};:WAV:STOP {stop}")
        parts.append(parse_block(scope.process(":WAV:DATA?")))
    assert [len(part) for part in parts] == [250000, 250000, 100000]
    assert all(part[1::2] == bytes(len(part) // 2) for part in parts)  # the upper 8 bits are 0
    whole = b"".join(parts)
    scope.process(":WAV:STAR 124001;:WAV:STOP 126000")
    assert parse_block(scope.process(":WAV:DATA?")) == whole[248000:252000]  # the same memory


def test_rigol_raw_without_stop_point_is_1200():
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.001;:ACQ:MDEP 120000;:STOP;:WAV:MODE RAW")
    assert len(parse_block(scope.process(":WAV:DATA?"))) == 1200


def test_rigol_mdepth_depends_on_channels():
    scope = make("rigol-ds1054z")
    scope.process(":ACQ:MDEP 24000000")
    assert scope.process(":ACQ:MDEP?") == "24000000"
    scope.process(":CHAN2:DISP ON;:ACQ:MDEP 24000000")
    assert scope.process(":SYST:ERR?").startswith("-224")
    scope.process(":ACQ:MDEP 6000;:ACQ:MDEP?")
    assert scope.process(":ACQ:MDEP?") == "6000"


def test_rigol_measure_items():
    scope = make("rigol-ds1054z", {1: Signal("square", 1e3, 3.0, offset=1.5, duty=0.25), 2: Signal("sine", 2e3, 1.0)})
    scope.process(":TIM:SCAL 0.0005;:CHAN2:DISP ON;:TRIG:EDG:LEV 1.5")
    item = lambda text: float(scope.process(f":MEAS:ITEM? {text}"))
    assert item("VPP,CHAN1") == pytest.approx(3.0, abs=0.1)
    assert item("FREQ,CHAN1") == pytest.approx(1e3, rel=0.01)
    assert item("PER,CHAN1") == pytest.approx(1e-3, rel=0.01)
    assert item("PWID,CHAN1") == pytest.approx(250e-6, rel=0.02)
    assert item("PDUT,CHAN1") == pytest.approx(0.25, rel=0.02)
    assert item("NDUT,CHAN1") == pytest.approx(0.75, rel=0.02)
    assert item("VTOP,CHAN1") == pytest.approx(3.0, abs=0.1)
    assert item("VBAS,CHAN1") == pytest.approx(0.0, abs=0.1)
    assert item("OVER,CHAN1") == pytest.approx(0, abs=0.03)  # a ratio, not percent
    assert item("FREQ,CHAN2") == pytest.approx(2e3, rel=0.01)
    scope.process(":MEAS:SOUR CHAN2")
    assert scope.process(":MEAS:SOUR?") == "CHAN2"
    assert item("VPP") == pytest.approx(1.0, abs=0.1)
    scope.process(":MEAS:ITEM VPP,CHAN1")  # enabling it is accepted
    assert scope.process(":SYST:ERR?").startswith("0")
    scope.process(":MEAS:ITEM? NONSENSE")
    assert scope.process(":SYST:ERR?").startswith("-224")


def test_rigol_no_result_is_9_9e37():
    scope = make("rigol-ds1054z")
    assert float(scope.process(":MEAS:ITEM? FREQ,CHAN1")) == 9.9e37  # 12 us on screen, 1 ms period


def test_rigol_trigger_status():
    scope = make("rigol-ds1054z", {1: Signal(offset=1.0)})
    assert scope.process(":TRIG:STAT?") == "AUTO"
    scope.process(":TRIG:SWE NORM")
    assert scope.process(":TRIG:SWE?") == "NORM"
    assert scope.process(":TRIG:STAT?") == "WAIT"
    scope.process(":SING")
    assert scope.process(":TRIG:SWE?") == "SING"
    assert scope.process(":TRIG:STAT?") == "WAIT"
    scope.process(":TFOR")
    assert scope.process(":TRIG:STAT?") == "STOP"
    scope.process(":RUN")
    assert scope.process(":TRIG:SWE?") == "NORM"
    scope = make("rigol-ds1054z")
    assert scope.process(":TRIG:STAT?") == "TD"
    scope.process(":STOP")
    assert scope.process(":TRIG:STAT?") == "STOP"


def test_rigol_trigger_settings():
    scope = make("rigol-ds1054z")
    assert scope.process(":TRIG:EDG:SOUR CHAN2;:TRIG:EDG:SOUR?") == "CHAN2"
    assert scope.process(":TRIG:EDG:SLOP NEG;:TRIG:EDG:SLOP?") == "NEG"
    assert scope.process(":TRIG:EDG:SLOP RFAL;:TRIG:EDG:SLOP?") == "RFAL"
    assert scope.process(":TRIG:EDG:LEV 0.16;:TRIG:EDG:LEV?") == "1.600000e-01"
    scope.process(":TRIG:EDG:LEV 6")  # outside +-5 x scale
    assert scope.process(":SYST:ERR?").startswith("-222")
    scope.process(":TRIG:MODE PULS")
    assert scope.process(":SYST:ERR?").startswith("-221")


def test_rigol_ascii_and_channel_commands():
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.0002;:WAV:FORM ASC;:WAV:STOP 10")
    values = scope.process(":WAV:DATA?").split(",")
    assert len(values) == 10 and "e" in values[0]
    assert scope.process(":CHAN1:COUP AC;:CHAN1:COUP?") == "AC"
    assert scope.process(":CHAN1:RANG 8;:CHAN1:SCAL?") == "1.000000e+00"
    assert scope.process(":CHAN1:RANG?") == "8.000000e+00"
    assert scope.process(":TIM:MAIN:OFFS 0.001;:TIM:OFFS?") == "1.000000e-03"
    assert scope.process(":CHAN2:OFFS 1;:CHAN2:OFFS?") == "1.000000e+00"
    scope.process(":CHAN2:OFFS 30")  # +-20 V below 5 V/div with a 10X probe
    assert scope.process(":SYST:ERR?").startswith("-222")
    scope.process(":WAV:SOUR D0")
    assert scope.process(":SYST:ERR?").startswith("-224")


def test_rigol_wave_settings_and_autoscale():
    scope = make("rigol-ds1054z", {1: Signal(), 2: Signal(), 3: Signal("sine", 2e3, 4.0)})
    scope.process(":AUT")
    assert scope.process(":CHAN3:DISP?") == "1" and scope.process(":TRIG:EDG:SOUR?") == "CHAN3"
    scope.process(":WAV:SOUR CHAN3")
    assert scope.process(":WAV:SOUR?") == "CHAN3"
    assert float(scope.process(":MEAS:ITEM? VPP,CHAN3")) == pytest.approx(4.0, abs=0.2)


# --- Keysight DSO-X 2024A -----------------------------------------------------------------------


def test_keysight_identity():
    scope = make("keysight-dsox2024a")
    assert scope.process("*IDN?") == "AGILENT TECHNOLOGIES,DSO-X 2024A,MY54100001,02.43.00"


def test_keysight_guide_flow():
    scope = make("keysight-dsox2024a")
    scope.process(":TIM:SCAL 2e-4;:TRIG:SOUR CHAN1;:TRIG:LEV 0")
    scope.process(":WAV:POIN:MODE RAW;:WAV:POIN 10000;:WAV:FORM BYTE;:WAV:SOUR CHAN1")
    scope.process(":DIG CHAN1")
    assert scope.process("*OPC?") == "1"
    fields = scope.process(":WAV:PRE?").split(",")
    assert fields[2] == "+10000"
    raw = parse_block(scope.process(":WAV:DATA?"))
    yinc, yorg, yref = float(fields[7]), float(fields[8]), int(fields[9])
    volts = [(b - yref) * yinc + yorg for b in raw]
    assert max(volts) == pytest.approx(1.0, abs=0.04)
    assert float(scope.process(":MEAS:FREQ? CHAN1")) == pytest.approx(1e3, rel=1e-2)
    assert float(scope.process(":MEAS:VPP? CHAN1")) == pytest.approx(2.0, abs=0.07)


def test_keysight_raw_limit():
    scope = make("keysight-dsox2024a")
    scope.process(":STOP;:WAV:POIN:MODE RAW;:WAV:POIN 8000000")
    assert int(scope.process(":WAV:POIN?")) <= 100_000


def test_keysight_error_queue_is_30_deep():
    assert make("keysight-dsox2024a").ERROR_QUEUE_SIZE == 30


# --- Tektronix TBS2104B -------------------------------------------------------------------------


def tek_volts(scope, raw, width=1, signed=True):
    """TBS2000B manual: value = ((curve_in_dl - YOFf) x YMUlt) + YZEro."""
    scope.process("HEADER OFF")
    ymult, yoff, yzero = (float(scope.process(f"WFMO:{name}?")) for name in ("YMU", "YOF", "YZE"))
    scope.process("HEADER ON")
    if width == 1:
        codes = struct.unpack(f">{len(raw)}{'b' if signed else 'B'}", raw)
    else:
        codes = struct.unpack(f">{len(raw) // 2}{'h' if signed else 'H'}", raw)
    return [(code - yoff) * ymult + yzero for code in codes]


def tek_curve(scope):
    answer = scope.process("CURV?")
    assert answer.startswith(b":CURVE #")
    return parse_block(answer[len(b":CURVE "):])


def test_tek_identity_and_headers():
    scope = make("tektronix-tbs2104b")
    assert scope.process("*IDN?") == "TEKTRONIX,TBS2104B,C010001,CF:91.1CT FV:v1.0.0"  # * commands: no header
    assert scope.process("CH1:SCA?") == ":CH1:SCALE 1.0000E+00"
    assert scope.process("DAT:SOU?") == ":DATA:SOURCE CH1"
    assert scope.process("HEAD?") == ":HEADER 1"
    scope.process("HEADER OFF")
    assert scope.process("CH1:SCA?") == "1.0000E+00"
    assert scope.process("HEAD?") == "0"
    scope.process("HEADER 1")
    assert scope.process("HOR:SCA?") == ":HORIZONTAL:MAIN:SCALE 4.0000E-06"


def test_tek_curve_formula_all_formats():
    scope = make("tektronix-tbs2104b")
    scope.process("HOR:SCA 2E-4;:ACQ:STATE STOP")
    for bn_fmt, width, signed in (("RI", 1, True), ("RP", 1, False), ("RI", 2, True), ("RP", 2, False)):
        scope.process(f"WFMO:BN_F {bn_fmt};:DAT:WID {width}")
        volts = tek_volts(scope, tek_curve(scope), width, signed)
        assert len(volts) == 2000
        assert max(volts) == pytest.approx(1.0, abs=0.05), (bn_fmt, width)
        assert min(volts) == pytest.approx(-1.0, abs=0.05), (bn_fmt, width)


def test_tek_position_and_offset():
    scope = make("tektronix-tbs2104b", {1: Signal("sine", 1e3, 2.0, offset=3.0)})
    scope.process("CH1:OFFS 3;:CH1:POS 1;:CH1:SCA 0.5;:TRIG:A:LEV 3;:HOR:SCA 2E-4;:ACQ:STATE STOP")
    volts = tek_volts(scope, tek_curve(scope))
    assert max(volts) == pytest.approx(4.0, abs=0.03) and min(volts) == pytest.approx(2.0, abs=0.03)
    assert scope.process("WFMO:YOF?") == ":WFMOUTPRE:YOFF 2.5000E+01"  # one division up
    assert scope.process("WFMO:YZE?") == ":WFMOUTPRE:YZERO 3.0000E+00"


def test_tek_wfmoutpre():
    scope = make("tektronix-tbs2104b")
    scope.process("HOR:SCA 2E-4")
    answer = scope.process("WFMO?")
    assert answer.startswith(":WFMOUTPRE:BYT_NR 1;BIT_NR 8;ENCDG BINARY;BN_FMT RI;BYT_OR MSB;WFID ")
    assert 'NR_PT 2000;PT_FMT Y;XUNIT "s";XINCR 1.0000E-06;XZERO -1.0000E-03;PT_OFF 0;YUNIT "V"' in answer
    scope.process("HEADER OFF")
    assert scope.process("WFMO?").startswith("1;8;BINARY;RI;MSB;")
    scope.process("HEADER ON;:SEL:CH1 OFF")  # only the transmission parameters of a waveform that is off
    assert scope.process("WFMO?") == ":WFMOUTPRE:BYT_NR 1;BIT_NR 8;ENCDG BINARY;BN_FMT RI;BYT_OR MSB"


def test_tek_curve_of_channel_off():
    """The manual: returns nothing, generates events 2244 and 420."""
    scope = make("tektronix-tbs2104b")
    scope.process("DAT:SOU CH2")
    assert scope.process("CURV?") is None
    assert scope.process("EVQ?") == ":EVQTY 2"
    assert scope.process("EVENT?") == ":EVENT 2244"
    assert scope.process("EVM?") == ':EVMSG 420,"Query UNTERMINATED"'
    assert scope.process("EVQ?") == ":EVQTY 0"
    assert scope.process("EVM?") == ':EVMSG 0,"No events to report; queue empty"'
    assert scope.process("WFMO:YMU?") is None


def test_tek_events_not_syst_err():
    scope = make("tektronix-tbs2104b")
    assert scope.process("SYST:ERR?") is None
    assert scope.process("ALLEV?") == ':ALLEV 113,"Undefined header"'
    assert scope.process("ALLEV?") == ':ALLEV 0,"No events to report; queue empty"'


def test_tek_data_range_and_init():
    scope = make("tektronix-tbs2104b")
    scope.process("DAT:STAR 11;:DAT:STOP 20")
    assert len(tek_curve(scope)) == 10
    assert scope.process("WFMO:NR_P?") == ":WFMOUTPRE:NR_PT 10"
    assert scope.process("DAT:STAR?") == ":DATA:START 11" and scope.process("DAT:STOP?") == ":DATA:STOP 20"
    scope.process("HOR:RECO 20000;:DAT INIT")
    assert scope.process("HOR:RECO?") == ":HORIZONTAL:RECORDLENGTH 20000"
    assert scope.process("DAT:STOP?") == ":DATA:STOP 20000"
    scope.process("DAT:STAR 30000")
    assert scope.process("CURV?") is None
    assert scope.process("EVENT?") == ":EVENT 222"


def test_tek_ascii_curve():
    scope = make("tektronix-tbs2104b")
    scope.process("WFMO:ENC ASC;:DAT:STOP 5")
    answer = scope.process("CURV?")
    assert answer.startswith(":CURVE ") and len(answer.split(",")) == 5
    assert scope.process("WFMO:ENC?") == ":WFMOUTPRE:ENCDG ASCII"
    assert scope.process("WFMO:BYT_N?") == ":WFMOUTPRE:BYT_NR 1"
    assert scope.process("WFMO:BIT_N 16;:WFMO:BIT_N?") == ":WFMOUTPRE:BIT_NR 16"
    assert scope.process("DAT:WID?") == ":DATA:WIDTH 2"
    assert scope.process("WFMO:BYT_O LSB;:WFMO:BYT_O?") == ":WFMOUTPRE:BYT_OR LSB"


def test_tek_acquire_sequence_from_the_manual():
    scope = make("tektronix-tbs2104b", {1: Signal("sine", 1e3, 2.0)})
    scope.process("ACQ:STATE OFF")
    scope.process("SEL:CH1 ON")
    scope.process("HOR:RECO 2000")
    scope.process("ACQ:STOPA SEQ")
    scope.process("ACQ:STATE ON")
    assert scope.process("*OPC?") == "1"
    assert scope.process("ACQ:STATE?") == ":ACQUIRE:STATE 0"
    assert scope.process("ACQ:STOPA?") == ":ACQUIRE:STOPAFTER SEQUENCE"
    assert scope.process("ACQ:NUMAC?") == ":ACQUIRE:NUMACQ 1"
    assert scope.process("TRIG:STATE?") == ":TRIGGER:STATE SAVE"


def test_tek_trigger_states_and_levels():
    scope = make("tektronix-tbs2104b", {1: Signal(offset=1.0)})
    assert scope.process("TRIG:STATE?") == ":TRIGGER:STATE AUTO"
    assert scope.process("TRIG:A:MOD NORM;:TRIG:A:MOD?") == ":TRIGGER:A:MODE NORMAL"
    assert scope.process("TRIG:STATE?") == ":TRIGGER:STATE READY"
    assert scope.process("TRIG:A:LEV TTL;:TRIG:A:LEV?") == ":TRIGGER:A:LEVEL 1.4000E+00"
    assert scope.process("TRIG:A:LEV:CH1 ECL;:TRIG:A:LEV:CH1?") == ":TRIGGER:A:LEVEL:CH1 -1.3000E+00"
    assert scope.process("TRIG:A:EDGE:SOU CH2;:TRIG:A:EDGE:SOU?") == ":TRIGGER:A:EDGE:SOURCE CH2"
    assert scope.process("TRIG:A:EDGE:SLO FALL;:TRIG:A:EDGE:SLO?") == ":TRIGGER:A:EDGE:SLOPE FALL"
    scope = make("tektronix-tbs2104b")
    assert scope.process("TRIG:STATE?") == ":TRIGGER:STATE TRIGGER"
    scope = make("tektronix-tbs2104b", {1: Signal(offset=1.0)})
    scope.process("TRIG:A:MOD NORM;:ACQ:STOPA SEQ;:ACQ:STATE RUN")
    assert scope.process("*OPC?") is None
    scope.process("TRIG:FORC")
    assert scope.process("*OPC?") == "1"


def test_tek_measurements():
    scope = make("tektronix-tbs2104b", {1: Signal("square", 1e3, 3.0, offset=1.5, duty=0.25)})
    scope.process("HOR:SCA 5E-4;:TRIG:A:LEV 1.5")
    for kind, expected, tolerance in (("PK2", 3.0, 0.1), ("FREQ", 1e3, 10), ("PERI", 1e-3, 1e-5), ("PDU", 25, 1),
                                      ("NDU", 75, 1), ("PWI", 250e-6, 1e-5), ("HIGH", 3.0, 0.1), ("LOW", 0, 0.1),
                                      ("MAX", 3, 0.1), ("MINI", 0, 0.1), ("MEAN", 0.75, 0.1)):
        scope.process(f"MEASU:IMM:TYP {kind}")
        assert float(scope.process("MEASU:IMM:VAL?").split()[1]) == pytest.approx(expected, abs=tolerance), kind
    assert scope.process("MEASU:IMM:TYP FREQ;:MEASU:IMM:TYP?") == ":MEASUREMENT:IMMED:TYPE FREQUENCY"
    assert scope.process("MEASU:IMM:UNI?") == ':MEASUREMENT:IMMED:UNITS "Hz"'
    assert scope.process("MEASU:IMM:SOU1 CH2;:MEASU:IMM:SOU1?") == ":MEASUREMENT:IMMED:SOURCE1 CH2"
    assert scope.process("MEASU:IMM:VAL?") == ":MEASUREMENT:IMMED:VALUE 9.9000E+37"  # the manual's "no result"


def test_tek_settings():
    scope = make("tektronix-tbs2104b")
    assert scope.process("CH1:VOL 0.5;:CH1:VOL?") == ":CH1:SCALE 5.0000E-01"
    assert scope.process("HOR:SEC 1E-3;:HOR:SEC?") == ":HORIZONTAL:MAIN:SCALE 1.0000E-03"
    assert scope.process("HOR:POS 1E-3;:HOR:POS?") == ":HORIZONTAL:POSITION 1.0000E-03"
    assert scope.process("CH2:COUP AC;:CH2:COUP?") == ":CH2:COUPLING AC"
    assert scope.process("CH2:PRO 10;:CH2:PRO?") == ":CH2:PROBE 1.0000E+01"
    assert scope.process("SEL:CH2 1;:SEL:CH2?") == ":SELECT:CH2 1"
    assert float(scope.process("HOR:SAMPLER?").split()[1]) > 0


# --- Rohde & Schwarz RTB2004 ------------------------------------------------------------------------


def test_rs_identity_and_divisions():
    scope = make("rs-rtb2004")
    assert scope.process("*IDN?") == "Rohde&Schwarz,RTB2004,1333.1005K04/100001,02.400"
    assert scope.process("TIM:DIV?") == "12"
    assert scope.process("FORM?") == "ASC,0"
    assert scope.process("FORM:BORD?") == "MSBF"


def test_rs_ascii_default():
    scope = make("rs-rtb2004")
    scope.process("TIM:SCAL 2E-4")
    values = [float(v) for v in scope.process("CHAN1:DATA?").split(",")]
    assert len(values) == 10_000 and max(values) == pytest.approx(1.0, abs=0.05)


def test_rs_manual_uint_conversion():
    """Manual, 15.2.1.4: Yn = yOrigin + yIncrement x byte; center screen is byte value 127.5."""
    scope = make("rs-rtb2004", {1: Signal("sine", 1e3, 0.04)})
    scope.process("CHAN1:SCAL 5E-3;:TIM:SCAL 2E-4;:FORM UINT,8")
    assert scope.process("FORM?") == "UINT,8"
    assert float(scope.process("CHAN1:DATA:YOR?")) == pytest.approx(-127.5 * 2e-4)  # -2.55E-2 for 5 mV/div
    yinc = float(scope.process("CHAN1:DATA:YINC?"))
    assert yinc == pytest.approx(2e-4)
    assert scope.process("CHAN1:DATA:YRES?") == "8"
    raw = parse_block(scope.process("CHAN1:DATA?"))
    origin = float(scope.process("CHAN1:DATA:YOR?"))
    volts = [origin + yinc * byte for byte in raw]
    assert max(volts) == pytest.approx(0.02, abs=4e-4) and min(volts) == pytest.approx(-0.02, abs=4e-4)


def test_rs_uint16_and_32_and_real():
    scope = make("rs-rtb2004")
    scope.process("TIM:SCAL 2E-4;:ACQ:STAT STOP")
    scope.process("FORM UINT,16")
    assert float(scope.process("CHAN1:DATA:YINC?")) == pytest.approx(0.04 / 256)
    raw = parse_block(scope.process("CHAN1:DATA?"))
    assert len(raw) == 20_000
    origin, yinc = float(scope.process("CHAN1:DATA:YOR?")), float(scope.process("CHAN1:DATA:YINC?"))
    volts = [origin + yinc * v for v in struct.unpack(f">{len(raw) // 2}H", raw)]
    assert max(volts) == pytest.approx(1.0, abs=0.05)
    scope.process("FORM UINT,32")
    assert scope.process("CHAN1:DATA:YRES?") == "32"
    assert len(parse_block(scope.process("CHAN1:DATA?"))) == 40_000
    scope.process("FORM REAL,32")
    assert scope.process("FORM?") == "REAL,32"
    raw = parse_block(scope.process("CHAN1:DATA?"))
    assert max(struct.unpack(">10000f", raw)) == pytest.approx(1.0, abs=0.05)
    scope.process("FORM:BORD LSBF")
    assert max(struct.unpack("<10000f", parse_block(scope.process("CHAN1:DATA?")))) == pytest.approx(1.0, abs=0.05)
    scope.process("FORM FLOAT")
    assert scope.process("SYST:ERR?").startswith("-224")


def test_rs_header():
    """Manual table 15-1: XStart, XStop, record length, values per sample."""
    scope = make("rs-rtb2004")
    scope.process("TIM:SCAL 1E-4")
    start, stop, length, per_sample = scope.process("CHAN1:DATA:HEAD?").split(",")
    assert float(start) == pytest.approx(-6e-4) and float(stop) == pytest.approx(6e-4, rel=1e-3)
    assert length == "10000" and per_sample == "1"
    assert "E-00" in start  # the manual's -9.477E-008
    assert float(scope.process("CHAN1:DATA:XOR?")) == pytest.approx(-6e-4)
    assert float(scope.process("CHAN1:DATA:XINC?")) == pytest.approx(1.2e-3 / 10000)


def test_rs_points_only_when_stopped():
    scope = make("rs-rtb2004")
    scope.process("TIM:SCAL 1E-3;:CHAN1:DATA:POIN MAX")  # running: stays DEF
    assert scope.process("CHAN1:DATA:POIN?") == "10000"
    scope.process("STOP;:CHAN1:DATA:POIN MAX")
    assert int(scope.process("CHAN1:DATA:POIN?")) == 20_000_000
    scope.process("RUN")
    assert scope.process("CHAN1:DATA:POIN?") == "10000"  # running: DEF is used


def test_rs_acquisition_state():
    scope = make("rs-rtb2004", {1: Signal(offset=1.0)})
    assert scope.process("ACQ:STAT?") == "RUN"
    scope.process("TRIG:A:MODE NORM;:SING")
    assert scope.process("*OPC?") is None  # DC never triggers: the query waits
    scope = make("rs-rtb2004")
    scope.process("RUNS")
    assert scope.process("*OPC?") == "1"
    assert scope.process("ACQ:STAT?") == "COMP"
    scope.process("RUNC")
    assert scope.process("ACQ:STAT?") == "RUN"
    scope.process("ACQ:STAT STOP")
    assert scope.process("ACQ:STAT?") == "STOP"
    scope.process("ACQ:STAT RUN;:STOP")
    assert scope.process("ACQ:STAT?") == "STOP"


def test_rs_measurements():
    scope = make("rs-rtb2004", {1: Signal("square", 1e3, 3.0, offset=1.5, duty=0.25)})
    scope.process("TIM:SCAL 5E-4;:TRIG:A:LEV1 1.5;:MEAS1:SOUR CH1;:MEAS1:MAIN FREQ;:MEAS1 ON")
    assert scope.process("MEAS1?") == "1" and scope.process("MEAS1:MAIN?") == "FREQ"
    assert scope.process("MEAS1:SOUR?") == "CH1"
    assert float(scope.process("MEAS1:RES?")) == pytest.approx(1e3, rel=0.01)
    for kind, expected, tolerance in (("PEAK", 3, 0.1), ("PER", 1e-3, 1e-5), ("HIGH", 3, 0.1), ("LOW", 0, 0.1),
                                      ("AMPL", 3, 0.2), ("MEAN", 0.75, 0.1), ("PDCY", 25, 1), ("NDCY", 75, 1),
                                      ("PPW", 250e-6, 1e-5), ("NPW", 750e-6, 1e-5), ("UPE", 3, 0.1), ("LPE", 0, 0.1)):
        assert float(scope.process(f"MEAS1:RES? {kind}")) == pytest.approx(expected, abs=tolerance), kind
    assert scope.process("MEAS2:MAIN PEAK;:MEAS2:MAIN?") == "PEAK"
    scope.process("MEAS7:MAIN PEAK")
    assert scope.process("SYST:ERR?").startswith("-114")
    scope.process("MEAS1:SOUR CH2")
    assert float(scope.process("MEAS1:RES?")) == 9.91e37


def test_rs_settings():
    scope = make("rs-rtb2004")
    assert float(scope.process("CHAN2:SCAL 0.1;:CHAN2:SCAL?")) == pytest.approx(0.1)
    assert float(scope.process("CHAN2:RANG 8;:CHAN2:RANG?")) == pytest.approx(8)
    assert float(scope.process("CHAN2:POS 1;:CHAN2:POS?")) == 1
    assert float(scope.process("CHAN2:OFFS 1;:CHAN2:OFFS?")) == 1
    assert scope.process("CHAN2:COUP ACL;:CHAN2:COUP?") == "ACL"
    assert scope.process("CHAN2:COUP DCLimit;:CHAN2:COUP?") == "DCL"
    assert scope.process("CHAN2:STAT ON;:CHAN2:STAT?") == "1"
    assert float(scope.process("TIM:RANG 1.2E-3;:TIM:SCAL?")) == pytest.approx(1e-4)
    assert float(scope.process("TIM:RANG?")) == pytest.approx(1.2e-3)
    assert float(scope.process("TIM:POS 1E-4;:TIM:POS?")) == pytest.approx(1e-4)
    assert scope.process("TRIG:A:SOUR CH2;:TRIG:A:SOUR?") == "CH2"
    assert scope.process("TRIG:A:MODE NORM;:TRIG:A:MODE?") == "NORM"
    assert float(scope.process("TRIG:A:LEV2 1.5;:TRIG:A:LEV2:VAL?")) == 1.5
    assert scope.trigger_level == 1.5
    scope.process("TRIG:A:LEV3:VAL 2.5;:TRIG:A:SOUR CH3")
    assert scope.trigger_level == 2.5
    assert scope.process("TRIG:A:EDGE:SLOP EITH;:TRIG:A:EDGE:SLOP?") == "EITH"
    assert scope.process("ACQ:POIN:AUT ON;:ACQ:POIN:AUT?") == "1"
    assert scope.process("ACQ:POIN 100000;:ACQ:POIN:AUT?") == "0"
    assert float(scope.process("ACQ:SRAT?")) > 0
    assert scope.process("ACQ:POIN?") is not None


def test_rs_channel_off():
    scope = make("rs-rtb2004")
    assert scope.process("CHAN2:DATA?") is None
    assert scope.process("SYST:ERR?").startswith("-221")


# --- Siglent SDS1104X-E --------------------------------------------------------------------------------


def siglent_volts(scope, trace="C1"):
    """Programming guide, WF?: code x (vdiv / 25) - voffset; codes above 127 minus 256; data ends in 0A 0A."""
    answer = scope.process(f"{trace}:WF? DAT2")
    assert answer.startswith(f"{trace}:WF DAT2,#9".encode())
    length = int(answer[13:22])
    assert answer[22 + length:] == b"\n"  # the transport adds the second newline
    data = answer[22:22 + length]
    vdiv = float(scope.process(f"{trace}:VDIV?").split()[1].rstrip("V"))
    ofst = float(scope.process(f"{trace}:OFST?").split()[1].rstrip("V"))
    return [(b - 256 if b > 127 else b) * (vdiv / 25) - ofst for b in data]


def test_siglent_identity_and_answers():
    scope = make("siglent-sds1104x-e")
    assert scope.process("*IDN?") == "Siglent Technologies,SDS1104X-E,SDS1EBAX000001,8.2.6.1.37R9"
    assert scope.process("C1:VDIV 500mV;:C1:VDIV?") == "C1:VDIV 5.00E-01V"
    assert scope.process("C1:VOLT_DIV?") == "C1:VDIV 5.00E-01V"
    assert scope.process("TDIV 5NS;TDIV?") == "TDIV 5.00E-09S"
    assert scope.process("TIME_DIV?") == "TDIV 5.00E-09S"
    assert scope.process("TDIV 1US;SARA?") == "SARA 1.00E+09Sa/s"
    assert scope.process("C1:OFST -0.5V;:C1:OFST?") == "C1:OFST -5.00E-01V"
    assert scope.process("C1:ATTN 10;:C1:ATTN?") == "C1:ATTN 10"
    assert scope.process("C1:CPL A1M;:C1:CPL?") == "C1:CPL A1M"
    assert scope.process("C2:TRA ON;:C2:TRA?") == "C2:TRA ON"
    assert scope.process("C1:TRLV 1.5V;:C1:TRLV?") == "C1:TRLV 1.50E+00V"
    assert scope.process("C1:TRSL NEG;:C1:TRSL?") == "C1:TRSL NEG"
    assert scope.process("TRSE EDGE,SR,C2,HT,OFF;TRSE?") == "TRSE EDGE,SR,C2,HT,OFF"
    assert scope.process("TRMD NORM;TRMD?") == "TRMD NORM"
    assert scope.process("WFSU?") == "WFSU SP,0,NP,0,FP,0"
    assert scope.process("TRDL 1MS;TRDL?") == "TRDL 1.00E-03S"
    assert scope.process("MSIZ 7M;MSIZ?") == "MSIZ 7M"


def test_siglent_no_syst_err():
    scope = make("siglent-sds1104x-e")
    assert scope.process("SYST:ERR?") is None
    assert int(scope.process("*ESR?")) & 32


def test_siglent_guide_formula():
    scope = make("siglent-sds1104x-e", {1: Signal("sine", 1e3, 2.0, offset=0.5)})
    scope.process("C1:VDIV 500mV;:C1:OFST -0.5V;:C1:TRLV 0.5;:TDIV 200US")
    volts = siglent_volts(scope)
    assert len(volts) == 14_000
    assert max(volts) == pytest.approx(1.5, abs=0.03) and min(volts) == pytest.approx(-0.5, abs=0.03)


def test_siglent_time_formula():
    """Guide: time = -(timebase x grid / 2) + index / sample rate, 14 divisions."""
    scope = make("siglent-sds1104x-e")
    scope.process("TDIV 200US;:C1:VDIV 500mV")
    rate = float(scope.process("SARA?").split()[1].removesuffix("Sa/s"))
    assert rate == pytest.approx(14_000 / (14 * 200e-6))
    assert scope.process("SANU?") == "SANU 1.40E+04pts"
    volts = siglent_volts(scope)
    assert abs(volts[7000]) < 0.03  # index 7000: t = 0, the trigger on the rising zero crossing
    assert volts[7050] > 0


def test_siglent_wfsu():
    scope = make("siglent-sds1104x-e")
    scope.process("TDIV 200US;STOP")
    whole = siglent_volts(scope)
    scope.process("WFSU NP,100")
    assert scope.process("WFSU?") == "WFSU SP,0,NP,100,FP,0"
    assert siglent_volts(scope) == whole[:100]
    scope.process("WFSU FP,1000")
    assert siglent_volts(scope) == pytest.approx(whole[1000:1100], abs=0.03)
    scope.process("WFSU SP,4,NP,0,FP,0")
    assert siglent_volts(scope) == pytest.approx(whole[::4], abs=0.03)
    scope.process("WFSU SP,x")
    assert int(scope.process("*ESR?")) & 16  # execution error


def test_siglent_measurements():
    scope = make("siglent-sds1104x-e", {1: Signal("square", 1e3, 3.0, offset=1.5, duty=0.25)})
    scope.process("TDIV 500US;:C1:TRLV 1.5")
    value = lambda name: scope.process(f"C1:PAVA? {name}")
    assert value("FREQ").startswith("C1:PAVA FREQ,") and value("FREQ").endswith("Hz")
    assert float(value("FREQ").split(",")[1][:-2]) == pytest.approx(1e3, rel=0.01)
    assert value("PKPK").endswith("V") and value("PER").endswith("S") and value("DUTY").endswith("%")
    assert float(value("PKPK").split(",")[1][:-1]) == pytest.approx(3.0, abs=0.1)
    assert float(value("DUTY").split(",")[1][:-1]) == pytest.approx(25, abs=1)
    everything = scope.process("C1:PAVA? ALL")
    assert everything.startswith("C1:PAVA PKPK,") and ",FREQ," in everything
    assert scope.process("C1:PARAMETER_VALUE? MAX").startswith("C1:PAVA MAX,")
    assert value("NOPE") is None


def test_siglent_no_result_stars():
    scope = make("siglent-sds1104x-e")
    assert scope.process("C1:PAVA? FREQ") == "C1:PAVA FREQ,****"
    assert scope.process("C2:PAVA? PKPK") == "C2:PAVA PKPK,****"  # the trace is off


def test_siglent_acquisition():
    scope = make("siglent-sds1104x-e", {1: Signal(offset=1.0)})
    assert scope.process("SAST?") == "SAST Auto"
    scope.process("TRMD NORM")
    assert scope.process("SAST?") == "SAST Ready"
    scope = make("siglent-sds1104x-e")
    assert scope.process("INR?") == "INR 8193"  # trigger ready and a new acquisition
    assert scope.process("STOP;SAST?") == "SAST Stop"
    assert scope.process("INR?") == "INR 1"  # stopping takes the acquisition that was running
    assert scope.process("INR?") == "INR 0"
    scope.process("TRMD SINGLE")
    assert scope.process("SAST?") == "SAST Stop"
    assert scope.process("TRMD?") == "TRMD SINGLE"
    assert scope.process("INR?") == "INR 1"
    scope.process("ARM")
    assert scope.process("INR?") == "INR 1"
    assert scope.process("SAST?") == "SAST Stop"
    scope.process("TRMD AUTO")
    assert scope.process("SAST?") == "SAST Trig'd"
    scope.process("TRMD STOP")
    assert scope.process("SAST?") == "SAST Stop"


def test_siglent_interleaving():
    scope = make("siglent-sds1104x-e")
    scope.process("MSIZ 14M;TDIV 1MS")
    assert float(scope.process("SARA?").split()[1].removesuffix("Sa/s")) == pytest.approx(1e9)
    scope.process("C3:TRA ON")  # channels 1 and 3 are on different converters
    assert float(scope.process("SARA?").split()[1].removesuffix("Sa/s")) == pytest.approx(1e9)
    scope.process("C2:TRA ON")  # 1 and 2 share one
    assert float(scope.process("SARA?").split()[1].removesuffix("Sa/s")) == pytest.approx(5e8)


def test_siglent_errors():
    scope = make("siglent-sds1104x-e")
    for bad in ("C1:WF? DAT1", "C1:CPL X", "MSIZ 3K", "TRSE GLIT", "TRSE EDGE,SR,C9"):
        assert scope.process(bad) is None
        assert int(scope.process("*ESR?")) != 0
    scope.process("C2:TRA OFF")
    assert scope.process("C2:WF? DAT2") is None


def test_rigol_math_is_off_for_client_libraries():
    scope = make("rigol-ds1054z")  # the ds1054z client asks :MATH:DISPlay? to list the displayed channels
    assert scope.process(":MATH:DISP?") == "0"
    scope.process(":MATH:DISP OFF")
    assert scope.process(":SYST:ERR?").startswith("0")
    scope.process(":MATH:DISP ON")
    assert scope.process(":SYST:ERR?").startswith("-221")


def test_rigol_statistic_item():
    scope = make("rigol-ds1054z")
    scope.process(":TIM:SCAL 0.0002;:CHAN1:SCAL 0.5")
    assert float(scope.process(":MEAS:STAT:ITEM? CURR,VPP,CHAN1")) == pytest.approx(2.0, abs=0.05)
    assert float(scope.process(":MEAS:STAT:ITEM? MAX,vpp,CHAN1")) == pytest.approx(2.0, abs=0.05)  # as a client sends it
    assert float(scope.process(":MEAS:STAT:ITEM? DEV,VPP,CHAN1")) == 0
    assert float(scope.process(":MEAS:STAT:ITEM? DEV,FREQ,CHAN2")) == 9.9e37
    scope.process(":MEAS:STAT:ITEM? CURR,NOPE,CHAN1")
    assert scope.process(":SYST:ERR?").startswith("-224")
    scope.process(":MEAS:STAT:ITEM? MEDIAN,VPP,CHAN1")
    assert scope.process(":SYST:ERR?").startswith("-224")
