# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## 0.4.2 - 2026-10-28

### Added

- Browser demo in `web/` (live at <https://scpi-sim.steitec.net>): the
  `examples/profiles/bench.yaml` bench (HMP4040 and DAQ970A) runs in Pyodide
  in the page, with a SCPI terminal, a guided tour and fault buttons. The
  package itself is unchanged; `web/` is not part of the distribution.

## 0.4.1 - 2026-10-27

### Fixed

- `keysight-daq970a`: `ROUT:OPEN` on a card that has channels in the scan list
  is refused with -221, like `ROUT:CLOS` (the manual refuses manual switching
  there).
- `keysight-daq970a`: `MEAS:VOLT:DC? (@)` and `MEAS:RES? (@)` with an empty
  channel list give -221 instead of an empty answer; the scan list stays as it
  was.
- `keysight-daq970a`, `keithley-daq6510`: injecting or clearing a fault waits
  for a running command, so a fault never changes in the middle of a scan.
- `siglent-spd3303x`: the IEEE 488.2 common commands its manual does not list
  (`*RST`, `*CLS`, `*ESR?`, `*ESE`, `*SRE`, `*STB?`, `*OPC`, `*OPC?`, `*WAI`,
  `*TST?`) give -113, like other commands the instrument does not have.
- `keysight-34461a`: resolution `MIN`/`MAX` in `CONF`/`MEAS` select the best
  (0.3 ppm of range) and worst (100 ppm) resolution from the manual's
  Resolution Table instead of the default.
- `keysight-34461a`: the default resolution is 1 ppm of the range, the
  34461A default in the manual's Resolution Table, instead of the 0.3 ppm from
  the generic `CONF?` example; `CONF?` after `CONF:VOLT:DC 10` now answers
  `"VOLT +1.00000000E+01,+1.00000000E-05"`.
- Bench files: a `profile:` that is not text (a list, a number) gives
  "'profile' must be text" instead of a Python error message.

## 0.4.0 - 2026-10-27

### Added

- **Profiles**: ten instruments that answer like real models — `rs-hmp4040`,
  `rs-nge103b`, `keysight-e36312a`, `siglent-spd3303x` (power supplies),
  `rs-hmc8012`, `keysight-34461a`, `keithley-dmm6500`, `siglent-sdm3065x`
  (multimeters), `keysight-daq970a`, `keithley-daq6510` (multiplexers with a
  built-in multimeter). Each follows the manufacturer's programming manual for
  the commonly used commands; not verified on real hardware.
- Bench files: `profile:` on an instrument, `serial:` on any instrument.
- `scpi-sim serve --profile NAME [--port] [--load]` and `scpi-sim profiles`.
- `scpi_sim.PROFILES` and `scpi_sim.get_profile()`.
- Example `examples/profiles/` (an HMP4040 and a DAQ970A driven by PyVISA).

### Changed

- Groundwork for profiles, backward compatible (instruments without a profile
  answer exactly as before): `Instrument` has `manufacturer`/`firmware` identity
  fields, a `serial` argument and a `format_number()` hook; `PowerSupply` takes
  per-channel `limits`; `Multimeter` ranges are per class (`RANGES`);
  `Multiplexer` takes any set of `valid_channels`.
- The error queue knows `-230` ("Data corrupt or stale").

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

