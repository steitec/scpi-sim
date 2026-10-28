# Browser demo

The interactive demo at <https://scpi-sim.steitec.net>: the example bench from
`examples/profiles/bench.yaml` (R&S HMP4040 supply, Keysight DAQ970A) running
in the browser. The real scpi-sim code runs in [Pyodide](https://pyodide.org);
the site is static, with no backend and no requests to other hosts.

This directory is not part of the PyPI package.

| Path | What it is |
|---|---|
| `py/demo.py` | the Python layer: sends messages, injects faults, returns the instrument state |
| `site/` | the page (HTML, CSS, JavaScript; no framework, no bundler) |
| `build_site.py` | builds `web/dist`: page, `demo.py`, bench, a wheel of this checkout, Pyodide (pinned by SHA-256) |
| `nginx.conf`, `Dockerfile` | the container that serves `web/dist` |
| `tests/` | pytest for `demo.py` and the build, Playwright for the page |

## Build and run

```bash
pip install -e ".[dev]"
python web/build_site.py                 # -> web/dist
python -m http.server -d web/dist 8000   # quick look, without the nginx headers

docker build -f web/Dockerfile -t scpi-sim-web .   # from the repository root
docker run --rm -p 8080:80 scpi-sim-web            # http://localhost:8080
```

## Test

```bash
pytest web/tests                       # Python layer and build
cd web && npm ci && npx playwright install chromium firefox
npx playwright test                    # against http://localhost:8080 (DEMO_URL to change it)
```
