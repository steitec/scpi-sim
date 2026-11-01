from pathlib import Path

import pytest

from scpi_sim.bench import Bench

BENCH = Path(__file__).parent.parent / "examples" / "profiles" / "bench.yaml"


def test_profiles_example_bench():
    if not BENCH.exists():
        pytest.skip("examples not available")
    b = Bench.from_file(BENCH)
    assert b.psu.process("*IDN?").startswith("Rohde&Schwarz,HMP4040,")
    for channel, volts in ((1, 12), (2, 5)):
        b.psu.process(f"INST OUT{channel}")
        b.psu.process(f"APPL {volts},1")
        b.psu.process("OUTP:SEL 1")
    b.psu.process("OUTP:GEN 1")
    readings = [float(v) for v in b.daq.process("MEAS:VOLT:DC? (@101,102)").split(",")]
    assert readings == pytest.approx([12, 5], rel=1e-3)


def test_scope_example_bench():
    path = BENCH.parent.parent / "scope" / "bench.yaml"
    if not path.exists():
        pytest.skip("examples not available")
    b = Bench.from_file(path)
    b.psu.process("INST OUT1")
    b.psu.process("APPL 12,1")
    b.psu.process("OUTP:SEL 1")
    b.psu.process("OUTP:GEN 1")
    b.scope.process(":CHAN1:SCAL 2;:CHAN1:OFFS 12;:CHAN2:DISP ON;:CHAN2:OFFS 1.65;:TIM:SCAL 5e-5")
    assert float(b.scope.process(":MEAS:VAV? CHAN1")) == pytest.approx(12, abs=0.2)
    assert float(b.scope.process(":MEAS:FREQ? CHAN2")) == pytest.approx(10000, rel=0.01)
