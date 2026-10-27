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
