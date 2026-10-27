import pytest

from scpi_sim import __version__
from scpi_sim.profiles import PROFILES, get_profile


def test_lookup():
    assert get_profile("rs-hmp4040").kind == "psu"


def test_unknown_profile_names_the_known_ones():
    with pytest.raises(KeyError, match="rs-hmp4040"):
        get_profile("nope")


@pytest.mark.parametrize("name", list(PROFILES))
def test_every_profile_is_complete(name):
    cls = PROFILES[name]
    assert cls.profile == name
    assert cls.kind in ("psu", "dmm", "mux")
    assert cls.description
    idn = cls().process("*IDN?")
    assert not idn.startswith("scpi-sim") and __version__ not in idn
