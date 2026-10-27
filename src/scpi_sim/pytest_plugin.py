"""pytest plugin: a fresh simulated bench for every test.

Fixtures:

- ``scpi_bench`` -- the bench from ``--scpi-bench FILE`` or the ini key
  ``scpi_bench`` (relative to the rootdir); without either, a generic
  ``psu`` + ``dmm`` + ``mux``. Started on free ports, stopped after the test.
  Instruments in the bench may be profiles of real models (``profile: rs-hmp4040``).
- ``scpi_bench_factory`` -- ``factory(path_or_dict)`` for more benches;
  relative paths are relative to the rootdir, like the ini key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .bench import Bench, BenchError

DEFAULT_BENCH = {
    "instruments": {
        "psu": {"type": "psu", "port": 0},
        "dmm": {"type": "dmm", "port": 0},
        "mux": {"type": "mux", "port": 0},
    }
}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("scpi-sim")
    group.addoption("--scpi-bench", metavar="FILE", help="bench file (YAML) for the scpi_bench fixture")
    parser.addini("scpi_bench", "bench file (YAML) for the scpi_bench fixture, relative to the rootdir")


class BenchPool:
    """Benches started for one test; all of them are stopped at the end."""

    def __init__(self, root: Path | None):
        self.root = root
        self.benches: list[Bench] = []

    def start(self, source: str | Path | dict) -> Bench:
        if not isinstance(source, dict):
            source = Path(source)
            if self.root is not None and not source.is_absolute():
                source = self.root / source
        try:
            bench = Bench.from_dict(source) if isinstance(source, dict) else Bench.from_file(source)
            bench.start(free_ports=True)
        except BenchError as error:
            pytest.fail(f"scpi-sim bench: {error}", pytrace=False)
        self.benches.append(bench)
        return bench

    def stop_all(self) -> None:
        """Stop every bench; re-raise the first failure afterwards."""
        failure = None
        for bench in self.benches:
            try:
                bench.stop()
            except Exception as error:  # keep stopping the others
                failure = failure or error
        self.benches.clear()
        if failure is not None:
            raise failure


def _configured(config: pytest.Config) -> Path | dict:
    option = config.getoption("--scpi-bench")
    if option:
        return config.invocation_params.dir / option
    ini = config.getini("scpi_bench")
    if ini:
        return config.rootpath / ini
    return DEFAULT_BENCH


@pytest.fixture
def scpi_bench_factory(request: pytest.FixtureRequest):
    pool = BenchPool(request.config.rootpath)
    yield pool.start
    pool.stop_all()


@pytest.fixture
def scpi_bench(request: pytest.FixtureRequest):
    pool = BenchPool(None)  # _configured() already resolved the path
    yield pool.start(_configured(request.config))
    pool.stop_all()
