"""Profiles: the generic instruments made to look and answer like real models.

A profile is a subclass of ``PowerSupply``, ``Multimeter``, ``Multiplexer`` or
``ScopeEngine`` with the class attributes ``profile`` (its name), ``kind``
(``psu``/``dmm``/``mux``/``scope``),
``description`` and ``options`` (bench options it accepts besides ``port`` and
``serial``, as ``{name: converter}``).
"""

from __future__ import annotations

from ..instrument import Instrument
from .rs_hmp import RsHmp4040
from .rs_nge import RsNge103b
from .keysight_dmm import Keysight34461A
from .keysight_daq import KeysightDaq970A
from .keysight_psu import KeysightE36312A
from .siglent_psu import SiglentSpd3303x
from .rs_dmm import RsHmc8012
from .siglent_dmm import SiglentSdm3065x
from .keithley import KeithleyDaq6510, KeithleyDmm6500
from .keysight_scope import KeysightDsox2024a
from .rigol_scope import RigolDs1054z
from .rs_scope import RsRtb2004
from .siglent_scope import SiglentSds1104xe
from .tektronix_scope import TektronixTbs2104b

PROFILES: dict[str, type[Instrument]] = {cls.profile: cls for cls in (
    RsHmp4040,
    RsNge103b,
    KeysightE36312A,
    SiglentSpd3303x,
    RsHmc8012,
    Keysight34461A,
    KeithleyDmm6500,
    SiglentSdm3065x,
    KeysightDaq970A,
    KeithleyDaq6510,
    KeysightDsox2024a,
    RigolDs1054z,
    TektronixTbs2104b,
    RsRtb2004,
    SiglentSds1104xe,
)}


def get_profile(name: str) -> type[Instrument]:
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown profile {name!r} (known: {', '.join(PROFILES)})") from None
