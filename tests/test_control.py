import json
import socket

import pytest
import yaml

from helpers import BASIC
from scpi_sim.bench import Bench
from scpi_sim.control import send_control


def bench_with_control():
    data = yaml.safe_load(BASIC)
    data["control"] = 0
    return Bench.from_dict(data)


def test_inject_clear_list():
    with bench_with_control() as b:
        port = b.control_port
        assert port
        assert send_control({"instrument": "dmm", "inject": "offset", "params": {"value": 1}}, port=port) == {"ok": True}
        assert send_control({"list": True}, port=port)["faults"] == {"psu": {}, "dmm": {"offset": {"value": 1.0}}, "mux": {}}
        assert b.dmm.faults == {"offset": {"value": 1.0}}
        assert send_control({"instrument": "dmm", "clear": True}, port=port) == {"ok": True}
        assert b.dmm.faults == {}


def test_errors_keep_connection_open():
    with bench_with_control() as b:
        with socket.create_connection(("127.0.0.1", b.control_port), timeout=2) as s, s.makefile("rwb") as f:
            for request in [b"not json", b'{"instrument": "scope", "clear": true}',
                            b'{"instrument": "dmm", "inject": "bogus"}', b'{"what": 1}']:
                f.write(request + b"\n")
                f.flush()
                answer = json.loads(f.readline())
                assert answer["ok"] is False and answer["error"]
            f.write(b'{"list": true}\n')
            f.flush()
            assert json.loads(f.readline())["ok"] is True


def test_error_message_names_instrument():
    with bench_with_control() as b:
        r = send_control({"instrument": "scope", "clear": True}, port=b.control_port)
        assert r["ok"] is False and "scope" in r["error"]


def test_no_control_port_no_server():
    with Bench.from_dict(yaml.safe_load(BASIC)) as b:
        assert b.control_port is None


def test_any_failure_is_an_error_answer(monkeypatch):
    with bench_with_control() as b:
        port = b.control_port
        r = send_control({"instrument": "dmm", "inject": "no_response", "params": {"count": 1e400}}, port=port)
        assert r["ok"] is False
        monkeypatch.setattr(b.dmm, "clear_faults", lambda: 1 / 0)
        r = send_control({"instrument": "dmm", "clear": True}, port=port)
        assert r["ok"] is False and "ZeroDivisionError" in r["error"]
        assert send_control({"list": True}, port=port)["ok"] is True


def test_stop_closes_open_control_connections():
    b = bench_with_control()
    b.start()
    s = socket.create_connection(("127.0.0.1", b.control_port), timeout=2)
    f = s.makefile("rwb")
    f.write(b'{"list": true}\n')
    f.flush()
    assert json.loads(f.readline())["ok"] is True
    b.stop()
    assert f.readline() == b""
    f.close()
    s.close()


def test_control_on_ipv6_loopback():
    if not socket.has_ipv6:
        import pytest
        pytest.skip("no IPv6")
    try:  # can this machine listen on ::1 at all?
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        import pytest
        pytest.skip("::1 not available")
    b = bench_with_control()
    b.start(host="::1")
    try:
        assert send_control({"list": True}, host="::1", port=b.control_port)["ok"] is True
    finally:
        b.stop()


def test_text_parameter_stays_text():
    with bench_with_control() as b:
        assert send_control({"instrument": "dmm", "inject": "idn", "params": {"value": None}},
                            port=b.control_port)["ok"] is False


def test_scope_faults_through_the_control_port():
    bench = Bench.from_dict({"instruments": {"scope": {"type": "scope", "port": 0, "noise": 0, "seed": 1}}, "control": 0})
    with bench:
        answer = send_control({"instrument": "scope", "inject": "signal",
                               "params": {"channel": "1", "shape": "square", "frequency": "5000", "vpp": "2"}},
                              port=bench.control_port)
        assert answer == {"ok": True}
        scope = bench.instruments["scope"]
        scope.process(":TIM:SCAL 1e-4")
        assert float(scope.process(":MEAS:FREQ?")) == pytest.approx(5000, rel=0.01)
        assert send_control({"instrument": "scope", "inject": "no_trigger"}, port=bench.control_port)["ok"]
        listed = send_control({"list": True}, port=bench.control_port)["faults"]["scope"]
        assert set(listed) == {"signal", "no_trigger"}
        bad = send_control({"instrument": "scope", "inject": "signal", "params": {"channel": "9"}},
                           port=bench.control_port)
        assert not bad["ok"] and "no channel 9" in bad["error"]
