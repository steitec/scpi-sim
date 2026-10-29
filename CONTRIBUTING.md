# Contributing to scpi-sim

Thanks for your interest! Bug reports, profile corrections from owners of the
real instruments, and pull requests are all welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and asking for features

Open an [issue](https://github.com/steitec/scpi-sim/issues/new/choose) and pick
a template:

- **Bug report** — something crashes or behaves differently than documented.
- **Instrument behaviour mismatch** — a profile answers differently than the
  real instrument. A transcript from the real device (command and answer) helps
  most.
- **Feature request** — a new instrument, protocol feature or fault.

Security problems do not belong in public issues; see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/steitec/scpi-sim.git
cd scpi-sim
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Running the tests

CI runs the same commands on Linux, Windows and macOS (Python 3.10 and 3.13):

```bash
pytest -v
pytest -v examples/test_example.py --scpi-bench examples/bench.yaml
pytest -v examples/devices --scpi-bench examples/devices/bench.yaml
pytest -v examples/profiles --scpi-bench examples/profiles/bench.yaml
pytest -v web/tests
```

The browser demo in `web/` has its own Playwright tests (`npm ci && npx
playwright test` in `web/`, against the demo container); CI runs them too.

If your machine runs `rpcbind` on port 111, start VXI-11 tests with a different
port mapper port (`--portmapper-port`).

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep the change focused; one topic per pull request.
3. Add or update tests for new behaviour and bug fixes.
4. Update `README.md` and `CHANGELOG.md` (an `## Unreleased` section at the top) when users
   will notice the change.
5. Make sure all tests pass, then open the pull request and fill in the
   template.

Style: follow the surrounding code — plain Python, standard library where
possible (the only runtime dependency is PyYAML), short docstrings, no type
gymnastics.

## Adding or correcting an instrument profile

Profiles live in `src/scpi_sim/profiles/`. Please cite where the behaviour
comes from (programming manual section, or a transcript from a real
instrument with its firmware version), and add a test in `tests/profiles/` that sends
the commands and checks the answers.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
