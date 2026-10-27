"""Profiles: the generic instruments made to look and answer like real models.

A profile is a subclass of ``PowerSupply``, ``Multimeter`` or ``Multiplexer`` with
the class attributes ``profile`` (its name), ``kind`` (``psu``/``dmm``/``mux``),
``description`` and ``options`` (bench options it accepts besides ``port`` and
``serial``, as ``{name: converter}``).
"""

from __future__ import annotations

from ..instrument import Instrument
from .rs_hmp import RsHmp4040
from .rs_nge import RsNge103b
from .keysight_dmm import Keysight34461A
from .keysight_daq import KeysightDaq970A

PROFILES: dict[str, type[Instrument]] = {cls.profile: cls for cls in (
    RsHmp4040,
    RsNge103b,
    Keysight34461A,
    KeysightDaq970A,
)}


def get_profile(name: str) -> type[Instrument]:
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown profile {name!r} (known: {', '.join(PROFILES)})") from None
