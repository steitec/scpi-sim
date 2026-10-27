"""keysight-daq970a, after the Keysight DAQ970A/DAQ973A Programming Guide DAQ97-90002, Edition 3."""

import threading

import pytest

from scpi_sim.bench import Bench, BenchError
from scpi_sim.profiles import get_profile

DAQ = get_profile("keysight-daq970a")


def errors(inst):
    found = []
    while not (error := inst.process("SYST:ERR?")).startswith("0,"):
        found.append(error.split(",")[0])
    return found


def bench(**daq):
    return Bench.from_dict({
        "instruments": {
            "psu": {"type": "psu", "port": 0, "channels": 2},
            "daq": {"profile": "keysight-daq970a", "port": 0, "noise": 0, **daq},
        },
        "loads": {"a": {"type": "resistor", "ohms": 1000}, "b": {"type": "resistor", "ohms": 1000}},
        "wiring": ["psu.ch1 -> a", "psu.ch2 -> b", "daq.101 -> a", "daq.102 -> b"],
    })


def powered(**daq):
    b = bench(**daq)
    b.psu.process("INST:NSEL 1;:VOLT 5;:CURR 1;:OUTP ON")
    b.psu.process("INST:NSEL 2;:VOLT 3.3;:CURR 1;:OUTP ON")
    return b


def test_identity():
    assert DAQ().process("*IDN?") == "Keysight Technologies,DAQ970A,MY58000001,A.02.04-00.16-11.29-00.02-02-01"


def test_measure_channel_list():
    b = powered()
    assert b.daq.process("MEAS:VOLT:DC? (@101,102)") == "+5.00000000E+00,+3.30000000E+00"
    assert b.daq.process("MEAS:VOLT:DC? 10,0.001 (@102)") == "+3.30000000E+00"  # manual example form
    assert b.daq.process("MEAS:VOLT:DC? 10,0.001,(@102:101)") == "+5.00000000E+00,+3.30000000E+00"  # sorted
    assert b.daq.process("MEAS:VOLT:DC? 1,(@101)") == "+9.90000000E+37"


def test_scan_with_conf_init_fetch():  # manual ROUTe:SCAN example
    b = powered()
    b.daq.process("CONF:VOLT:DC 10,0.003,(@101,102)")
    assert b.daq.process("ROUT:SCAN?") == "#210(@101,102)"
    b.daq.process("ROUT:SCAN (@101,102)")
    b.daq.process("INIT")
    assert b.daq.process("FETC?") == "+5.00000000E+00,+3.30000000E+00"
    assert b.daq.process("READ?") == "+5.00000000E+00,+3.30000000E+00"
    b.daq.process("ROUT:SCAN (@)")
    assert b.daq.process("ROUT:SCAN?") == "#13(@)"


def test_measure_reads_only_its_channel_and_opens_the_card():  # Review Focus 4
    b = powered()
    b.daq.process("ROUT:CLOS (@102)")
    assert b.daq.process("MEAS:VOLT:DC? (@101)") == "+5.00000000E+00"
    assert b.daq.process("ROUT:CLOS? (@101:103)") == "0,0,0"
    b.daq.process("ROUT:CLOS (@103)")  # 101 is in the scan list now: card 1 belongs to the scan
    assert errors(b.daq) == ["-221"]
    b.daq.process("ROUT:SCAN (@)")
    b.daq.process("ROUT:CLOS (@103)")
    assert errors(b.daq) == [] and b.daq.process("ROUT:CLOS? (@101:103)") == "0,0,1"


def test_channel_outside_the_model_gives_no_partial_answer():
    b = powered()
    assert b.daq.process("MEAS:VOLT:DC? (@101,121)") is None
    assert errors(b.daq) == ["-222"]
    assert b.daq.process("MEAS:VOLT:DC? (@201)") is None
    assert errors(b.daq) == ["-222"]


def test_cards_option():
    b = powered(cards=2)
    assert b.daq.process("MEAS:VOLT:DC? (@201)") == "+0.00000000E+00"
    assert b.daq.process("MEAS:VOLT:DC? (@301)") is None
    assert errors(b.daq) == ["-222"]
    with pytest.raises(BenchError, match="'cards'"):
        bench(cards=4)


def test_resistance():
    b = bench()
    assert b.daq.process("MEAS:RES? (@101)") == "+1.00000000E+03"


def test_offset_fault_on_internal_dmm():  # Review Focus 5
    b = powered()
    b.daq.inject("offset", value=0.1)
    assert b.daq.process("MEAS:VOLT:DC? (@101)") == "+5.10000000E+00"
    assert "offset" in b.daq.faults
    b.daq.clear_faults()
    assert b.daq.process("MEAS:VOLT:DC? (@101)") == "+5.00000000E+00"


def test_scan_respects_bench_relay_rules():
    b = Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0, "channels": 2},
                        "daq": {"profile": "keysight-daq970a", "port": 0, "noise": 0}},
        "wiring": ["psu.ch1 -> daq.101", "psu.ch2 -> daq.102", "daq.common -> x"],
        "loads": {"x": {"type": "resistor", "ohms": 100}},
    })
    assert b.daq.process("MEAS:VOLT:DC? (@101,102)") == "+0.00000000E+00,+0.00000000E+00"  # one at a time: fine
    b.daq.process("ROUT:SCAN (@)")
    b.daq.process("ROUT:CLOS (@101)")
    b.daq.process("ROUT:CLOS (@102)")  # two supplies on the common: refused
    assert errors(b.daq) == ["-221"]


def test_reset_clears_scan_list():
    b = powered()
    b.daq.process("CONF:VOLT:DC (@101)")
    b.daq.process("*RST")
    assert b.daq.process("ROUT:SCAN?") == "#13(@)"
    assert b.daq.process("FETC?") is None
    assert errors(b.daq) == ["-230"]


def test_each_card_has_its_own_common():  # review: a relay on card 2 must not affect card 1
    b = Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0, "channels": 2},
                        "daq": {"profile": "keysight-daq970a", "port": 0, "noise": 0, "cards": 2}},
        "loads": {"a": {"type": "resistor", "ohms": 1000}, "b": {"type": "resistor", "ohms": 1000}},
        "wiring": ["daq.101 -> a", "daq.201 -> b"],
    })
    b.daq.process("ROUT:CLOS (@201)")
    assert b.daq.process("MEAS:RES? (@101)") == "+1.00000000E+03"
    assert b.daq.process("MEAS:RES? (@201)") == "+1.00000000E+03"


def test_supplies_on_two_cards_closed_at_once():
    b = Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0, "channels": 2},
                        "daq": {"profile": "keysight-daq970a", "port": 0, "noise": 0, "cards": 2}},
        "wiring": ["psu.ch1 -> daq.101", "psu.ch2 -> daq.201", "daq.common1 -> x", "daq.common2 -> y"],
        "loads": {"x": {"type": "resistor", "ohms": 100}, "y": {"type": "resistor", "ohms": 100}},
    })
    b.daq.process("ROUT:CLOS (@101)")
    b.daq.process("ROUT:CLOS (@201)")
    assert errors(b.daq) == []


@pytest.mark.parametrize("command", ["ROUT:CLOS:STAT?", "ROUT:OPEN:ALL"])
def test_generic_commands_not_in_the_manual(command):
    daq = DAQ()
    daq.process(command)
    assert errors(daq) == ["-113"]


def test_manual_open_refused_on_a_card_in_the_scan_list():  # review: like ROUT:CLOS
    daq = DAQ(cards=2)
    daq.process("ROUT:SCAN (@101,102)")
    daq.process("ROUT:OPEN (@103)")
    assert errors(daq) == ["-221"]
    daq.process("ROUT:OPEN (@201)")  # a card without scan channels
    assert errors(daq) == []


def test_measure_with_empty_channel_list_is_an_error():  # review: was an empty answer
    daq = DAQ()
    daq.process("ROUT:SCAN (@101)")
    assert daq.process("MEAS:VOLT:DC? (@)") is None
    assert errors(daq) == ["-221"]
    assert daq.process("ROUT:SCAN?") == "#16(@101)"  # scan list unchanged


@pytest.mark.parametrize("action", [lambda daq: daq.inject("offset", value=0.1), lambda daq: daq.clear_faults()])
def test_fault_changes_wait_for_a_running_command(action):  # review: inject took only the DMM lock
    daq = DAQ()
    done = threading.Event()
    thread = threading.Thread(target=lambda: (action(daq), done.set()))
    with daq._lock:  # held while a command (a sweep) runs
        thread.start()
        assert not done.wait(0.2)
    assert done.wait(2)
    thread.join()
