"""Instruments of one bench run in different threads; reads across them must not race."""

import sys
import threading
import time

import pytest
import yaml

from helpers import BASIC
from scpi_sim.bench import Bench


@pytest.fixture
def fast_switching():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def hammer(target, seconds=0.5):
    stop = time.monotonic() + seconds
    thread = threading.Thread(target=lambda: [target() for _ in iter(lambda: time.monotonic() < stop, False)])
    thread.start()
    return thread


def test_relay_changes_while_measuring(fast_switching):
    data = yaml.safe_load(BASIC)
    data["instruments"]["mux"]["channels"] = 99
    b = Bench.from_dict(data)
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    worker = hammer(lambda: (b.mux.process("ROUT:CLOS (@101:199)"), b.mux.process("ROUT:OPEN (@102:199)"),
                             b.mux.process("ROUT:OPEN:ALL"), b.mux.process("*RST")))
    answers = []
    while worker.is_alive():
        answers.append(b.dmm.process("MEAS:VOLT?"))
    worker.join()
    assert None not in answers
    assert b.dmm.process("SYST:ERR?") == '0,"No error"'


def test_supply_reset_while_measuring(fast_switching):
    b = Bench.from_dict(yaml.safe_load(BASIC))
    b.mux.process("ROUT:CLOS (@101)")
    worker = hammer(lambda: b.psu.process("*RST;VOLT 5;CURR 1;:OUTP ON"))
    answers = []
    while worker.is_alive():
        answers.append(b.dmm.process("MEAS:VOLT?"))
    worker.join()
    assert None not in answers
    assert b.dmm.process("SYST:ERR?") == '0,"No error"'


def test_two_muxes_cannot_join_two_supplies_concurrently(fast_switching):
    data = {
        "instruments": {
            "psu": {"type": "psu", "port": 0, "channels": 2},
            "mux_a": {"type": "mux", "port": 0},
            "mux_b": {"type": "mux", "port": 0},
        },
        "wiring": ["psu.ch1 -> mux_a.101", "psu.ch2 -> mux_b.101", "mux_a.common -> bus", "mux_b.common -> bus"],
    }
    for _ in range(300):
        b = Bench.from_dict(data)
        barrier = threading.Barrier(2)

        def close(mux):
            barrier.wait()
            mux.process("ROUT:CLOS (@101)")

        threads = [threading.Thread(target=close, args=(m,)) for m in (b.mux_a, b.mux_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not (b.mux_a.closed and b.mux_b.closed)
