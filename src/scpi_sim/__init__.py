"""Simulated SCPI lab instruments on a raw TCP socket."""

__version__ = "0.1.0"

from .instrument import Instrument, scpi  # noqa: E402
from .instruments import Multimeter  # noqa: E402

__all__ = ["Instrument", "Multimeter", "scpi", "__version__"]
