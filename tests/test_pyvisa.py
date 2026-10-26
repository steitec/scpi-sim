import pytest
import pyvisa

from scpi_sim import Multimeter
from scpi_sim.transport import InstrumentServer


def test_pyvisa_py_socket_resource():
    with InstrumentServer(Multimeter(volts=3.3, noise=0), port=0) as srv:
        rm = pyvisa.ResourceManager("@py")
        inst = rm.open_resource(f"TCPIP0::127.0.0.1::{srv.port}::SOCKET",
                                read_termination="\n", write_termination="\n", timeout=2000)
        assert inst.query("*IDN?").startswith("scpi-sim,DMM")
        assert float(inst.query("MEAS:VOLT?")) == pytest.approx(3.3)
        inst.close()
        rm.close()
