# scpi-sim

Simulated SCPI lab instruments — a multimeter, a power supply and a relay
multiplexer — that listen on a raw TCP socket like real LAN instruments on
port 5025. Develop and test instrument automation without the hardware.

```console
$ scpi-sim serve psu --load 24 &
scpi-sim: PSU listening on 127.0.0.1:5025 (Ctrl+C to stop)
$ printf '*IDN?\nVOLT 12;CURR 1;:OUTP ON;:MEAS:CURR?\nVOLT 40\nSYST:ERR?\n' | nc -q1 localhost 5025
scpi-sim,PSU,SIM00002,0.1.0
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

### Compared with pyvisa-sim

[pyvisa-sim](https://github.com/pyvisa/pyvisa-sim) is a PyVISA backend that
simulates devices described in YAML, inside your Python process. It is a good
fit for unit tests of PyVISA code. scpi-sim takes a different approach:

| | pyvisa-sim | scpi-sim |
|---|---|---|
| Runs as | PyVISA backend in your process | TCP server, one port per instrument |
| Usable from | Python with PyVISA | any language or tool |
| Devices defined in | YAML | Python classes (generic instruments included) |
| Physical behaviour (CV/CC into a load, ranges, overload) | – | yes |

## Quick start

Requires Python 3.10 or newer. No runtime dependencies.

```bash
git clone <this repository> && cd scpi-sim
pip install -e .            # PyPI release follows with v0.2

scpi-sim serve dmm          # multimeter on 127.0.0.1:5025
```

From another terminal:

```console
$ echo '*IDN?' | nc -q1 localhost 5025
scpi-sim,DMM,SIM00001,0.1.0
```

With PyVISA and the pure-Python backend (`pip install pyvisa pyvisa-py`):

```python
import pyvisa

rm = pyvisa.ResourceManager("@py")
dmm = rm.open_resource("TCPIP0::127.0.0.1::5025::SOCKET",
                       read_termination="\n", write_termination="\n")
print(dmm.query("MEAS:VOLT:DC?"))
```

Run several instruments side by side on different ports:

```bash
scpi-sim serve psu --port 5025 --channels 2 --load 24
scpi-sim serve dmm --port 5026 --volts 3.3
scpi-sim serve mux --port 5027
```

The server listens on `127.0.0.1` by default; use `--host 0.0.0.0` to make it
reachable from other machines.

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
holds it is selected). A reading above 120 % of the range returns
`+9.90000000E+37`.

Options: `--volts 5.0`, `--amps 0.1`, `--ohms 1000` (the simulated input),
`--noise 50e-6` (relative standard deviation, `0` for exact readings),
`--seed N` (reproducible noise).

### Power supply — `scpi-sim serve psu`

| Command | |
|---|---|
| `[SOURce:]VOLTage[:LEVel][:IMMediate][:AMPLitude] <v>` / `…? [MIN\|MAX]` | voltage setpoint, 0–30 V |
| `[SOURce:]CURRent[:LEVel][:IMMediate][:AMPLitude] <a>` / `…? [MIN\|MAX]` | current limit, 0–3 A |
| `OUTPut[n][:STATe] ON\|OFF` / `OUTPut[n][:STATe]?` | output on/off, `n` = channel |
| `OUTPut[n]:MODE?` | `OFF`, `CV` or `CC` |
| `MEASure:VOLTage[:DC]?`, `MEASure:CURRent[:DC]?` | at the output terminals |
| `INSTrument:NSELect <n>` / `?`, `INSTrument[:SELect] CH<n>` / `?` | select channel |

`*RST` state: 0 V, 0.1 A, output off, channel 1 selected. Values accept units
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

- **v0.2** — a bench file that wires instruments together (the multimeter
  measures what the power supply delivers), fault injection (timeouts, no
  response, dropped connections, out-of-tolerance readings), a pytest plugin,
  PyPI release
- **v0.3** — Docker image for CI
- **later** — profiles that behave similar to common bench instruments,
  VXI-11, a simple oscilloscope

## License

MIT
