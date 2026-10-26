import pytest

from scpi_sim import Console, Device


class Lamp(Device):
    terminals = {"vin": "input", "sense": "output"}

    def current(self, terminal, volts):
        return 0.2 if volts > 3 else 0.0

    def voltage(self, terminal):
        return self.input_voltage("vin") / 2


def test_defaults_without_bench():
    lamp = Lamp()
    assert lamp.input_voltage("vin") == 0.0
    assert lamp.voltage("sense") == 0.0


def test_attach_provides_input_voltage():
    lamp = Lamp()
    lamp.attach(lambda terminal: 12.0)
    assert lamp.voltage("sense") == 6.0


def test_base_defaults():
    class Plain(Device):
        terminals = {"vin": "input", "out": "output"}

    d = Plain()
    assert d.current("vin", 5.0) == 0.0
    assert d.voltage("out") is None
    d.update()
    d.console_line("x")


def test_console_broadcast_and_drop_when_unattached():
    c = Console()
    seen_a, seen_b = [], []
    c.write("lost\n")
    c.attach(seen_a.append)
    c.attach(seen_b.append)
    c.write("hello\n")
    c.detach(seen_a.append)
    c.write("bye\n")
    assert seen_a == ["hello\n"]
    assert seen_b == ["hello\n", "bye\n"]


def test_console_sink_that_fails_is_detached():
    c = Console()
    seen = []

    def broken(text):
        raise OSError("gone")

    c.attach(broken)
    c.attach(seen.append)
    c.write("a")
    c.write("b")
    assert seen == ["a", "b"]


def test_each_device_has_its_own_console():
    assert Lamp().console is not Lamp().console


@pytest.mark.parametrize("terminals", [{"vin": "power"}, {"v in": "input"}, {"": "output"}])
def test_bad_terminal_spec_rejected(terminals):
    with pytest.raises(TypeError):
        type("Broken", (Device,), {"terminals": terminals})
