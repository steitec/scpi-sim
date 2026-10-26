import pytest

from scpi_sim.loads import Parallel, Resistor, operating_point


def test_resistor_cv_and_cc():
    assert operating_point(Resistor(24), 12, 1) == (12, 0.5, "CV")
    assert operating_point(Resistor(24), 12, 0.25) == (6.0, 0.25, "CC")


def test_short_circuit():
    assert operating_point(Resistor(0), 5, 0.1) == (0.0, 0.1, "CC")
    assert operating_point(Resistor(0), 0, 0.1) == (0.0, 0.0, "CV")


def test_open():
    assert operating_point(None, 5, 0.1) == (5, 0.0, "CV")


def test_parallel_resistors():
    assert operating_point(Parallel((Resistor(20), Resistor(30))), 12, 3) == (12, 1.0, "CV")
    v, a, mode = operating_point(Parallel((Resistor(20), Resistor(30))), 12, 0.5)
    assert (v, a, mode) == (pytest.approx(6.0), 0.5, "CC")


def test_parallel_with_short():
    assert operating_point(Parallel((Resistor(20), Resistor(0))), 12, 0.5) == (0.0, 0.5, "CC")


def test_custom_load_bisection():
    class Diode:  # 0 A below 0.6 V, then 1 A per volt
        def current(self, v):
            return max(0.0, v - 0.6)

    v, a, mode = operating_point(Diode(), 5, 0.4)
    assert mode == "CC" and a == 0.4 and v == pytest.approx(1.0, abs=1e-9)


def test_custom_load_in_parallel_uses_bisection():
    class Constant:
        def current(self, v):
            return 0.1 if v > 0 else 0.0

    v, a, mode = operating_point(Parallel((Resistor(10), Constant())), 5, 0.3)
    assert mode == "CC" and a == 0.3 and v == pytest.approx(2.0, abs=1e-9)


def test_negative_resistance_rejected():
    with pytest.raises(ValueError):
        Resistor(-1)
