import time

import pytest

from scpi_sim.bench import Bench, BenchError


def board_bench(cls="helpers:Board", args=None, channels=1, extra_wiring=()):
    device = {"class": cls}
    if args:
        device["args"] = args
    return Bench.from_dict({
        "instruments": {
            "psu": {"type": "psu", "port": 0, "channels": channels},
            "dmm": {"type": "dmm", "port": 0, "noise": 0},
            "mux": {"type": "mux", "port": 0},
        },
        "devices": {"board": device},
        "wiring": ["psu.ch1 -> board.vin", "mux.101 -> board.3V3", "mux.common -> dmm.input", *extra_wiring],
    })


def amps(b):
    return float(b.psu.process("MEAS:CURR?"))


def test_rail_measured_through_mux():
    b = board_bench()
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    b.mux.process("ROUT:CLOS (@101)")
    assert b.dmm.process("MEAS:VOLT?") == "+3.30000000E+00"


def test_rail_off_below_threshold():
    b = board_bench()
    b.psu.process("VOLT 4;CURR 1;:OUTP ON")
    b.mux.process("ROUT:CLOS (@101)")
    assert b.dmm.process("MEAS:VOLT?") == "+0.00000000E+00"


def test_inrush_then_idle_current():
    b = board_bench()
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    assert abs(amps(b) - 0.5) < 0.01
    time.sleep(0.3)
    assert abs(amps(b) - 0.1) < 0.01


def test_power_cycle_restarts_timeline():
    b = board_bench()
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    time.sleep(0.3)
    assert abs(amps(b) - 0.1) < 0.01
    b.psu.process("OUTP OFF")
    b.psu.process("OUTP ON")
    assert abs(amps(b) - 0.5) < 0.01


def test_device_short_goes_into_current_limit():
    b = board_bench(args={"short": True})
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    assert b.psu.process("OUTP:MODE?") == "CC"
    assert float(b.psu.process("MEAS:VOLT?")) == pytest.approx(0.5, abs=1e-6)


def test_update_called_after_every_message():
    b = board_bench()
    before = b.board.updates
    b.psu.process("*OPC?")
    b.mux.process("*OPC?")
    b.dmm.process("*OPC?")
    assert b.board.updates == before + 3


def test_device_output_and_supply_on_one_net():
    with pytest.raises(BenchError, match=r"board\.3V3.*psu\.ch1|psu\.ch1.*board\.3V3"):
        board_bench(extra_wiring=["psu.ch1 -> board.3V3"])


def test_relay_joining_device_rail_and_supply_refused():
    b = board_bench(channels=2, extra_wiring=["psu.ch2 -> mux.102"])
    b.mux.process("ROUT:CLOS (@101)")
    b.mux.process("ROUT:CLOS (@102)")
    assert b.mux.process("SYST:ERR?") == '-221,"Settings conflict"'
    assert b.mux.process("ROUT:CLOS:STAT?") == "(@101)"


def test_unknown_device_terminal():
    with pytest.raises(BenchError, match=r"board\.5V0"):
        board_bench(extra_wiring=["board.5V0 -> x"])


def test_class_must_be_a_device():
    with pytest.raises(BenchError, match="Device"):
        board_bench(cls="helpers:NotADevice")


def test_device_and_instrument_names_share_namespace():
    with pytest.raises(BenchError, match="psu"):
        Bench.from_dict({"instruments": {"psu": {"type": "psu", "port": 0}},
                         "devices": {"psu": {"class": "helpers:Board"}}})


def test_device_attribute_access():
    b = board_bench()
    assert b.board is b.devices["board"]


def test_device_bug_is_minus_300_not_a_crash():
    b = board_bench(cls="helpers:BuggyBoard")
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    assert b.psu.process("MEAS:CURR?") is None
    assert b.psu.process("SYST:ERR?").startswith("-300,")
    assert b.psu.process("*OPC?") == "1"


def test_update_failure_reported_once(capsys):
    b = board_bench(cls="helpers:BadUpdateBoard")
    for _ in range(3):
        assert b.psu.process("*OPC?") == "1"
    err = capsys.readouterr().err
    assert err.count("update() failed") == 1 and "board" in err
