"""Command line interface: ``scpi-sim serve dmm|psu|mux``."""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading

from . import __version__
from .instrument import Instrument
from .instruments import Multimeter, Multiplexer, PowerSupply
from .transport import InstrumentServer


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"invalid port {text!r} (allowed: 0-65535)")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scpi-sim", description="Simulated SCPI lab instruments on a raw TCP socket.")
    parser.add_argument("--version", action="version", version=f"scpi-sim {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve one simulated instrument")
    kinds = serve.add_subparsers(dest="kind", required=True, metavar="INSTRUMENT")

    def kind(name: str, help: str) -> argparse.ArgumentParser:
        p = kinds.add_parser(name, help=help)
        p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
        p.add_argument("--port", type=_port, default=5025, help="TCP port (default: 5025, 0 = any free port)")
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

    q = commands.add_parser("query", help="send SCPI messages to an instrument and print the answers")
    q.add_argument("messages", nargs="+", metavar="MESSAGE", help='e.g. "*IDN?" "VOLT 5" "MEAS:VOLT?"')
    q.add_argument("--host", default="127.0.0.1", help="instrument address (default: 127.0.0.1)")
    q.add_argument("--port", type=_port, default=5025, help="TCP port (default: 5025)")
    q.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for an answer (default: 2)")
    return parser


def _query(args: argparse.Namespace) -> int:
    """A minimal cross-platform client, so the examples do not depend on nc."""
    try:
        sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    except OSError as error:
        print(f"scpi-sim: error: cannot connect to {args.host}:{args.port}: {error.strerror or error}",
              file=sys.stderr)
        return 1
    with sock, sock.makefile("rwb") as stream:
        for message in args.messages:
            stream.write(message.encode() + b"\n")
            stream.flush()
            if "?" not in message:
                continue
            try:
                answer = stream.readline()
            except OSError:  # socket timeout
                answer = b""
            if not answer:
                print(f"scpi-sim: error: no response to {message!r} (check SYST:ERR?)", file=sys.stderr)
                return 1
            print(answer.decode(errors="replace").rstrip("\r\n"), flush=True)
    return 0


def _instrument(args: argparse.Namespace) -> Instrument:
    if args.kind == "dmm":
        return Multimeter(volts=args.volts, amps=args.amps, ohms=args.ohms, noise=args.noise, seed=args.seed)
    if args.kind == "psu":
        return PowerSupply(channels=args.channels, load_ohms=args.load)
    return Multiplexer(channels=args.channels)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "query":
        return _query(args)
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
