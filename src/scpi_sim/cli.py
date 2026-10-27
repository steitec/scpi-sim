"""Command line interface: ``scpi-sim serve``, ``query`` and ``fault``."""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading

from . import __version__
from .bench import Bench, BenchError
from .control import send_control
from .instrument import Instrument
from .instruments import Multimeter, Multiplexer, PowerSupply
from .profiles import PROFILES, get_profile
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
    serve = commands.add_parser("serve", help="serve one simulated instrument, or a whole bench")
    serve.add_argument("--bench", metavar="FILE", help="serve all instruments of a bench file (YAML)")
    serve.add_argument("--profile", metavar="NAME", help="serve one instrument that behaves like a real model "
                                                         "(see: scpi-sim profiles)")
    serve.add_argument("--host", dest="bench_host", default="127.0.0.1",
                       help="with --bench or --profile: address to listen on (default: 127.0.0.1)")
    serve.add_argument("--port", dest="profile_port", type=_port,
                       help="with --profile: TCP port (default: 5025, 0 = any free port)")
    serve.add_argument("--load", dest="profile_load", type=float, metavar="OHMS",
                       help="with --profile of a power supply: resistive load on every output")
    kinds = serve.add_subparsers(dest="kind", metavar="INSTRUMENT")

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

    commands.add_parser("profiles", help="list the instrument profiles (real models)")
    q = commands.add_parser("query", help="send SCPI messages to an instrument and print the answers")
    q.add_argument("messages", nargs="+", metavar="MESSAGE", help='e.g. "*IDN?" "VOLT 5" "MEAS:VOLT?"')
    q.add_argument("--host", default="127.0.0.1", help="instrument address (default: 127.0.0.1)")
    q.add_argument("--port", type=_port, default=5025, help="TCP port (default: 5025)")
    q.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for an answer (default: 2)")

    f = commands.add_parser("fault", help="inject or clear faults in a running bench (via its control port)")
    f.add_argument("instrument", nargs="?", help="instrument name from the bench file")
    f.add_argument("kind", nargs="?", help="no_response, delay, disconnect, error, idn, offset, noise")
    f.add_argument("params", nargs="*", metavar="KEY=VALUE", help="fault parameters, e.g. sigma=0.05")
    f.add_argument("--clear", action="store_true", help="clear all faults of the instrument")
    f.add_argument("--list", action="store_true", help="list the active faults of all instruments")
    f.add_argument("--host", default="127.0.0.1", help="bench address (default: 127.0.0.1)")
    f.add_argument("--control-port", type=_port, default=5099, help="control port (default: 5099)")
    return parser


def _fault_request(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    if args.list:
        if args.instrument or args.clear:
            parser.error("--list takes no other arguments")
        return {"list": True}
    if not args.instrument or bool(args.kind) == args.clear:
        parser.error("fault needs INSTRUMENT and either KIND [KEY=VALUE ...] or --clear")
    if args.clear:
        if args.params:
            parser.error("--clear takes no parameters")
        return {"instrument": args.instrument, "clear": True}
    params = {}
    for item in args.params:
        key, sep, value = item.partition("=")
        if not sep or not key:
            parser.error(f"parameter {item!r} is not KEY=VALUE")
        params[key] = value  # sent as text; the bench converts per parameter (idn value=007 stays "007")
    return {"instrument": args.instrument, "inject": args.kind, "params": params}


def _fault(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    request = _fault_request(parser, args)
    try:
        answer = send_control(request, host=args.host, port=args.control_port)
    except OSError as error:
        print(f"scpi-sim: error: cannot connect to control port {args.host}:{args.control_port}: "
              f"{getattr(error, 'strerror', None) or error}", file=sys.stderr)
        return 1
    if not answer.get("ok"):
        print(f"scpi-sim: error: {answer.get('error')}", file=sys.stderr)
        return 1
    if "faults" in answer:
        for name, faults in answer["faults"].items():
            text = "; ".join(kind + "".join(f" {k}={v}" for k, v in params.items() if v is not None)
                             for kind, params in faults.items())
            print(f"{name}: {text or '-'}")
    else:
        print("ok")
    return 0


def _stop_event() -> threading.Event:
    """Set on SIGINT/SIGTERM. Install before announcing the server, so no signal is lost."""
    stop = threading.Event()
    # Install SIGINT explicitly: a shell starts "scpi-sim serve ... &" with SIGINT ignored.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    return stop


def _wait(stop: threading.Event) -> None:
    while not stop.wait(0.5):
        pass


def _serve_bench(args: argparse.Namespace) -> int:
    try:
        bench = Bench.from_file(args.bench)
    except BenchError as error:
        print(f"scpi-sim: error: {error}", file=sys.stderr)
        return 2
    host = args.bench_host
    try:
        bench.start(host=host)
    except OSError as error:
        print(f"scpi-sim: error: cannot start bench on {host}: {error.strerror or error}", file=sys.stderr)
        return 1
    stop = _stop_event()
    for name, instrument in bench.instruments.items():
        print(f"scpi-sim: {name} ({instrument.model}) listening on {host}:{bench.port(name)}")
    for name in bench.devices:
        try:
            print(f"scpi-sim: {name} console on {host}:{bench.console_port(name)}")
        except KeyError:  # device without console
            pass
    if bench.control_port is not None:
        print(f"scpi-sim: control on {host}:{bench.control_port}")
    print("scpi-sim: bench running (Ctrl+C to stop)", flush=True)
    _wait(stop)
    bench.stop()
    print("scpi-sim: stopped", flush=True)
    return 0


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
    if args.command == "fault":
        return _fault(parser, args)
    if args.command == "profiles":
        width = max(map(len, PROFILES))
        for name, cls in PROFILES.items():
            print(f"{name:<{width}}  {cls.kind}  {cls.description}")
        return 0
    if [bool(args.bench), bool(args.kind), bool(args.profile)].count(True) != 1:
        parser.error("serve needs an instrument (dmm, psu, mux), --profile NAME or --bench FILE")
    if not args.profile and (args.profile_port is not None or args.profile_load is not None):
        parser.error("--port and --load before the instrument only work with --profile "
                     "(for an instrument, put them after it: serve psu --port 5025)")
    if args.bench:
        return _serve_bench(args)
    if args.profile:
        try:
            cls = get_profile(args.profile)
        except KeyError as error:
            parser.error(error.args[0])
        if args.profile_load is not None and cls.kind != "psu":
            parser.error(f"--load only works with a power supply profile, {args.profile} is a {cls.kind}")
        try:
            instrument = cls(load_ohms=args.profile_load) if args.profile_load is not None else cls()
        except ValueError as error:
            parser.error(str(error))
        port = 5025 if args.profile_port is None else args.profile_port
        host, label = args.bench_host, f"{args.profile} ({instrument.model})"
    else:
        try:
            instrument = _instrument(args)
        except ValueError as error:
            parser.error(str(error))
        host, port, label = args.host, args.port, instrument.model

    server = InstrumentServer(instrument, host=host, port=port)
    try:
        server.start()
    except OSError as error:
        print(f"scpi-sim: error: cannot listen on {host}:{port}: {error.strerror or error}", file=sys.stderr)
        return 1

    stop = _stop_event()
    print(f"scpi-sim: {label} listening on {host}:{server.port} (Ctrl+C to stop)", flush=True)
    _wait(stop)
    server.stop()
    print("scpi-sim: stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
