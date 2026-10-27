from scpi_sim.instrument import Instrument, scpi
from scpi_sim import __version__


class Dummy(Instrument):
    model, serial = "DUMMY", "SIM99999"

    def __init__(self):
        super().__init__()
        self.level = 0.0

    def reset(self):
        super().reset()
        self.level = 0.0

    @scpi("LEVel")
    def set_level(self, call):
        self.level = float(call.arg(0))

    @scpi("LEVel?")
    def get_level(self, call):
        return self.level

    @scpi("BOOM")
    def boom(self, call):
        raise RuntimeError("kaputt")


def test_idn():
    assert Dummy().process("*IDN?") == f"scpi-sim,DUMMY,SIM99999,{__version__}"


def test_set_and_query_joined():
    d = Dummy()
    assert d.process("LEV 2.5;LEV?;*OPC?") == "+2.50000000E+00;1"


def test_no_query_no_response():
    assert Dummy().process("LEV 1") is None


def test_undefined_header_queues_error():
    d = Dummy()
    assert d.process("FOO?") is None
    assert d.process("SYST:ERR?") == '-113,"Undefined header"'
    assert d.process("SYST:ERR?") == '0,"No error"'


def test_syntax_error_from_parser_is_queued():
    d = Dummy()
    assert d.process("LEV 1,,2;*OPC?") == "1"
    assert d.process("SYST:ERR?") == '-102,"Syntax error"'


def test_missing_parameter():
    d = Dummy()
    d.process("LEV")
    assert d.process("SYST:ERR?") == '-109,"Missing parameter"'


def test_handler_crash_is_device_error():
    d = Dummy()
    d.process("BOOM")
    assert d.process("SYST:ERR?").startswith("-300,")


def test_queue_overflow():
    d = Dummy()
    for _ in range(25):
        d.process("FOO")
    assert d.process("SYST:ERR:COUN?") == "20"
    errors = [d.process("SYST:ERR?") for _ in range(20)]
    assert errors[-1] == '-350,"Queue overflow"' and errors[0].startswith("-113")
    assert d.process("SYST:ERR:NEXT?") == '0,"No error"'


def test_rst_keeps_errors_cls_clears():
    d = Dummy()
    d.process("LEV 3;FOO")
    d.process("*RST")
    assert d.process("LEV?") == "+0.00000000E+00"
    assert d.process("SYST:ERR:COUN?") == "1"
    d.process("*CLS")
    assert d.process("SYST:ERR:COUN?") == "0"


def test_esr_pon_then_cme_and_clear():
    d = Dummy()
    assert d.process("*ESR?") == "128"
    d.process("FOO")
    assert d.process("*ESR?") == "32"
    assert d.process("*ESR?") == "0"


def test_opc_sets_esr_bit():
    d = Dummy()
    d.process("*ESR?;*OPC")
    assert d.process("*ESR?") == "1"


def test_stb_bits():
    d = Dummy()
    d.process("*ESR?")
    d.process("*ESE 32;*SRE 32;FOO")
    assert d.process("*STB?") == str(4 + 32 + 64)
    assert d.process("*ESE?;*SRE?") == "32;32"


def test_ese_out_of_range():
    d = Dummy()
    d.process("*ESE 256")
    assert d.process("SYST:ERR?").startswith("-222")


def test_misc_common_commands():
    d = Dummy()
    assert d.process("*TST?;*WAI;SYST:VERS?") == "0;1999.0"


def test_extra_parameters_rejected():
    d = Dummy()
    for message in ["LEV 1,2", "*RST 5", "*IDN? 5", "LEV? 1"]:
        assert d.process(message) is None
        assert d.process("SYST:ERR?") == '-108,"Parameter not allowed"', message


class Branded(Dummy):
    manufacturer, model, firmware = "ACME", "X1", "1.2"

    def __init__(self, serial=None):
        Instrument.__init__(self, serial=serial)
        self.level = 0.0


def test_identity_fields_and_serial_override():
    assert Branded(serial="42").process("*IDN?") == "ACME,X1,42,1.2"
    assert Branded().process("*IDN?") == "ACME,X1,SIM99999,1.2"


class ThreeDecimals(Dummy):
    def format_number(self, value):
        return f"{value:.3f}"

    @scpi("PAIR?")
    def pair(self, call):
        return [1.5, 2.0]


def test_number_format_hook():
    d = ThreeDecimals()
    d.process("LEV 2.5")
    assert d.process("LEV?;PAIR?") == "2.500;1.500,2.000"
