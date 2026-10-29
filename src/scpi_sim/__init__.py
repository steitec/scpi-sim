"""Simulated SCPI lab instruments on a raw TCP socket."""

__version__ = "0.5.0"

from .instrument import Instrument, scpi  # noqa: E402
from .instruments import Multimeter, Multiplexer, PowerSupply  # noqa: E402
from .loads import Parallel, Resistor  # noqa: E402
from .transport import InstrumentServer  # noqa: E402
from .hislip import HislipServer  # noqa: E402
from .vxi11 import PortMapper, Vxi11Server  # noqa: E402
from .bench import Bench, BenchError  # noqa: E402
from .device import Console, Device  # noqa: E402
from .profiles import PROFILES, get_profile  # noqa: E402

__all__ = [
    "Bench", "BenchError", "Console", "Device", "HislipServer", "Instrument", "InstrumentServer", "Multimeter",
    "Multiplexer", "PROFILES", "Parallel", "PortMapper", "PowerSupply", "Resistor", "Vxi11Server", "get_profile",
    "scpi", "__version__",
]
