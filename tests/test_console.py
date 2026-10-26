import socket
import struct
import subprocess
import sys
import time

import pytest

from helpers import read_until
from scpi_sim.bench import Bench


def console_bench(cls="helpers:EchoBoard", console=0):
    return Bench.from_dict({
        "instruments": {"psu": {"type": "psu", "port": 0}},
        "devices": {"board": {"class": cls, "console": console}},
        "wiring": ["psu.ch1 -> board.vin"],
    })


def open_console(bench):
    return socket.create_connection(("127.0.0.1", bench.console_port("board")), timeout=2)


def test_console_output_and_input():
    with console_bench() as b:
        with open_console(b) as c:
            time.sleep(0.05)  # let the server attach the client
            b.psu.process("VOLT 5;CURR 1;:OUTP ON")
            assert b"boot\n" in read_until(c, b"boot\n")
            c.sendall(b"hi\r\n")
            assert b"> hi\n" in read_until(c, b"> hi\n")


def test_late_client_sees_only_new_output():
    with console_bench() as b:
        b.psu.process("VOLT 5;CURR 1;:OUTP ON")  # "boot" goes nowhere
        with open_console(b) as c:
            time.sleep(0.05)
            c.sendall(b"x\n")
            data = read_until(c, b"> x\n")
            assert b"boot" not in data


def test_two_clients_both_receive_and_both_can_type():
    with console_bench() as b:
        with open_console(b) as one, open_console(b) as two:
            time.sleep(0.05)
            one.sendall(b"from one\n")
            two.sendall(b"from two\n")
            for c in (one, two):
                data = read_until(c, b"> from two\n")
                data += read_until(c, b"> from one\n") if b"> from one\n" not in data else b""
                assert b"> from one\n" in data and b"> from two\n" in data


def test_dropped_client_does_not_break_the_other():
    with console_bench() as b:
        with open_console(b) as stays:
            gone = open_console(b)
            time.sleep(0.05)
            gone.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            gone.close()
            time.sleep(0.05)
            b.board.console.write("still here\n")
            assert b"still here\n" in read_until(stays, b"still here\n")
            stays.sendall(b"ok\n")
            assert b"> ok\n" in read_until(stays, b"> ok\n")


def test_console_url_and_errors():
    with console_bench() as b:
        assert b.console_url("board") == f"socket://127.0.0.1:{b.console_port('board')}"
        with pytest.raises(KeyError):
            b.console_port("psu")


def test_device_without_console_has_no_port():
    b = Bench.from_dict({"devices": {"board": {"class": "helpers:EchoBoard"}},
                         "instruments": {"psu": {"type": "psu", "port": 0}}})
    with b:
        with pytest.raises(KeyError):
            b.console_port("board")


def test_console_line_exception_does_not_kill_server(capsys):
    with console_bench(cls="helpers:GrumpyBoard") as b:
        with open_console(b) as c:
            time.sleep(0.05)
            c.sendall(b"crash\n")
            c.sendall(b"after\n")
            assert b"> after\n" in read_until(c, b"> after\n")
    assert "console_line" in capsys.readouterr().err


def test_console_stops_with_the_bench():
    b = console_bench()
    b.start()
    c = open_console(b)
    time.sleep(0.05)
    b.stop()
    c.settimeout(2)
    assert c.recv(10) == b""
    c.close()


def test_cli_prints_console_line(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("instruments: {psu: {type: psu, port: 0}}\n"
                 "devices: {board: {class: 'helpers:EchoBoard', console: 0}}\n"
                 "wiring: [psu.ch1 -> board.vin]\n")
    import os
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(__import__('pathlib').Path(__file__).parent),
                                                       os.environ.get("PYTHONPATH", "")]))
    p = subprocess.Popen([sys.executable, "-m", "scpi_sim.cli", "serve", "--bench", str(f)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        lines = [p.stdout.readline() for _ in range(2)]
        assert lines[0].startswith("scpi-sim: psu (PSU) listening on 127.0.0.1:")
        assert lines[1].startswith("scpi-sim: board console on 127.0.0.1:")
    finally:
        p.kill()
        p.communicate()
