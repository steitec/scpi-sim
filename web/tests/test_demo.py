"""Tests for the Python layer of the browser demo (web/py/demo.py), run on CPython."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web" / "py"))

from demo import Demo  # noqa: E402

BENCH = ROOT / "examples" / "profiles" / "bench.yaml"
POWER_ON = "INST OUT1; VOLT 12; OUTP:SEL ON;:OUTP:GEN ON"  # ";:" back to the root


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
