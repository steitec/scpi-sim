from scpi_sim.scpi.pattern import HeaderPattern as P


def test_short_and_long_forms():
    p = P("MEASure:VOLTage[:DC]?")
    for h in [("MEAS", "VOLT"), ("MEASURE", "VOLTAGE"), ("MEAS", "VOLT", "DC")]:
        assert p.match(h, True) == ()


def test_partial_keyword_rejected():
    assert P("MEASure:VOLTage?").match(("MEASU", "VOLT"), True) is None


def test_query_flag_must_match():
    assert P("MEASure:VOLTage?").match(("MEAS", "VOLT"), False) is None


def test_optional_leading_node():
    p = P("[SOURce:]VOLTage[:LEVel][:IMMediate][:AMPLitude]")
    assert p.match(("VOLT",), False) == ()
    assert p.match(("SOUR", "VOLT", "LEV", "IMM", "AMPL"), False) == ()


def test_numeric_suffix():
    p = P("OUTPut#[:STATe]")
    assert p.match(("OUTP2",), False) == (2,)
    assert p.match(("OUTPUT", "STAT"), False) == (None,)


def test_suffix_not_allowed_without_hash():
    assert P("MEASure:VOLTage?").match(("MEAS2", "VOLT"), True) is None


def test_common_command():
    assert P("*IDN?").match(("*IDN",), True) == ()
    assert P("*IDN?").is_query
