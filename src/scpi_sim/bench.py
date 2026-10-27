"""A simulated test bench: instruments wired together, described in YAML.

Wiring lines join terminals (``psu.ch1``, ``dmm.input``, ``mux.101``,
``mux.common`` or a node name) into nets. A closed multiplexer relay joins its
channel terminal to the common terminal. Loads sit on nodes. Every reading is
evaluated against the nets as they are at that moment.
"""

from __future__ import annotations

import importlib
import math
import re
import sys
import threading
from collections.abc import Iterable
from pathlib import Path

import yaml

from .console import ConsoleServer
from .control import ControlServer
from .device import Console, Device
from .errors import ScpiError
from .instrument import Instrument
from .instruments import Multimeter, Multiplexer, PowerSupply
from .loads import Load, Parallel, Resistor
from .options import real as _real, whole as _whole
from .profiles import get_profile
from .transport import InstrumentServer

def _text(value):
    if not isinstance(value, str) or not value:
        raise ValueError("must be a non-empty text")
    return value


# type -> (class, {option: converter})
_TYPES = {
    "psu": (PowerSupply, {"channels": _whole(1), "max_volts": _real(above=0), "max_amps": _real(above=0)}),
    "dmm": (Multimeter, {"volts": _real(), "amps": _real(), "ohms": _real(low=0), "noise": _real(low=0),
                         "seed": _whole(0)}),
    "mux": (Multiplexer, {"channels": _whole(1, 99)}),
}
# names an instrument cannot have: they are attributes of Bench
_RESERVED = {"instruments", "devices", "ports", "control_port", "host", "start", "stop", "port", "resource",
             "console_port", "console_url"}
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_NODE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WIRE = re.compile(r"\s*(\S+)\s*->\s*(\S+)\s*")


class BenchError(ValueError):
    """A problem in the bench description."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys instead of keeping the last one."""


def _unique_mapping(loader: _Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise BenchError(f"duplicate key {key!r} in line {key_node.start_mark.line + 1}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


class _Nets:
    """Union-find over terminal names."""

    def __init__(self, parent: dict[str, str] | None = None):
        self.parent = dict(parent or {})

    def find(self, terminal: str) -> str:
        self.parent.setdefault(terminal, terminal)
        while self.parent[terminal] != terminal:
            self.parent[terminal] = self.parent[self.parent[terminal]]
            terminal = self.parent[terminal]
        return terminal

    def join(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


class _NetLoad:
    """The load a supply output sees: everything on its net, resolved per call."""

    def __init__(self, bench: Bench, terminal: str):
        self.bench, self.terminal = bench, terminal

    def _parallel(self) -> Parallel:
        return Parallel(tuple(self.bench._loads_on(self.terminal)))

    def current(self, volts: float) -> float:
        return self._parallel().current(volts)

    def voltage(self, amps: float) -> float | None:
        return self._parallel().voltage(amps)

    def snapshot(self) -> Parallel:
        """The loads on the net right now, fixed for one operating-point calculation."""
        return self._parallel()


_in_current = threading.local()  # set while a device's current() runs on this thread


class _DeviceInput:
    """A device input terminal seen as a load on its net."""

    def __init__(self, device: Device, terminal: str):
        self.device, self.terminal = device, terminal

    def current(self, volts: float) -> float:
        outer, _in_current.active = getattr(_in_current, "active", False), True
        try:
            return self.device.current(self.terminal, volts)
        finally:
            _in_current.active = outer


class _DmmInput:
    """What the multimeter input is connected to."""

    def __init__(self, bench: Bench, terminal: str):
        self.bench, self.terminal = bench, terminal

    def volts(self) -> float:
        return self.bench._net_voltage(self.terminal)

    def ohms(self) -> float:
        resistors = [load for load in self.bench._loads_on(self.terminal) if isinstance(load, Resistor)]
        if not resistors:
            return math.inf
        if any(r.ohms == 0 for r in resistors):
            return 0.0
        return 1 / sum(1 / r.ohms for r in resistors)


class _DaqInput(_DmmInput):
    """A data acquisition unit's built-in multimeter: on the common of the card it measures."""

    def __init__(self, bench: Bench, name: str, daq: Multiplexer):
        self.bench, self.name, self.daq = bench, name, daq

    @property
    def terminal(self) -> str:
        return f"{self.name}.common{self.daq.active_slot}"


class Bench:
    def __init__(self) -> None:
        self.instruments: dict[str, Instrument] = {}
        self.devices: dict[str, Device] = {}
        self._console_ports: dict[str, int] = {}  # device name -> configured console port
        self.ports: dict[str, int] = {}
        self.control_port: int | None = None
        self.host = "127.0.0.1"
        self._loads: dict[str, Load] = {}
        self._static = _Nets()
        # terminals that drive their net: PSU channels -> (psu, channel), device outputs -> (device, terminal)
        self._sources: dict[str, tuple[PowerSupply | Device, int | str]] = {}
        self._device_inputs: dict[str, _DeviceInput] = {}
        self._update_failed: set[str] = set()
        self._update_lock = threading.Lock()  # one device update at a time, across instrument threads
        self._servers: dict[str, InstrumentServer] = {}
        self._control: ControlServer | None = None
        self._consoles: dict[str, ConsoleServer] = {}
        self._switch_lock = threading.Lock()  # relay check + switch, across all multiplexers

    # --- construction -------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path) -> Bench:
        path = Path(path)
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
        except OSError as error:
            raise BenchError(f"{path}: {error.strerror or error}") from None
        except BenchError as error:
            raise BenchError(f"{path}: {error}") from None
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
            problem = getattr(error, "problem", None) or str(error).splitlines()[0]
            raise BenchError(f"{path}: {where}{problem}") from None
        # python loads may live in modules next to the bench file
        folder = str(path.resolve().parent)
        sys.path.insert(0, folder)
        try:
            return cls.from_dict(data)
        except BenchError as error:
            raise BenchError(f"{path}: {error}") from None
        finally:
            sys.path.remove(folder)

    @classmethod
    def from_dict(cls, data: dict) -> Bench:
        if not isinstance(data, dict):
            raise BenchError("a bench is a mapping with an 'instruments' key")
        unknown = set(data) - {"instruments", "devices", "loads", "wiring", "control"}
        if unknown:
            raise BenchError(f"unknown key {sorted(unknown)[0]!r} "
                             "(allowed: instruments, devices, loads, wiring, control)")
        bench = cls()
        bench._add_instruments(data.get("instruments"))
        bench._add_devices(data.get("devices") or {})
        bench._add_loads(data.get("loads") or {})
        bench._add_wiring(data.get("wiring") or [])
        bench._check()
        bench.control_port = bench._control_setting(data.get("control"))
        used = {**{f"instrument {n!r}": p for n, p in bench.ports.items()},
                **{f"console of {n!r}": p for n, p in bench._console_ports.items()}}
        seen: dict[int, str] = {}
        for owner, port in used.items():
            if port and port in seen:
                raise BenchError(f"port {port} is used by {seen[port]} and {owner}")
            seen[port] = owner
        if bench.control_port and bench.control_port in seen:
            raise BenchError(f"control port {bench.control_port} is already used by {seen[bench.control_port]}")
        for instrument in bench.instruments.values():
            instrument.listeners.append(bench._update_devices)
        return bench

    def _add_instruments(self, config) -> None:
        if not isinstance(config, dict) or not config:
            raise BenchError("'instruments' must map names to instrument settings")
        used_ports: dict[int, str] = {}
        for name, settings in config.items():
            where = f"instrument {name!r}"
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise BenchError(f"{where}: names use lowercase letters, digits and '_'")
            if name in _RESERVED:
                raise BenchError(f"{where}: reserved name, choose another one")
            if not isinstance(settings, dict):
                raise BenchError(f"{where}: settings must be a mapping")
            settings = dict(settings)
            kind = settings.pop("type", None)
            profile = settings.pop("profile", None)
            if profile is not None:
                try:
                    factory = get_profile(profile)
                except (KeyError, TypeError) as error:
                    raise BenchError(f"{where}: {error.args[0] if isinstance(error, KeyError) else error}") from None
                if kind is not None and kind != factory.kind:
                    raise BenchError(f"{where}: type {kind!r} does not match profile {profile!r} ({factory.kind})")
                options = factory.options
                for_what = f"profile {profile}"
            elif kind not in _TYPES:
                raise BenchError(f"{where}: unknown type {kind!r} (allowed: {', '.join(_TYPES)}; or give a 'profile')")
            else:
                factory, options = _TYPES[kind]
                for_what = kind
            port = settings.pop("port", None)
            if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
                raise BenchError(f"{where}: 'port' must be a number 0-65535")
            if port and port in used_ports:
                raise BenchError(f"{where}: port {port} is already used by {used_ports[port]!r}")
            used_ports[port] = name
            options = {**options, "serial": _text}
            unknown = set(settings) - set(options)
            if unknown:
                raise BenchError(f"{where}: unknown option {sorted(unknown)[0]!r} "
                                 f"(allowed for {for_what}: {', '.join(sorted(options))})")
            for option, value in settings.items():
                try:
                    settings[option] = options[option](value)
                except ValueError as error:
                    raise BenchError(f"{where}: option {option!r} {error}") from None
            try:
                instrument = factory(**settings)
            except (TypeError, ValueError) as error:
                raise BenchError(f"{where}: {error}") from None
            self.instruments[name] = instrument
            self.ports[name] = port
            if isinstance(instrument, PowerSupply):
                for channel in range(1, instrument.channel_count + 1):
                    terminal = f"{name}.ch{channel}"
                    self._sources[terminal] = (instrument, channel)
                    instrument.loads[channel - 1] = _NetLoad(self, terminal)
            elif isinstance(instrument, Multimeter):
                instrument.source = _DmmInput(self, f"{name}.input")
            elif isinstance(instrument, Multiplexer):
                instrument.before_close = lambda closed, mux=name: self._check_relays(mux, closed)
                instrument.switch_lock = self._switch_lock
                dmm = getattr(instrument, "dmm", None)
                if isinstance(dmm, Multimeter):  # a data acquisition unit: its multimeter sits on the commons
                    dmm.source = _DaqInput(self, name, instrument)
                    self._static.join(f"{name}.common", f"{name}.{instrument.common_pin(101)}")

    def _add_loads(self, config) -> None:
        if not isinstance(config, dict):
            raise BenchError("'loads' must map node names to loads")
        for node, settings in config.items():
            where = f"load {node!r}"
            if not isinstance(node, str) or not _NODE.fullmatch(node) or node in self.instruments:
                raise BenchError(f"{where}: a load sits on a node name (letters, digits, '_'), "
                                 "not on an instrument")
            if node in self.devices:
                raise BenchError(f"{where}: {node!r} is a device; put the load on a node and wire "
                                 "that node to a device terminal")
            if not isinstance(settings, dict):
                raise BenchError(f"{where}: settings must be a mapping")
            kind = settings.get("type")
            if kind == "resistor":
                try:
                    self._loads[node] = Resistor(float(settings["ohms"]))
                except KeyError:
                    raise BenchError(f"{where}: a resistor needs 'ohms'") from None
                except (TypeError, ValueError) as error:
                    raise BenchError(f"{where}: {error}") from None
            elif kind == "python":
                self._loads[node] = self._python_load(where, settings)
            else:
                raise BenchError(f"{where}: unknown type {kind!r} (allowed: resistor, python)")

    @staticmethod
    def _import(where: str, target) -> type:
        module_name, _, class_name = str(target).partition(":")
        if not module_name or not class_name:
            raise BenchError(f"{where}: 'class' must look like 'package.module:ClassName'")
        try:
            return getattr(importlib.import_module(module_name), class_name)
        except Exception as error:  # import errors, missing class
            raise BenchError(f"{where}: cannot import {target}: {error}") from None

    @classmethod
    def _python_load(cls, where: str, settings: dict) -> Load:
        target = settings.get("class", "")
        factory = cls._import(where, target)
        try:
            load = factory(**(settings.get("args") or {}))
        except Exception as error:  # bad args, errors in the constructor
            raise BenchError(f"{where}: cannot create {target}: {error}") from None
        if not callable(getattr(load, "current", None)):
            raise BenchError(f"{where}: {target} has no current(volts) method")
        return load

    def _add_devices(self, config) -> None:
        if not isinstance(config, dict):
            raise BenchError("'devices' must map names to device settings")
        for name, settings in config.items():
            where = f"device {name!r}"
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise BenchError(f"{where}: names use lowercase letters, digits and '_'")
            if name in _RESERVED:
                raise BenchError(f"{where}: reserved name, choose another one")
            if name in self.instruments:
                raise BenchError(f"{where}: an instrument is already called {name!r}")
            if not isinstance(settings, dict):
                raise BenchError(f"{where}: settings must be a mapping")
            unknown = set(settings) - {"class", "args", "console"}
            if unknown:
                raise BenchError(f"{where}: unknown key {sorted(unknown)[0]!r} (allowed: class, args, console)")
            target = settings.get("class", "")
            factory = self._import(where, target)
            if not (isinstance(factory, type) and issubclass(factory, Device)):
                raise BenchError(f"{where}: {target} is not a scpi_sim Device subclass")
            args = settings.get("args") or {}
            if not isinstance(args, dict):
                raise BenchError(f"{where}: 'args' must be a mapping")
            try:
                device = factory(**args)
            except Exception as error:
                raise BenchError(f"{where}: cannot create {target}: {error}") from None
            if not isinstance(getattr(device, "console", None), Console):
                raise BenchError(f"{where}: {target} is not set up as a Device -- "
                                 "does its __init__ call super().__init__()?")
            if "console" in settings:
                port = settings["console"]
                if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
                    raise BenchError(f"{where}: 'console' must be a port number 0-65535")
                self._console_ports[name] = port
            self.devices[name] = device
            for terminal, kind in device.terminals.items():
                full = f"{name}.{terminal}"
                if kind == "output":
                    self._sources[full] = (device, terminal)
                else:
                    self._device_inputs[full] = _DeviceInput(device, terminal)
            device.attach(lambda terminal, device_name=name: self._device_input_voltage(f"{device_name}.{terminal}"))

    def _terminal(self, text: str, line: str) -> str:
        where = f"wiring line {line!r}"
        if "." not in text:
            if not _NODE.fullmatch(text):
                raise BenchError(f"{where}: invalid node name {text!r}")
            if text in self.instruments:
                raise BenchError(f"{where}: {text!r} is an instrument, name one of its terminals")
            if text in self.devices:
                raise BenchError(f"{where}: {text!r} is a device, name one of its terminals "
                                 f"({', '.join(self.devices[text].terminals)})")
            return text
        name, _, pin = text.partition(".")
        if name in self.devices:
            if pin not in self.devices[name].terminals:
                raise BenchError(f"{where}: unknown terminal {text!r} "
                                 f"(device terminals: {', '.join(self.devices[name].terminals)})")
            return text
        instrument = self.instruments.get(name)
        if instrument is None:
            raise BenchError(f"{where}: unknown instrument or device {name!r}")
        valid = (
            (pin == "input") if isinstance(instrument, Multimeter) else
            (text in self._sources) if isinstance(instrument, PowerSupply) else
            (pin in instrument.common_pins or (pin.isdigit() and int(pin) in instrument.valid_channels))
        )
        if not valid:
            raise BenchError(f"{where}: unknown terminal {text!r}")
        return text

    def _add_wiring(self, lines) -> None:
        if not isinstance(lines, list):
            raise BenchError("'wiring' must be a list of 'A -> B' lines")
        for line in lines:
            m = _WIRE.fullmatch(str(line))
            if not m:
                raise BenchError(f"wiring line {line!r}: expected 'A -> B'")
            self._static.join(self._terminal(m.group(1), line), self._terminal(m.group(2), line))

    def _check(self) -> None:
        for node in self._loads:
            if node not in self._static.parent:
                raise BenchError(f"load {node!r}: node {node!r} is not wired to anything")
        self._check_sources(self._static, error=lambda msg: BenchError(msg))

    @staticmethod
    def _control_setting(value) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 65535:
            raise BenchError("'control' must be a port number 0-65535")
        return value

    # --- evaluation -----------------------------------------------------------------

    def _nets(self, closed_override: tuple[str, set[int]] | None = None) -> _Nets:
        nets = _Nets(self._static.parent)
        for name, instrument in self.instruments.items():
            if isinstance(instrument, Multiplexer):
                closed = instrument.closed
                if closed_override is not None and closed_override[0] == name:
                    closed = closed_override[1]
                for channel in tuple(closed):
                    nets.join(f"{name}.{channel}", f"{name}.{instrument.common_pin(channel)}")
        return nets

    def _check_sources(self, nets: _Nets, error) -> None:
        seen: dict[str, str] = {}
        for terminal in self._sources:
            net = nets.find(terminal)
            if net in seen:
                raise error(f"{terminal} and {seen[net]} would drive the same net")
            seen[net] = terminal

    def _check_relays(self, mux: str, closed: set[int]) -> None:
        self._check_sources(self._nets((mux, closed)), error=lambda msg: ScpiError(-221))

    def _loads_on(self, terminal: str) -> Iterable[Load]:
        nets = self._nets()
        net = nets.find(terminal)
        loads = [load for node, load in self._loads.items() if nets.find(node) == net]
        loads += [load for name, load in self._device_inputs.items() if nets.find(name) == net]
        return loads

    def _device_input_voltage(self, terminal: str) -> float:
        if getattr(_in_current, "active", False):  # would recurse: the voltage depends on that current
            raise RuntimeError("input_voltage() inside current(): current() gets the voltage as its argument")
        return self._net_voltage(terminal)

    def _net_voltage(self, terminal: str) -> float:
        """Voltage of the net ``terminal`` is on: from the supply or device output driving it, else 0."""
        nets = self._nets()
        net = nets.find(terminal)
        for source, (owner, pin) in self._sources.items():
            if nets.find(source) == net:
                if isinstance(owner, PowerSupply):
                    return owner.output(pin)[0]
                volts = owner.voltage(pin)
                return 0.0 if volts is None else float(volts)
        return 0.0

    def _update_devices(self) -> None:
        with self._update_lock:
            self._update_each()

    def _update_each(self) -> None:
        for name, device in self.devices.items():
            try:
                device.update()
            except Exception as error:
                if name not in self._update_failed:
                    self._update_failed.add(name)
                    print(f"scpi-sim: device {name}: update() failed: {type(error).__name__}: {error}",
                          file=sys.stderr)

    # --- running ----------------------------------------------------------------------

    def start(self, host: str = "127.0.0.1", free_ports: bool = False) -> None:
        try:
            for name, instrument in self.instruments.items():
                server = InstrumentServer(instrument, host=host, port=0 if free_ports else self.ports[name])
                server.start()
                self._servers[name] = server
            for name, port in self._console_ports.items():
                console = ConsoleServer(self.devices[name], host=host, port=0 if free_ports else port, name=name)
                console.start()
                self._consoles[name] = console
            if self.control_port is not None:
                self._control = ControlServer(self, host=host, port=0 if free_ports else self.control_port)
                self.control_port = self._control.start()
        except BaseException:
            self.stop()
            raise
        self.host = host

    def stop(self) -> None:
        """Stop every server; if one fails, still stop the others, then re-raise."""
        servers = [*([self._control] if self._control else []), *self._consoles.values(), *self._servers.values()]
        self._control = None
        self._consoles.clear()
        self._servers.clear()
        failure = None
        for server in servers:
            try:
                server.stop()
            except Exception as error:
                failure = failure or error
        if failure is not None:
            raise failure

    def port(self, name: str) -> int:
        return self._servers[name].port

    def resource(self, name: str) -> str:
        return f"TCPIP0::{self.host}::{self.port(name)}::SOCKET"

    def console_port(self, name: str) -> int:
        """Bound console port of device ``name``; KeyError if it has no console."""
        return self._consoles[name].port

    def console_url(self, name: str) -> str:
        """pyserial URL of a device console: ``socket://host:port``."""
        return f"socket://{self.host}:{self.console_port(name)}"

    def __enter__(self) -> Bench:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def __getattr__(self, name: str) -> Instrument | Device:
        for registry in ("instruments", "devices"):
            items = self.__dict__.get(registry, {})
            if name in items:
                return items[name]
        raise AttributeError(name)
