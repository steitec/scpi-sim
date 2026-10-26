import os
import re
import signal
import subprocess
import sys

import pytest

from helpers import connect, query
from scpi_sim import Multimeter
from scpi_sim.cli import main
from scpi_sim.transport import InstrumentServer
from scpi_sim import __version__


def start(*args, **popen_kwargs):
    p = subprocess.Popen([sys.executable, "-m", "scpi_sim.cli", "serve", *args, "--port", "0"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **popen_kwargs)
    line = p.stdout.readline()
    port = int(re.search(r":(\d+) \(Ctrl\+C", line).group(1))
    return p, line, port


def test_serve_psu_and_stop():
    p, line, port = start("psu", "--channels", "2", "--load", "10")
    try:
        assert line.startswith("scpi-sim: PSU listening on 127.0.0.1:")
        _, f = connect(port)
        assert query(f, "INST:NSEL 2;:VOLT 5;:CURR 1;:OUTP ON;:MEAS:CURR?") == "+5.00000000E-01"
        if os.name == "nt":  # no SIGINT to child processes on Windows
            p.terminate()
            p.wait(5)
        else:
            p.send_signal(signal.SIGINT)
            assert p.wait(5) == 0
            assert "scpi-sim: stopped" in p.stdout.read()
    finally:
        p.kill()
        p.communicate()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_stops_on_signal_even_when_started_in_background(sig):
    # a shell starts "scpi-sim serve ... &" with SIGINT ignored
    p, _, _ = start("mux", preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
    try:
        p.send_signal(sig)
        assert p.wait(5) == 0
    finally:
        p.kill()
        p.communicate()


def test_serve_dmm_options():
    p, line, port = start("dmm", "--volts", "1.8", "--noise", "0")
    try:
        _, f = connect(port)
        assert query(f, "MEAS:VOLT?") == "+1.80000000E+00"
    finally:
        p.kill()
        p.communicate()


def test_port_in_use_exit_1():
    with InstrumentServer(Multimeter(), port=0) as srv:
        r = subprocess.run([sys.executable, "-m", "scpi_sim.cli", "serve", "dmm", "--port", str(srv.port)],
                           capture_output=True, text=True, timeout=10)
    assert r.returncode == 1 and "cannot listen on 127.0.0.1:" in r.stderr


@pytest.mark.parametrize("args", [["serve", "mux", "--channels", "100"], ["serve", "psu", "--channels", "0"]])
def test_bad_channels_exit_2(args):
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_negative_load_exit_2():
    # subprocess with timeout: without validation the server would start and block
    r = subprocess.run([sys.executable, "-m", "scpi_sim.cli", "serve", "psu", "--load", "-5", "--port", "0"],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and "load" in r.stderr


@pytest.mark.parametrize("port", ["70000", "-1"])
def test_port_out_of_range_exit_2(port, capsys):
    with pytest.raises(SystemExit) as e:
        main(["serve", "dmm", "--port", port])
    assert e.value.code == 2
    assert "0-65535" in capsys.readouterr().err


def test_query_command_prints_responses(capsys):
    with InstrumentServer(Multimeter(volts=1.8, noise=0), port=0) as srv:
        rc = main(["query", "--port", str(srv.port), "*IDN?", "CONF:RES", "MEAS:VOLT?"])
    assert rc == 0
    assert capsys.readouterr().out == f"scpi-sim,DMM,SIM00001,{__version__}\n+1.80000000E+00\n"


def test_query_command_connection_refused(capsys):
    with InstrumentServer(Multimeter(), port=0) as srv:
        port = srv.port
    assert main(["query", "--port", str(port), "*IDN?"]) == 1
    assert "cannot connect" in capsys.readouterr().err


def test_query_command_timeout_when_no_answer(capsys):
    with InstrumentServer(Multimeter(), port=0) as srv:
        # "FOO?" is undefined: the instrument queues -113 and sends nothing
        assert main(["query", "--port", str(srv.port), "--timeout", "0.3", "FOO?"]) == 1
    assert "no response" in capsys.readouterr().err


def _start_bench(path):
    p = subprocess.Popen([sys.executable, "-m", "scpi_sim.cli", "serve", "--bench", str(path)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = [p.stdout.readline().strip() for _ in range(4)]
    ports = {}
    for line in lines:
        m = re.search(r"^scpi-sim: (\w+) .*:(\d+)", line)
        assert m, (line, p.stderr.read() if p.poll() is not None else "")
        ports[m.group(1)] = int(m.group(2))
    return p, ports


def test_serve_bench_and_fault_cli(tmp_path, capsys):
    from helpers import BASIC
    f = tmp_path / "bench.yaml"
    f.write_text(BASIC + "control: 0\n")
    p, ports = _start_bench(f)
    try:
        assert main(["fault", "dmm", "offset", "value=0.5", "--control-port", str(ports["control"])]) == 0
        assert main(["fault", "--list", "--control-port", str(ports["control"])]) == 0
        assert "dmm: offset value=0.5" in capsys.readouterr().out
        _, s = connect(ports["mux"])
        assert query(s, "ROUT:CLOS (@101);*OPC?") == "1"
        _, s = connect(ports["psu"])
        assert query(s, "VOLT 3;CURR 1;:OUTP ON;*OPC?") == "1"
        _, s = connect(ports["dmm"])
        assert query(s, "MEAS:VOLT?") == "+3.50000000E+00"
        assert main(["fault", "dmm", "--clear", "--control-port", str(ports["control"])]) == 0
        assert query(s, "MEAS:VOLT?") == "+3.00000000E+00"
    finally:
        p.kill()
        p.communicate()


def test_fault_cli_rejected_fault(tmp_path, capsys):
    from helpers import BASIC
    f = tmp_path / "bench.yaml"
    f.write_text(BASIC + "control: 0\n")
    p, ports = _start_bench(f)
    try:
        assert main(["fault", "psu", "offset", "value=1", "--control-port", str(ports["control"])]) == 1
        assert "not supported" in capsys.readouterr().err
    finally:
        p.kill()
        p.communicate()


def test_serve_bench_config_error_exit_2(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("instruments: {x: {type: scope, port: 0}}\n")
    r = subprocess.run([sys.executable, "-m", "scpi_sim.cli", "serve", "--bench", str(f)],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and "scope" in r.stderr and "Traceback" not in r.stderr


def test_serve_needs_exactly_one_mode(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["serve"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["serve", "--bench", str(tmp_path / "b.yaml"), "dmm"])
    assert e.value.code == 2


def test_fault_cli_unreachable(capsys):
    with InstrumentServer(Multimeter(), port=0) as srv:
        port = srv.port
    assert main(["fault", "dmm", "--clear", "--control-port", str(port)]) == 1
    assert "cannot connect" in capsys.readouterr().err


def test_fault_cli_needs_kind_or_clear(capsys):
    with pytest.raises(SystemExit) as e:
        main(["fault", "dmm"])
    assert e.value.code == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_bench_stops_on_sigint_right_after_start(tmp_path):
    from helpers import BASIC
    f = tmp_path / "bench.yaml"
    f.write_text(BASIC)
    p = subprocess.Popen([sys.executable, "-m", "scpi_sim.cli", "serve", "--bench", str(f)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
    try:
        p.stdout.readline()  # first line printed -> handlers must already be installed
        p.send_signal(signal.SIGINT)
        assert p.wait(5) == 0
    finally:
        p.kill()
        p.communicate()


def test_fault_cli_keeps_text_values(tmp_path):
    import yaml
    from helpers import BASIC
    from scpi_sim.bench import Bench
    data = yaml.safe_load(BASIC)
    data["control"] = 0
    with Bench.from_dict(data) as b:
        port = str(b.control_port)
        assert main(["fault", "dmm", "idn", "value=007", "--control-port", port]) == 0
        assert b.dmm.faults["idn"] == {"value": "007"}
        assert main(["fault", "dmm", "offset", "value=0.5", "--control-port", port]) == 0
        assert b.dmm.faults["offset"] == {"value": 0.5}
        assert main(["fault", "psu", "no_response", "count=2", "--control-port", port]) == 0
        assert b.psu.faults["no_response"] == {"count": 2}
