"""End-of-line test of a simulated sensor board: power, rail, console.

    pytest examples/devices --scpi-bench examples/devices/bench.yaml
"""

import time

import pytest
import pyvisa
import serial


def read_until(port, text, timeout=3.0):
    data = ""
    end = time.monotonic() + timeout
    while text not in data:
        if time.monotonic() > end:
            raise TimeoutError(f"no {text!r} on the console, got {data!r}")
        data += port.read(256).decode(errors="replace")
    return data


@pytest.fixture
def station(scpi_bench):
    rm = pyvisa.ResourceManager("@py")
    names = ("psu", "dmm", "mux")
    psu, dmm, mux = (rm.open_resource(scpi_bench.resource(n), read_termination="\n",
                                      write_termination="\n", timeout=2000) for n in names)
    console = serial.serial_for_url(scpi_bench.console_url("board"), timeout=0.1)
    time.sleep(0.05)  # console connected before the board is powered
    yield psu, dmm, mux, console
    console.close()
    for instrument in (psu, dmm, mux):
        instrument.close()
    rm.close()


def test_board_boots_and_answers(station):
    psu, dmm, mux, console = station
    psu.query("VOLT 5;CURR 1;:OUTP ON;*OPC?")
    boot_log = read_until(console, "sensor> ")
    assert "SensorBoard" in boot_log

    console.write(b"id?\n")
    assert "SB-24-0042" in read_until(console, "sensor> ")
    console.write(b"temp?\n")
    assert "23.5" in read_until(console, "sensor> ")

    mux.query("ROUT:CLOS (@101);*OPC?")
    assert float(dmm.query("MEAS:VOLT?")) == pytest.approx(3.3, rel=0.01)
    assert float(psu.query("MEAS:CURR?")) == pytest.approx(0.08, abs=0.005)


def test_no_boot_below_minimum_input(station):
    psu, dmm, mux, console = station
    psu.query("VOLT 4;CURR 1;:OUTP ON;*OPC?")
    time.sleep(0.5)
    assert console.read(256) == b""
    mux.query("ROUT:CLOS (@101);*OPC?")
    assert float(dmm.query("MEAS:VOLT?")) == pytest.approx(0.0, abs=0.01)
