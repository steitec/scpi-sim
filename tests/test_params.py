import pytest

from scpi_sim.errors import ScpiError
from scpi_sim.scpi.params import format_number, parse_bool, parse_channel_list, parse_number

LIM = dict(minimum=0.0, maximum=30.0, default=0.0)


@pytest.mark.parametrize("arg,expected", [
    ("5", 5.0), (".5", 0.5), ("+5.0E+00", 5.0), ("500 mV", 0.5), ("500mv", 0.5),
    ("5V", 5.0), ("MAX", 30.0), ("min", 0.0), ("DEFault", 0.0), ("maximum", 30.0),
])
def test_parse_number(arg, expected):
    assert parse_number(arg, "V", **LIM) == pytest.approx(expected)


def test_megaohm_and_kilo():
    assert parse_number("1 MOHM", "OHM", minimum=0, maximum=1e9, default=0) == 1e6
    assert parse_number("4.7kohm", "OHM", minimum=0, maximum=1e9, default=0) == pytest.approx(4700)


def test_no_unit_expected():
    assert parse_number("3", minimum=0, maximum=5, default=0) == 3
    with pytest.raises(ScpiError, match="-131"):
        parse_number("3 V", minimum=0, maximum=5, default=0)


@pytest.mark.parametrize("arg,code", [("abc", -104), ("5 A", -131), ("31", -222), ("-1", -222)])
def test_parse_number_errors(arg, code):
    with pytest.raises(ScpiError) as e:
        parse_number(arg, "V", **LIM)
    assert e.value.code == code


def test_parse_bool():
    assert [parse_bool(a) for a in ["ON", "off", "1", "0"]] == [True, False, True, False]
    with pytest.raises(ScpiError, match="-224"):
        parse_bool("maybe")


def test_channel_list():
    assert parse_channel_list("(@101,103:105)") == [101, 103, 104, 105]
    assert parse_channel_list("(@ 105:103 )") == [105, 104, 103]
    with pytest.raises(ScpiError, match="-224"):
        parse_channel_list("101")


def test_format_number():
    assert format_number(5) == "+5.00000000E+00"
    assert format_number(-0.0012) == "-1.20000000E-03"


def test_channel_list_bounds_checked_before_expanding():
    with pytest.raises(ScpiError, match="-222"):
        parse_channel_list("(@101:999999999)", valid=range(101, 117))
    assert parse_channel_list("(@116:115)", valid=range(101, 117)) == [116, 115]


def test_channel_list_with_a_set_of_valid_channels():
    assert parse_channel_list("(@101:102,201)", valid={101, 102, 201}) == [101, 102, 201]
    with pytest.raises(ScpiError) as error:
        parse_channel_list("(@101:103)", valid={101, 103})
    assert error.value.code == -222
