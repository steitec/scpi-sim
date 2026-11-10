"""Tests for the Python layer of the browser demo (web/py/demo.py), run on CPython."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web" / "py"))
sys.path.insert(0, str(ROOT / "examples" / "power_on"))  # build_site.py puts power_board.py next to the bench

from demo import Demo  # noqa: E402

BENCH = ROOT / "web" / "py" / "bench.yaml"
POWER_ON = "INST OUT1; VOLT 12; OUTP:SEL ON;:OUTP:GEN ON"  # ";:" back to the root
SCOPE_POWER_SETUP = (":CHAN1:DISP OFF;:CHAN2:DISP OFF;:CHAN3:DISP ON;:CHAN3:SCAL 0.05;:CHAN3:OFFS -0.15;"
                     ":CHAN4:DISP ON;:CHAN4:SCAL 1;:CHAN4:OFFS -2;:TIM:SCAL 0.002;:TIM:OFFS 0.008;"
                     ":TRIG:EDG:SOUR CHAN3;:TRIG:EDG:LEV 0.02")


@pytest.fixture
def demo():
    return Demo(str(BENCH))


def test_tour(demo):
    assert demo.send("psu", "*IDN?")["response"].startswith("Rohde&Schwarz,HMP4040")
    r = demo.send("psu", POWER_ON)
    assert r["response"] is None and r["timeout"] is False
    ch1 = r["state"]["psu"]["channels"][0]
    assert ch1["active"] and r["state"]["psu"]["general"] and ch1["mode"] == "CV"
    assert ch1["out_a"] == pytest.approx(0.5)
    assert demo.send("psu", "MEAS:CURR?")["response"] == "0.500"
    r = demo.send("daq", "MEAS:VOLT:DC? (@101)")
    assert float(r["response"]) == pytest.approx(12, abs=0.01)
    assert r["state"]["daq"]["last"]["channel"] == 101
    demo.send("psu", "VOLT 9")
    assert float(demo.send("daq", "MEAS:VOLT:DC? (@101)")["response"]) == pytest.approx(9, abs=0.01)


def test_state_shape(demo):
    s = demo.state()
    assert s["psu"]["model"] == "HMP4040" and s["daq"]["model"] == "DAQ970A"
    assert len(s["psu"]["channels"]) == 4
    ch2 = s["psu"]["channels"][1]
    assert set(ch2) == {"set_v", "set_a", "active", "out_v", "out_a", "mode"}
    assert (ch2["active"], ch2["out_v"], ch2["out_a"], ch2["mode"]) == (False, 0.0, 0.0, "OFF")
    assert s["psu"]["selected"] == 1 and s["psu"]["error"] is False and s["psu"]["faults"] == []
    assert s["daq"]["closed"] == [] and s["daq"]["last"] is None


def test_error_queue(demo):
    assert demo.send("psu", "FOO")["state"]["psu"]["error"] is True
    assert demo.send("psu", "SYST:ERR?")["response"].startswith("-113")
    assert demo.state()["psu"]["error"] is False


def test_query_error_is_timeout(demo):
    r = demo.send("psu", "FOO?")
    assert r["timeout"] is True and r["state"]["psu"]["error"] is True


def test_fault_no_response(demo):
    assert demo.inject("daq-no-response")["daq"]["faults"] == ["no_response"]
    assert demo.send("daq", "*IDN?")["timeout"] is True
    r = demo.send("daq", "*IDN?")
    assert r["timeout"] is False and r["state"]["daq"]["faults"] == []


def test_fault_offset(demo):
    demo.send("psu", POWER_ON)
    assert demo.inject("daq-offset")["daq"]["faults"] == ["offset"]
    assert float(demo.send("daq", "MEAS:VOLT:DC? (@101)")["response"]) == pytest.approx(12.5, abs=0.01)


def test_fault_psu_error(demo):
    assert demo.inject("psu-error")["psu"]["error"] is True
    assert demo.send("psu", "SYST:ERR?")["response"].startswith("-222")


def test_reset(demo):
    demo.send("psu", POWER_ON + "; FOO")
    demo.send("daq", "MEAS:VOLT:DC? (@101)")
    demo.inject("daq-offset")
    s = demo.reset()
    assert not s["psu"]["general"] and not s["psu"]["error"]
    assert s["daq"]["faults"] == [] and s["daq"]["last"] is None


def test_garbage_input(demo):
    r = demo.send("psu", "VOLT " + "9" * 10_000 + "\x00\x1b[31mÄ")
    assert r["timeout"] is False and isinstance(r["state"], dict)
    json.dumps(r)


def test_long_input_is_cut(demo):
    # a valid command past MAX_LINE is never executed
    r = demo.send("psu", " " * 300 + "*IDN?")
    assert r["response"] is None


def test_unknown_target_or_fault(demo):
    with pytest.raises(ValueError):
        demo.send("dmm", "*IDN?")
    with pytest.raises(ValueError):
        demo.inject("nope")


def test_echo_is_the_line_that_was_sent(demo):
    # the terminal shows what the instrument got: cut and without control characters
    assert demo.send("psu", "*IDN?\x1b[31m")["line"] == "*IDN?[31m"
    assert demo.send("psu", "X" * 300)["line"] == "X" * 256


def test_question_mark_in_an_argument_is_no_query(demo):
    assert demo.send("psu", 'FOO "what?"')["timeout"] is False
    assert demo.send("psu", "VOLT 5;FOO?")["timeout"] is True
    assert demo.send("psu", "*CLS;:FOO:BAR? 1")["timeout"] is True


def test_daq_reading_carries_its_function(demo):
    demo.send("psu", POWER_ON)
    demo.send("daq", "MEAS:VOLT:DC? (@101)")
    assert demo.state()["daq"]["last"]["function"] == "VOLT"
    demo.send("daq", "MEAS:RES? (@102)")
    assert demo.state()["daq"]["last"] == {"channel": 102, "function": "RES",
                                           "value": demo.state()["daq"]["last"]["value"]}


# --- the oscilloscope ----------------------------------------------------------------------


def test_scope_screen_after_power_on(demo):
    scope = demo.state()["scope"]
    assert scope["model"] == "DS1054Z" and scope["columns"] == 12
    assert scope["status"] == "triggered"
    (trace,) = scope["traces"]
    assert trace["channel"] == 1 and trace["scale"] == pytest.approx(0.5)
    assert len(trace["divisions"]) == 600
    assert max(trace["divisions"]) == pytest.approx(2.0, abs=0.1)  # 1 V at 500 mV/div
    assert scope["trigger"] == {"source": 1, "level": 0.0, "slope": "POS", "divisions": 0.0}


def test_scope_binary_answer_is_shown_by_its_size(demo):
    r = demo.send("scope", ":WAV:DATA?")
    assert r["response"] == "#9000001200<1200 bytes of binary data>"
    assert r["timeout"] is False


def test_scope_long_text_answer_is_shortened(demo):
    demo.send("scope", ":WAV:FORM ASC")
    answer = demo.send("scope", ":WAV:DATA?")["response"]
    assert len(answer) < 450 and answer.endswith(" characters)")


def test_scope_tour(demo):
    assert float(demo.send("scope", ":MEAS:ITEM? FREQ,CHAN1")["response"]) == pytest.approx(1e3, rel=0.01)
    assert float(demo.send("scope", ":MEAS:ITEM? VPP,CHAN1")["response"]) == pytest.approx(2, abs=0.1)

    demo.send("scope", ":CHAN1:SCAL 0.1")  # clips
    assert float(demo.send("scope", ":MEAS:ITEM? VPP,CHAN1")["response"]) == pytest.approx(1.02, abs=0.03)
    demo.send("scope", ":CHAN1:SCAL 0.5")

    demo.send("scope", ":CHAN2:SCAL 5")
    state = demo.send("scope", ":CHAN2:DISP ON")["state"]["scope"]
    rail = next(t for t in state["traces"] if t["channel"] == 2)
    assert abs(sum(rail["divisions"]) / 600) < 0.1  # the supply is off
    state = demo.send("psu", POWER_ON)["state"]["scope"]
    rail = next(t for t in state["traces"] if t["channel"] == 2)
    assert sum(rail["divisions"]) / 600 == pytest.approx(2.4, abs=0.1)  # 12 V at 5 V/div

    demo.send("scope", ":TRIG:EDG:LEV 1.5")
    assert demo.send("scope", ":SING")["state"]["scope"]["status"] == "wait"
    assert demo.send("scope", "*OPC?")["timeout"] is True
    demo.send("scope", ":TRIG:EDG:LEV 0")
    assert demo.send("scope", "*OPC?")["response"] == "1"
    assert demo.send("scope", ":RUN")["state"]["scope"]["status"] == "triggered"


@pytest.mark.parametrize("fault, kind", [("scope-no-trigger", "no_trigger"), ("scope-noise", "noise"),
                                         ("scope-probe-off", "signal")])
def test_scope_faults_and_reset(demo, fault, kind):
    state = demo.inject(fault)["scope"]
    assert state["faults"] == [kind]
    state = demo.reset()["scope"]
    assert state["faults"] == [] and state["status"] == "triggered"
    assert state["time_scale"] == pytest.approx(2e-4)  # the demo's setup, not the 1 us/div of *RST


def test_scope_fault_effects(demo):
    assert demo.inject("scope-no-trigger")["scope"]["status"] == "auto"
    demo.reset()
    (trace,) = demo.inject("scope-probe-off")["scope"]["traces"]
    assert max(map(abs, trace["divisions"])) < 0.3
    demo.reset()
    (trace,) = demo.inject("scope-noise")["scope"]["traces"]
    assert max(trace["divisions"]) > 2.2


def test_scope_state_survives_rst_by_hand(demo):
    state = demo.send("scope", "*RST")["state"]["scope"]
    assert state["time_scale"] == pytest.approx(1e-6)
    demo.send("scope", ":CHAN1:DISP OFF")
    assert demo.state()["scope"]["traces"] == []


def test_power_on_chip_catches_the_board(demo):
    demo.send("psu", "INST OUT3; VOLT 12; CURR 3; OUTP:SEL OFF")
    demo.send("scope", SCOPE_POWER_SETUP)
    assert demo.send("scope", ":SING")["state"]["scope"]["status"] == "wait"
    r = demo.send("psu", "OUTP:SEL ON;:OUTP:GEN ON")
    assert r["state"]["scope"]["status"] == "stop"
    traces = {trace["channel"]: trace["divisions"] for trace in r["state"]["scope"]["traces"]}
    assert set(traces) == {3, 4}
    assert float(demo.send("scope", ":MEAS:ITEM? VMAX,CHAN3")["response"]) == pytest.approx(0.22, abs=0.01)
    assert float(demo.send("scope", ":MEAS:ITEM? VMAX,CHAN4")["response"]) == pytest.approx(3.3, abs=0.1)
    assert traces[4][0] < traces[4][-1]  # the rail comes up on the screen
    assert demo.reset()["scope"]["status"] != "stop"


def test_power_board_is_in_the_build():
    text = (ROOT / "web" / "build_site.py").read_text(encoding="utf-8")
    assert "power_board.py" in text
    assert "power_board.py" in (ROOT / "web" / "site" / "sim.js").read_text(encoding="utf-8")
