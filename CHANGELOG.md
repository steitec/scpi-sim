# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## 0.2.0 - 2026-10-26

### Added

- **Bench**: `scpi-sim serve --bench FILE` starts several instruments from a
  YAML file and wires them together — the multimeter measures, through closed
  multiplexer relays, what the power supply delivers into its loads.
- Loads: resistors (`ohms: 0` is a short), parallel loads and custom Python
  classes with a `current(volts)` curve; the supply finds the CV/CC
  operating point for any of them.
- A relay that would connect two supply outputs is refused with
  `-221,"Settings conflict"`.
- Bench files are checked strictly: duplicate keys, wrong option types and
  unknown terminals are reported with the offending key or line.
- Python loads are imported from the bench file's folder as well.
- **Fault injection**: `no_response`, `delay`, `disconnect`, `error`, `idn`,
  `offset`, `noise` — from Python (`instrument.inject(...)`), from pytest, or
  into a running bench with `scpi-sim fault` through a JSON control port.
- **pytest plugin**: `scpi_bench` and `scpi_bench_factory` fixtures,
  configured with `--scpi-bench FILE` or the `scpi_bench` ini key.
- Examples in `examples/` (bench file and a pytest suite using PyVISA).
- Release workflow for PyPI (Trusted Publishing).

### Changed

- PyYAML is now a runtime dependency.
- `PowerSupply` takes any `load=` object; `load_ohms=` still works.

## 0.1.0 - 2026-10-26

### Added

- Generic multimeter, power supply and multiplexer on raw TCP sockets
  (`scpi-sim serve dmm|psu|mux`).
- SCPI parser with short/long forms, optional nodes, numeric suffixes and the
  SCPI path rule; IEEE 488.2 common commands, error queue and status registers.
- `scpi-sim query`, a small cross-platform client.
- Python API (`InstrumentServer`, `Instrument` with the `@scpi` decorator).

