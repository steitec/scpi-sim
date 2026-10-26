# scpi-sim

Simulated SCPI lab instruments — a multimeter, a power supply and a relay
multiplexer — that listen on a raw TCP socket like real LAN instruments on
port 5025. Develop and test instrument automation without the hardware.

![Demo: a simulated power supply driven from the command line](docs/demo.gif)

```console
$ scpi-sim serve psu --load 24 &
scpi-sim: PSU listening on 127.0.0.1:5025 (Ctrl+C to stop)
$ scpi-sim query "*IDN?" "VOLT 12;CURR 1;:OUTP ON;:MEAS:CURR?" "VOLT 40" "SYST:ERR?"
scpi-sim,PSU,SIM00002,0.2.0
+5.00000000E-01
-222,"Data out of range"
```

## Why

Test software for lab instruments is usually written while the instruments
are busy, on someone else's bench, or not bought yet — and it is rarely tested
in CI at all. scpi-sim gives you instruments that:

- **are real network servers** — anything that can open a TCP socket can talk
  to them: PyVISA (`TCPIP0::127.0.0.1::5025::SOCKET`), C#, LabVIEW, NI MAX,
  `nc`, your own code
- **keep state** like the real thing — settings persist, `*RST` resets them,
  errors go to the error queue, status registers (`*ESR?`, `*STB?`) work
- **behave physically** — the power supply goes from constant voltage into
  current limit when its load draws too much, the multimeter has ranges and
  reports overload (`+9.90000000E+37`) when a reading does not fit
- **are strict where real instruments are strict** — short/long forms only,
  the SCPI path rule in compound commands, standard error codes — so bugs in
  your command strings show up before they meet the hardware
- **work together on a bench** — wire a supply, a multiplexer and a
  multimeter in a YAML file, and the multimeter measures what the supply
  delivers into its load
- **break on purpose** — inject timeouts, dropped connections, wrong
  identities or drifting readings at runtime, and see whether your test code
  copes

### Compared with pyvisa-sim

[pyvisa-sim](https://github.com/pyvisa/pyvisa-sim) is a PyVISA backend that
simulates devices described in YAML, inside your Python process. It is a good
fit for unit tests of PyVISA code. scpi-sim takes a different approach:

| | pyvisa-sim | scpi-sim |
|---|---|---|
| Runs as | PyVISA backend in your process | TCP server, one port per instrument |
| Usable from | Python with PyVISA | any language or tool |
| Devices defined in | YAML | Python classes (generic instruments included) |

On top of that, scpi-sim has physical behaviour (CV/CC into a load, ranges,
overload), instruments wired together on a bench, fault injection at runtime
and a pytest plugin.

## Quick start

Requires Python 3.10 or newer; the only dependency is PyYAML.

```bash
git clone https://github.com/steitec/scpi-sim.git && cd scpi-sim
pip install -e .

scpi-sim serve dmm          # multimeter on 127.0.0.1:5025
```

From another terminal, with the built-in client (works on Linux, macOS and
Windows):

```console
$ scpi-sim query "*IDN?"
scpi-sim,DMM,SIM00001,0.2.0
```

`scpi-sim query [--host HOST] [--port PORT] MESSAGE...` sends each message
and prints the answer to every message that contains a `?`.

Any raw TCP client works as well, e.g. `nc`:

```bash
echo '*IDN?' | nc -q1 localhost 5025    # Linux (netcat-openbsd)
echo '*IDN?' | nc -N localhost 5025     # macOS
```

Every message must end with a newline (`\n`), as on real LAN instruments:
`printf '*IDN?' | nc …` without one gets no answer.

With PyVISA and the pure-Python backend (`pip install pyvisa pyvisa-py`):

```python
import pyvisa

rm = pyvisa.ResourceManager("@py")
dmm = rm.open_resource("TCPIP0::127.0.0.1::5025::SOCKET",
                       read_termination="\n", write_termination="\n")
print(dmm.query("MEAS:VOLT:DC?"))
```

Run several instruments side by side on different ports (`scpi-sim query
--port 5026 …` talks to the second one):

```bash
scpi-sim serve psu --port 5025 --channels 2 --load 24
scpi-sim serve dmm --port 5026 --volts 3.3
scpi-sim serve mux --port 5027
```

The server listens on `127.0.0.1` by default; use `--host 0.0.0.0` to make it
reachable from other machines. A host name that resolves to several addresses
(`localhost` → `127.0.0.1` and `::1`) is served on all of them, on the same
port.

## Instruments

All instruments support the IEEE 488.2 common commands `*IDN?`, `*RST`,
`*CLS`, `*ESR?`, `*ESE`, `*ESE?`, `*SRE`, `*SRE?`, `*STB?`, `*OPC`, `*OPC?`,
`*WAI`, `*TST?` and `SYSTem:ERRor[:NEXT]?`, `SYSTem:ERRor:COUNt?`,
`SYSTem:VERSion?`.

### Multimeter — `scpi-sim serve dmm`

| Command | |
|---|---|
| `MEASure:VOLTage[:DC]? [range]` | DC voltage |
| `MEASure:CURRent[:DC]? [range]` | DC current |
| `MEASure:RESistance? [range]` | 2-wire resistance |
| `CONFigure:VOLTage[:DC] [range]`, `CONFigure:CURRent[:DC] [range]`, `CONFigure:RESistance [range]` | select function and range |
| `CONFigure?` | e.g. `"VOLT AUTO"`, `"RES +1.00000000E+04"` |
| `READ?` | measure with the current configuration |

Ranges: 0.1/1/10/100/1000 V, 0.01/0.1/1/3/10 A, 100 Ω … 100 MΩ in decades.
`range` is `AUTO`, `DEF`, `MIN`, `MAX` or a value (the smallest range that
holds its magnitude is selected); an optional second parameter (resolution)
is accepted. A reading above 120 % of the range returns `+9.90000000E+37`
(`-9.90000000E+37` for negative readings).

Options: `--volts 5.0`, `--amps 0.1`, `--ohms 1000` (the simulated input),
`--noise 50e-6` (relative standard deviation, `0` for exact readings),
`--seed N` (reproducible noise).

### Power supply — `scpi-sim serve psu`

| Command | |
|---|---|
| `[SOURce[n]:]VOLTage[:LEVel][:IMMediate][:AMPLitude] <v>` / `…? [MIN\|MAX\|DEF]` | voltage setpoint, 0–30 V |
| `[SOURce[n]:]CURRent[:LEVel][:IMMediate][:AMPLitude] <a>` / `…? [MIN\|MAX\|DEF]` | current limit, 0–3 A |
| `OUTPut[n][:STATe] ON\|OFF` / `OUTPut[n][:STATe]?` | output on/off, `n` = channel |
| `OUTPut[n]:MODE?` | `OFF`, `CV` or `CC` |
| `MEASure:VOLTage[:DC]?`, `MEASure:CURRent[:DC]?` | at the output terminals |
| `INSTrument:NSELect <n>` / `?`, `INSTrument[:SELect] CH<n>` / `?` | select channel |

`SOURce<n>` and `OUTPut<n>` address channel `n` directly; without a number
the channel selected with `INSTrument` is used. `*RST` state: 0 V, 0.1 A,
output off, channel 1 selected. Values accept units
(`500 mV`, `250 mA`).

Options: `--channels 1`, `--load OHMS` (resistive load on every output;
without it the outputs are open and deliver no current; `--load 0` is a short
circuit, which drives the output straight into the current limit).

### Multiplexer — `scpi-sim serve mux`

| Command | |
|---|---|
| `ROUTe:CLOSe (@101,103:105)` | close channels |
| `ROUTe:OPEN (@…)`, `ROUTe:OPEN:ALL` | open channels |
| `ROUTe:CLOSe? (@…)`, `ROUTe:OPEN? (@…)` | one `1`/`0` per channel |
| `ROUTe:CLOSe:STATe?` | closed channels, e.g. `(@101,105)` |

Channels are numbered 101, 102, … A list with an invalid channel is rejected
as a whole. Options: `--channels 16` (1–99).

## Bench

A bench file starts several instruments at once and wires them together:

```yaml
# examples/bench.yaml
instruments:
  psu: {type: psu, port: 5025, channels: 2}
  dmm: {type: dmm, port: 5026}
  mux: {type: mux, port: 5027, channels: 16}
loads:
  rail_12v: {type: resistor, ohms: 24}      # 0.5 A at 12 V
  rail_3v3: {type: resistor, ohms: 33}      # 0.1 A at 3.3 V
wiring:
  - psu.ch1 -> rail_12v
  - psu.ch2 -> rail_3v3
  - mux.101 -> rail_12v
  - mux.102 -> rail_3v3
  - mux.common -> dmm.input
control: 5099
```

```console
$ scpi-sim serve --bench examples/bench.yaml
scpi-sim: psu (PSU) listening on 127.0.0.1:5025
scpi-sim: dmm (DMM) listening on 127.0.0.1:5026
scpi-sim: mux (MUX) listening on 127.0.0.1:5027
scpi-sim: control on 127.0.0.1:5099
scpi-sim: bench running (Ctrl+C to stop)
```

```console
$ scpi-sim query "SOUR1:VOLT 12;:SOUR1:CURR 1;:OUTP1 ON;*OPC?"
1
$ scpi-sim query --port 5027 "ROUT:CLOS (@101);*OPC?"
1
$ scpi-sim query --port 5026 "MEAS:VOLT?"
+1.20000310E+01
$ scpi-sim query "INST:NSEL 1;:MEAS:CURR?"
+5.00000000E-01
```

- **Instruments**: `type` is `psu`, `dmm` or `mux`, `port` is the TCP port
  (`0` = any free port); the other keys are the options of that instrument
  (`psu`: `channels`, `max_volts`, `max_amps`; `dmm`: `volts`, `amps`, `ohms`,
  `noise`, `seed`; `mux`: `channels`).
- **Wiring**: each line `A -> B` connects two terminals: `<psu>.ch<n>`,
  `<dmm>.input`, `<mux>.<channel>`, `<mux>.common` or a node name of your
  choice. A closed multiplexer relay connects its channel to `common`.
- **Loads** sit on nodes: `{type: resistor, ohms: 24}` (`ohms: 0` is a short),
  or your own Python class with a `current(volts)` method, e.g. a diode or a
  device that draws constant current:
  `{type: python, class: "mypackage.loads:Lamp", args: {amps: 0.3}}`. The
  supply finds the operating point (constant voltage, or current limit) for
  any such load.
- The multimeter measures the voltage of the net its input is on (0 V if
  nothing drives it) and the resistance of the resistors on it.
- Two supply outputs never end up on the same net: in the file that is an
  error, and a `ROUT:CLOS` that would connect them is refused with
  `-221,"Settings conflict"`.
- Mistakes in the file are reported with the offending line and exit code 2.

Every instrument has its own connection, so a command sent to one
instrument can still be in flight when you query another one. Finish
commands with `*OPC?` before measuring elsewhere, as you would on a real
bench (`ROUT:CLOS (@101);*OPC?`).

## Fault injection

Faults are injected at runtime — from Python, from pytest, or into a running
bench through its control port:

```console
$ scpi-sim fault dmm noise sigma=0.05
ok
$ scpi-sim fault psu no_response count=2
ok
$ scpi-sim fault --list
psu: no_response count=2
dmm: noise sigma=0.05
mux: -
$ scpi-sim fault dmm --clear
ok
```

| Fault | Parameters | Effect |
|---|---|---|
| `no_response` | `count=1` | the next `count` answers are not sent (the commands still execute) |
| `delay` | `seconds`, `count` (default: until cleared) | answers are sent `seconds` later |
| `disconnect` | — | the next message closes the connection |
| `error` | `code`, `message` | one entry is pushed into the error queue |
| `idn` | `value` | `*IDN?` answers `value` |
| `offset` | `value` | multimeter: added to every reading |
| `noise` | `sigma` | multimeter: replaces the noise of the readings |

Faults stay active until cleared (`*RST` and `*CLS` do not clear them) and
belong to the instrument, not to a connection. From Python:
`bench.dmm.inject("offset", value=0.2)`, `bench.dmm.clear_faults()`.

The control port speaks one JSON object per line:
`{"instrument": "dmm", "inject": "noise", "params": {"sigma": 0.05}}`,
`{"instrument": "dmm", "clear": true}`, `{"list": true}`.

## pytest plugin

Installing scpi-sim registers a pytest plugin. The `scpi_bench` fixture
starts a fresh bench for every test, on free ports, and stops it afterwards:

```python
import pyvisa
import pytest


def test_dead_multimeter_raises_timeout(scpi_bench):
    rm = pyvisa.ResourceManager("@py")
    dmm = rm.open_resource(scpi_bench.resource("dmm"), read_termination="\n",
                           write_termination="\n", timeout=500)
    scpi_bench.dmm.inject("no_response")
    with pytest.raises(pyvisa.errors.VisaIOError):
        dmm.query("MEAS:VOLT?")
    dmm.close()
    rm.close()
```

- The bench comes from `--scpi-bench FILE`, or from the ini key
  `scpi_bench = FILE` (relative to the rootdir); without either it is a
  generic `psu`, `dmm` and `mux`, not wired.
- `scpi_bench.resource(name)` gives the VISA resource string,
  `scpi_bench.port(name)` the port, `scpi_bench.<name>` the instrument object.
- `scpi_bench_factory(path_or_dict)` starts further benches.

[`examples/test_example.py`](examples/test_example.py) is a complete example:
`pytest examples --scpi-bench examples/bench.yaml`.

## SCPI behaviour

- Keywords match in short or long form, case-insensitive (`MEAS`,
  `measure`) — but nothing in between (`MEASU` is an error), like on real
  instruments. Optional nodes in `[ ]` may be left out.
- Compound commands follow the SCPI path rule: after `;` a command continues
  at the level of the previous one, so `SOUR:VOLT 5;CURR 1` sets
  `SOUR:CURR`. Start with `:` to go back to the root:
  `MEAS:VOLT?;:MEAS:CURR?` works, `MEAS:VOLT?;MEAS:CURR?` gives
  `-113,"Undefined header"` — exactly as a real instrument would.
- Several queries in one message are answered in one line, separated by `;`.
- `*RST` resets the instrument settings; `*CLS` clears the error queue and
  the event status register. The error queue holds 20 entries; on overflow
  the last one becomes `-350,"Queue overflow"`.

Error codes:

| Code | Message |
|---|---|
| -102 | Syntax error |
| -104 | Data type error |
| -108 | Parameter not allowed (more parameters than the command takes) |
| -109 | Missing parameter |
| -113 | Undefined header |
| -114 | Header suffix out of range |
| -131 | Invalid suffix |
| -222 | Data out of range |
| -223 | Too much data (line longer than 64 KiB) |
| -224 | Illegal parameter value |
| -300 | Device-specific error |
| -350 | Queue overflow |

## Python API

Start instruments from your own tests, on a free port:

```python
import pyvisa

from scpi_sim import InstrumentServer, PowerSupply

with InstrumentServer(PowerSupply(load_ohms=24), port=0) as server:
    rm = pyvisa.ResourceManager("@py")
    psu = rm.open_resource(f"TCPIP0::127.0.0.1::{server.port}::SOCKET",
                           read_termination="\n", write_termination="\n")
    psu.write("VOLT 12;CURR 1;:OUTP ON")
    print(psu.query("MEAS:CURR?"))              # +5.00000000E-01
    psu.write("CURR 0.25")
    print(psu.query("MEAS:VOLT?;:OUTP:MODE?"))  # +6.00000000E+00;CC
```

The simulated multimeter input can be changed from Python while a test runs:
`dmm = Multimeter(noise=0)`, then `dmm.volts = 1.8`.

A bench from Python:

```python
from scpi_sim import Bench

with Bench.from_file("examples/bench.yaml") as bench:   # starts all servers
    print(bench.resource("dmm"))                         # TCPIP0::127.0.0.1::5026::SOCKET
    bench.psu.process("SOUR1:VOLT 12;:SOUR1:CURR 1;:OUTP1 ON")
    bench.mux.process("ROUT:CLOS (@101)")
    print(bench.dmm.process("MEAS:VOLT?"))               # about +1.2000E+01
```

Your own instrument is a subclass of `Instrument` with one method per
command:

```python
from scpi_sim import Instrument, scpi

class Thermometer(Instrument):
    model, serial = "THERMO", "SIM00042"

    @scpi("MEASure:TEMPerature?")
    def measure(self, call):
        return 23.5
```

## Roadmap

- **v0.3** — Docker image for CI
- **later** — profiles that behave similar to common bench instruments,
  VXI-11, a simple oscilloscope

## License

MIT
