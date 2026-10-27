# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## 0.3.1 - 2026-10-27

### Fixed

- A device whose `current()` calls `input_voltage()` now gets a clear
  `RuntimeError` (`-300` on the instrument) instead of a `RecursionError`.
- Console: output for a client that doesn't read is dropped once 256 KiB are
  queued for it, instead of piling up in memory without limit.

### Changed

- CI and release workflows use the current major versions of the GitHub
  Actions (Node.js 24).

## 0.3.0 - 2026-10-26

### Added

- **Devices**: your own device under test as a Python class
  (`scpi_sim.Device`) with input terminals that draw current and output
  terminals that drive their net; `update()` after every instrument message
  lets it react to power-on (inrush, boot timelines). Configured under
  `devices:` in the bench file.
- **Device console**: a raw TCP port per device (`console:` in the bench
  file), broadcast to every client, typed lines go to `console_line()`;
  pyserial reaches it as `socket://host:port` (`Bench.console_url()`).
- Example device in `examples/devices` (sensor board with boot log and
  console) and the README section "Writing your own device".
- **Docker image**: `Dockerfile` (non-root, example bench by default); CI
  builds and smoke-tests it, releases publish `ghcr.io/steitec/scpi-sim`.

### Changed

- `InstrumentServer` is now built on a small `TcpServer` base class shared
  with the console server.
- `Instrument.listeners`: callables run after every message, outside the
  instrument lock (the bench uses it to update devices). A `no_response`
  fault now also runs them.
- `Bench.stop()` stops every server even if one of them fails.

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
- The control port is refused if it clashes with an instrument port, closes
  its connections when the bench stops and works on IPv6 (`--host ::1`).
- The source distribution contains the tests, examples and changelog and can
  run its own test suite.
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

