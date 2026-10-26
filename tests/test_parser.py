from scpi_sim.errors import ScpiError
from scpi_sim.scpi.parser import Command, parse_message


def test_simple_query():
    assert parse_message("meas:volt:dc?") == [Command(("MEAS", "VOLT", "DC"), True, ())]


def test_leading_colon_and_args():
    assert parse_message(":ROUT:CLOS (@101, 102)") == [
        Command(("ROUT", "CLOS"), False, ("(@101, 102)",))
    ]


def test_multiple_args_extra_spaces():
    assert parse_message("VOLT  5 , MAX")[0].args == ("5", "MAX")


def test_path_rule_relative():
    cmds = parse_message("SOUR:VOLT 5;CURR 1")
    assert cmds[1].header == ("SOUR", "CURR")


def test_path_rule_absolute_and_common():
    cmds = parse_message("SOUR:VOLT 5;*OPC;CURR 1;:SYST:ERR?")
    assert [c.header for c in cmds] == [
        ("SOUR", "VOLT"), ("*OPC",), ("SOUR", "CURR"), ("SYST", "ERR")
    ]


def test_quoted_semicolon_not_split():
    assert len(parse_message('DISP:TEXT "a;b"')) == 1


def test_syntax_errors_are_returned_in_place():
    cmds = parse_message("*CLS;;VOLT 1,,2;*OPC")
    assert isinstance(cmds[1], ScpiError) and cmds[1].code == -102
    assert isinstance(cmds[2], ScpiError) and cmds[2].code == -102
    assert cmds[3] == Command(("*OPC",), False, ())


def test_trailing_semicolon_ok():
    assert len(parse_message("*CLS;")) == 1


def test_bad_header_characters():
    assert parse_message("VO?LT 1")[0].code == -102
    assert parse_message("V$LT 1")[0].code == -102


def test_unbalanced_quote_or_paren():
    assert parse_message('DISP:TEXT "abc')[0].code == -102
    assert parse_message("ROUT:CLOS (@101")[0].code == -102
