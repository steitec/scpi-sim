import pytest

from scpi_sim.bench import Bench, BenchError


def hmp_bench(**psu):
    return {
        "instruments": {
            "psu": {"profile": "rs-hmp4040", "port": 0, **psu},
            "dmm": {"type": "dmm", "port": 0, "noise": 0},
        },
        "loads": {"main": {"type": "resistor", "ohms": 10}},
        "wiring": ["psu.ch1 -> main", "dmm.input -> main"],
    }


def test_bench_profile_instrument():
    b = Bench.from_dict(hmp_bench())
    b.psu.process("INST OUT1")
    b.psu.process("APPL 5,1")
    b.psu.process("OUTP 1")
    assert b.dmm.process("MEAS:VOLT?") == "+5.00000000E+00"
    assert b.psu.process("MEAS:CURR?") == "0.500"


def test_profile_and_matching_type():
    b = Bench.from_dict(hmp_bench(type="psu"))
    assert b.psu.process("*IDN?").startswith("Rohde&Schwarz,HMP4040,")


@pytest.mark.parametrize("option", ["channels", "max_volts", "max_amps"])
def test_profile_with_fixed_option_rejected(option):
    with pytest.raises(BenchError, match=rf"'{option}'.*rs-hmp4040"):
        Bench.from_dict(hmp_bench(**{option: 8}))


def test_profile_type_mismatch():
    with pytest.raises(BenchError, match=r"type 'dmm' does not match profile 'rs-hmp4040' \(psu\)"):
        Bench.from_dict(hmp_bench(type="dmm"))


def test_unknown_profile():
    with pytest.raises(BenchError, match="unknown profile 'hmp9999'.*rs-hmp4040"):
        Bench.from_dict(hmp_bench(profile="hmp9999"))


@pytest.mark.parametrize("serial", [123, None, ""])
def test_serial_must_be_text(serial):
    with pytest.raises(BenchError, match="'serial'"):
        Bench.from_dict(hmp_bench(serial=serial))


def test_two_instruments_same_profile_own_serials():
    data = hmp_bench(serial="3629.6776.04/111111")
    data["instruments"]["psu2"] = {"profile": "rs-hmp4040", "port": 0, "serial": "3629.6776.04/222222"}
    data["instruments"]["psu3"] = {"profile": "rs-hmp4040", "port": 0}
    b = Bench.from_dict(data)
    serials = [b.instruments[n].process("*IDN?").split(",")[2] for n in ("psu", "psu2", "psu3")]
    assert serials == ["3629.6776.04/111111", "3629.6776.04/222222", "3629.6776.04/100001"]


def test_serial_on_generic_instrument():
    data = hmp_bench()
    data["instruments"]["dmm"]["serial"] = "MY123"
    assert Bench.from_dict(data).dmm.process("*IDN?").split(",")[2] == "MY123"


@pytest.mark.parametrize("value", [["rs-hmp4040"], 4040, {"name": "rs-hmp4040"}])
def test_profile_must_be_text(value):  # review: was "unhashable type"
    with pytest.raises(BenchError, match="instrument 'psu': 'profile' must be text"):
        Bench.from_dict(hmp_bench(profile=value))
