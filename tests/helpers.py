import socket

_open = []


def connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    f = sock.makefile("rwb")
    _open.append((sock, f))
    return sock, f


def query(f, text):
    f.write(text.encode() + b"\n")
    f.flush()
    return f.readline().decode().rstrip("\n")


def close_all():
    while _open:
        sock, f = _open.pop()
        f.close()
        sock.close()


BASIC = """
instruments:
  psu: {type: psu, port: 0}
  dmm: {type: dmm, port: 0, noise: 0}
  mux: {type: mux, port: 0}
loads:
  main: {type: resistor, ohms: 24}
wiring:
  - psu.ch1 -> main
  - mux.101 -> main
  - mux.common -> dmm.input
"""


import time as _time  # noqa: E402

from scpi_sim.device import Device  # noqa: E402


class Board(Device):
    """3.3 V regulator board: 0.5 A for 0.2 s after power-on, then 0.1 A; drives 3V3 when vin >= 4.5 V."""

    terminals = {"vin": "input", "3V3": "output"}

    def __init__(self, short=False):
        super().__init__()
        self.short = short
        self.powered_at = None
        self.updates = 0

    def update(self):
        self.updates += 1
        on = self.input_voltage("vin") >= 4.5
        if on and self.powered_at is None:
            self.powered_at = _time.monotonic()
        if not on:
            self.powered_at = None

    def current(self, terminal, volts):
        if self.short:
            return volts / 0.5
        if volts < 1 or self.powered_at is None:
            return 0.01 if volts > 0 else 0.0
        return 0.5 if _time.monotonic() - self.powered_at < 0.2 else 0.1

    def voltage(self, terminal):
        return 3.3 if self.input_voltage("vin") >= 4.5 else 0.0


class NotADevice:
    pass


class BuggyBoard(Board):
    def current(self, terminal, volts):
        raise RuntimeError("bug in current()")


class BadUpdateBoard(Board):
    def update(self):
        raise RuntimeError("bug in update()")


class EchoBoard(Board):
    """Board with a console: prints "boot" on power-on and echoes typed lines."""

    def update(self):
        was_on = self.powered_at is not None
        super().update()
        if self.powered_at is not None and not was_on:
            self.console.write("boot\n")

    def console_line(self, line):
        self.console.write(f"> {line}\n")


class GrumpyBoard(EchoBoard):
    def console_line(self, line):
        if line == "crash":
            raise RuntimeError("bug in console_line()")
        super().console_line(line)


def read_until(sock, expected, timeout=2.0):
    """Read from a raw socket until ``expected`` (bytes) has arrived; return everything read."""
    import time as t
    sock.settimeout(0.1)
    data = b""
    end = t.monotonic() + timeout
    while expected not in data:
        assert t.monotonic() < end, f"timeout waiting for {expected!r}, got {data!r}"
        try:
            chunk = sock.recv(4096)
        except OSError:
            continue
        if not chunk:
            break
        data += chunk
    return data


class OverlapBoard(Board):
    """Counts how often update() runs on two threads at the same time."""

    def __init__(self):
        super().__init__()
        self.inside = 0
        self.overlaps = 0

    def update(self):
        self.inside += 1
        if self.inside > 1:
            self.overlaps += 1
        _time.sleep(0.001)
        super().update()
        self.inside -= 1


class LazyBoard(Device):
    """Forgets super().__init__()."""

    terminals = {"vin": "input"}

    def __init__(self):
        pass


def wait_for_clients(device, count=1, timeout=5.0):
    end = _time.monotonic() + timeout
    while device.console.clients < count:
        assert _time.monotonic() < end, "console client did not attach"
        _time.sleep(0.005)


class LoopyBoard(Board):
    """Breaks the rule: current() asks for its own input voltage."""

    def current(self, terminal, volts):
        return self.input_voltage(terminal) / 10.0
