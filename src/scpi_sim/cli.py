"""Command line interface: ``scpi-sim serve dmm|psu|mux``."""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from . import __version__
from .instrument import Instrument
from .instruments import Multimeter, Multiplexer, PowerSupply
from .transport import InstrumentServer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scpi-sim", description="Simulated SCPI lab instruments on a raw TCP socket.")
    parser.add_argument("--version", action="version", version=f"scpi-sim {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve one simulated instrument")
    kinds = serve.add_subparsers(dest="kind", required=True, metavar="INSTRUMENT")

    def kind(name: str, help: str) -> argparse.ArgumentParser:
        p = kinds.add_parser(name, help=help)
        p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
        p.add_argument("--port", type=int, default=5025, help="TCP port (default: 5025, 0 = any free port)")
        return p

    dmm = kind("dmm", "digital multimeter")
    dmm.add_argument("--volts", type=float, default=5.0, help="simulated DC voltage at the input")
    dmm.add_argument("--amps", type=float, default=0.1, help="simulated DC current at the input")
    dmm.add_argument("--ohms", type=float, default=1000.0, help="simulated resistance at the input")
    dmm.add_argument("--noise", type=float, default=50e-6, help="relative standard deviation of readings")
    dmm.add_argument("--seed", type=int, help="random seed for reproducible noise")

    psu = kind("psu", "DC power supply")
    psu.add_argument("--channels", type=int, default=1, help="number of output channels")
    psu.add_argument("--load", type=float, metavar="OHMS", help="resistive load on every output (default: open)")

    mux = kind("mux", "relay multiplexer (channels 101...)")
    mux.add_argument("--channels", type=int, default=16, help="number of channels, 1-99")
    return parser


def _instrument(args: argparse.Namespace) -> Instrument:
    if args.kind == "dmm":
        return Multimeter(volts=args.volts, amps=args.amps, ohms=args.ohms, noise=args.noise, seed=args.seed)
    if args.kind == "psu":
        return PowerSupply(channels=args.channels, load_ohms=args.load)
    return Multiplexer(channels=args.channels)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        instrument = _instrument(args)
    except ValueError as error:
        parser.error(str(error))

    server = InstrumentServer(instrument, host=args.host, port=args.port)
    try:
        server.start()
    except OSError as error:
        print(f"scpi-sim: error: cannot listen on {args.host}:{args.port}: {error.strerror or error}",
              file=sys.stderr)
        return 1

    stop = threading.Event()
    # Install SIGINT explicitly: a shell starts "scpi-sim serve ... &" with SIGINT ignored.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    print(f"scpi-sim: {instrument.model} listening on {args.host}:{server.port} (Ctrl+C to stop)", flush=True)
    while not stop.wait(0.5):
        pass
    server.stop()
    print("scpi-sim: stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
