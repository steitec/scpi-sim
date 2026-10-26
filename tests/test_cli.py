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


def start(*args, **popen_kwargs):
    p = subprocess.Popen([sys.executable, "-m", "scpi_sim.cli", "serve", *args, "--port", "0"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **popen_kwargs)
    line = p.stdout.readline()
    port = int(re.search(r":(\d+) \(Ctrl\+C", line).group(1))
    return p, line, port


@pytest.mark.skipif(os.name == "nt", reason="no SIGINT to child processes on Windows")
def test_serve_psu_and_stop():
    p, line, port = start("psu", "--channels", "2", "--load", "10")
    try:
        assert line.startswith("scpi-sim: PSU listening on 127.0.0.1:")
        _, f = connect(port)
        assert query(f, "INST:NSEL 2;:VOLT 5;:CURR 1;:OUTP ON;:MEAS:CURR?") == "+5.00000000E-01"
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
    assert "0.1.0" in capsys.readouterr().out


def test_negative_load_exit_2():
    # subprocess with timeout: without validation the server would start and block
    r = subprocess.run([sys.executable, "-m", "scpi_sim.cli", "serve", "psu", "--load", "-5", "--port", "0"],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and "load" in r.stderr
