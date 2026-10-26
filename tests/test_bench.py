import pytest
import yaml

from helpers import BASIC
from scpi_sim.bench import Bench, BenchError


def basic():
    return yaml.safe_load(BASIC)


def test_dmm_measures_psu_through_closed_relay():
    b = Bench.from_dict(basic())
    b.psu.process("VOLT 12;CURR 1;:OUTP ON")
    assert b.dmm.process("MEAS:VOLT?") == "+0.00000000E+00"  # relay open
    b.mux.process("ROUT:CLOS (@101)")
    assert b.dmm.process("MEAS:VOLT?") == "+1.20000000E+01"
    assert b.psu.process("MEAS:CURR?") == "+5.00000000E-01"  # 12 V / 24 ohm


def test_current_limit_visible_on_dmm():
    b = Bench.from_dict(basic())
    b.mux.process("ROUT:CLOS (@101)")
    b.psu.process("VOLT 12;CURR 0.25;:OUTP ON")
    assert b.dmm.process("MEAS:VOLT?") == "+6.00000000E+00"


def test_resistance_of_unpowered_net():
    b = Bench.from_dict(basic())
    assert b.dmm.process("MEAS:RES?") == "+9.90000000E+37"  # open input
    b.mux.process("ROUT:CLOS (@101)")
    assert b.dmm.process("MEAS:RES?") == "+2.40000000E+01"


def test_psu_without_wiring_is_open():
    b = Bench.from_dict({"instruments": {"psu": {"type": "psu", "port": 0}}})
    b.psu.process("VOLT 5;:OUTP ON")
    assert b.psu.process("MEAS:CURR?;:OUTP:MODE?") == "+0.00000000E+00;CV"


def test_custom_python_load(tmp_path, monkeypatch):
    (tmp_path / "myloads.py").write_text(
        "class Lamp:\n"
        "    def __init__(self, amps):\n"
        "        self.amps = amps\n"
        "    def current(self, v):\n"
        "        return self.amps if v > 0 else 0.0\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    data = basic()
    data["loads"]["main"] = {"type": "python", "class": "myloads:Lamp", "args": {"amps": 0.3}}
    b = Bench.from_dict(data)
    b.psu.process("VOLT 5;CURR 1;:OUTP ON")
    assert b.psu.process("MEAS:CURR?") == "+3.00000000E-01"


def _dup_port(d):
    d["instruments"]["dmm"]["port"] = 5025
    d["instruments"]["psu"]["port"] = 5025


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["wiring"].append("psu.chl -> main"), "psu.chl"),
    (lambda d: d["wiring"].append("scope.ch1 -> main"), "scope"),
    (lambda d: d["wiring"].append("psu.ch2 -> main"), "psu.ch2"),
    (lambda d: d["wiring"].append("mux.117 -> main"), "mux.117"),
    (lambda d: d["wiring"].append("main main"), "main main"),
    (_dup_port, "5025"),
    (lambda d: d["loads"].update(spare={"type": "resistor", "ohms": 1}), "spare"),
    (lambda d: d["instruments"]["psu"].update(type="scope"), "scope"),
    (lambda d: d["instruments"]["psu"].update(colour="red"), "colour"),
    (lambda d: d["instruments"]["psu"].pop("port"), "port"),
    (lambda d: d["loads"]["main"].update(ohms=-1), "main"),
    (lambda d: d["loads"].update(x={"type": "python", "class": "nosuchmodule:X"}), "nosuchmodule"),
    (lambda d: d.update(extra=1), "extra"),
])
def test_config_errors_name_the_problem(mutate, needle):
    d = basic()
    mutate(d)
    with pytest.raises(BenchError, match=needle):
        Bench.from_dict(d)


def test_yaml_syntax_error(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("instruments: [\n")
    with pytest.raises(BenchError, match="bench.yaml"):
        Bench.from_file(f)


def test_from_file(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text(BASIC)
    assert set(Bench.from_file(f).instruments) == {"psu", "dmm", "mux"}


def test_two_supplies_on_one_static_net():
    d = basic()
    d["instruments"]["psu"]["channels"] = 2
    d["wiring"].append("psu.ch2 -> main")
    with pytest.raises(BenchError, match="psu.ch2"):
        Bench.from_dict(d)


def test_relay_that_would_short_two_supplies_is_refused():
    d = basic()
    d["instruments"]["psu"]["channels"] = 2
    d["wiring"] += ["psu.ch2 -> aux", "mux.102 -> aux"]
    b = Bench.from_dict(d)
    b.mux.process("ROUT:CLOS (@101)")
    b.mux.process("ROUT:CLOS (@102)")
    assert b.mux.process("SYST:ERR?") == '-221,"Settings conflict"'
    assert b.mux.process("ROUT:CLOS:STAT?") == "(@101)"


def test_second_channel_through_other_relay():
    d = basic()
    d["instruments"]["psu"]["channels"] = 2
    d["wiring"] += ["psu.ch2 -> aux", "mux.102 -> aux"]
    b = Bench.from_dict(d)
    b.psu.process("SOUR2:VOLT 3.3;:OUTP2 ON")
    b.mux.process("ROUT:CLOS (@102)")
    assert b.dmm.process("MEAS:VOLT?") == "+3.30000000E+00"


def test_start_resources_and_ports():
    with Bench.from_dict(basic()) as b:  # all ports 0 in BASIC
        assert b.resource("dmm") == f"TCPIP0::127.0.0.1::{b.port('dmm')}::SOCKET"
        assert len({b.port(n) for n in ("psu", "dmm", "mux")}) == 3


def test_unknown_instrument_attribute():
    with pytest.raises(AttributeError):
        Bench.from_dict(basic()).scope
