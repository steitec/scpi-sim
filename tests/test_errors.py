from scpi_sim.errors import STANDARD_ERRORS, ScpiError


def test_str_uses_standard_message():
    assert str(ScpiError(-113)) == '-113,"Undefined header"'


def test_custom_message():
    assert str(ScpiError(-222, "Voltage out of range")) == '-222,"Voltage out of range"'


def test_no_error_entry():
    assert STANDARD_ERRORS[0] == "No error"
