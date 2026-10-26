"""pytest plugin: a fresh simulated bench for every test.

Fixtures:

- ``scpi_bench`` -- the bench from ``--scpi-bench FILE`` or the ini key
  ``scpi_bench`` (relative to the rootdir); without either, a generic
  ``psu`` + ``dmm`` + ``mux``. Started on free ports, stopped after the test.
- ``scpi_bench_factory`` -- ``factory(path_or_dict)`` for more benches.
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


def _start(source: str | Path | dict) -> Bench:
    try:
        bench = Bench.from_dict(source) if isinstance(source, dict) else Bench.from_file(source)
        bench.start(free_ports=True)
    except BenchError as error:
        pytest.fail(f"scpi-sim bench: {error}", pytrace=False)
    return bench


def _configured(config: pytest.Config) -> Path | dict:
    option = config.getoption("--scpi-bench")
    if option:
        return config.invocation_params.dir / option
    ini = config.getini("scpi_bench")
    if ini:
        return config.rootpath / ini
    return DEFAULT_BENCH


@pytest.fixture
def scpi_bench_factory():
    benches: list[Bench] = []

    def factory(source: str | Path | dict) -> Bench:
        bench = _start(source)
        benches.append(bench)
        return bench

    yield factory
    for bench in benches:
        bench.stop()


@pytest.fixture
def scpi_bench(request: pytest.FixtureRequest):
    bench = _start(_configured(request.config))
    yield bench
    bench.stop()
