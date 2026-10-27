"""Simulated SCPI lab instruments on a raw TCP socket."""

__version__ = "0.4.0"

from .instrument import Instrument, scpi  # noqa: E402
from .instruments import Multimeter, Multiplexer, PowerSupply  # noqa: E402
from .loads import Parallel, Resistor  # noqa: E402
from .transport import InstrumentServer  # noqa: E402
from .bench import Bench, BenchError  # noqa: E402
from .device import Console, Device  # noqa: E402
from .profiles import PROFILES, get_profile  # noqa: E402

__all__ = [
    "Bench", "BenchError", "Console", "Device", "Instrument", "InstrumentServer", "Multimeter", "Multiplexer",
    "PROFILES", "Parallel", "PowerSupply", "Resistor", "get_profile", "scpi", "__version__",
]
