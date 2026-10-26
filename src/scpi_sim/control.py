"""Control channel of a running bench: inject and clear faults from outside.

One JSON object per line in, one per line out::

    {"instrument": "dmm", "inject": "noise", "params": {"sigma": 0.05}}  -> {"ok": true}
    {"instrument": "dmm", "clear": true}                                  -> {"ok": true}
    {"list": true}                        -> {"ok": true, "faults": {"dmm": {...}, ...}}

Anything invalid gets ``{"ok": false, "error": "..."}``; the connection stays open.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bench import Bench


class _Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.server.connections.add(self.request)

    def finish(self) -> None:
        self.server.connections.discard(self.request)
        super().finish()

    def handle(self) -> None:
        for raw in self.rfile:
            if not raw.strip():
                continue
            answer = self.server.control.handle(raw)
            try:
                self.wfile.write(json.dumps(answer).encode() + b"\n")
            except OSError:
                return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler):
        # pick IPv4 or IPv6 from the host, so "::1" works like it does for the instruments
        self.address_family = socket.getaddrinfo(address[0], address[1], type=socket.SOCK_STREAM)[0][0]
        self.connections: set[socket.socket] = set()
        super().__init__(address, handler)


class ControlServer:
    def __init__(self, bench: Bench, host: str = "127.0.0.1", port: int = 5099):
        self.bench = bench
        self.host = host
        self.port = port
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._server = _Server((self.host, self.port), _Handler)
        self._server.control = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True, name="scpi-sim-control")
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            for connection in list(self._server.connections):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self._thread.join(timeout=5)
            self._server = self._thread = None

    def __enter__(self) -> ControlServer:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def handle(self, raw: bytes) -> dict[str, Any]:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("a request is a JSON object")
            return self._handle(request)
        except ValueError as error:  # includes json.JSONDecodeError
            return {"ok": False, "error": str(error)}
        except Exception as error:  # never drop the connection with a traceback
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}

    def _handle(self, request: dict) -> dict[str, Any]:
        instruments = self.bench.instruments
        if request.get("list"):
            return {"ok": True, "faults": {name: inst.faults for name, inst in instruments.items()}}
        name = request.get("instrument")
        instrument = instruments.get(name) if isinstance(name, str) else None
        if instrument is None:
            raise ValueError(f"unknown instrument {name!r} (bench has: {', '.join(instruments)})")
        if request.get("clear"):
            instrument.clear_faults()
        elif "inject" in request:
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("'params' must be an object")
            try:
                instrument.inject(str(request["inject"]), **params)
            except TypeError as error:
                raise ValueError(str(error)) from None
        else:
            raise ValueError("a request needs 'list', 'clear' or 'inject'")
        return {"ok": True}


def send_control(request: dict, host: str = "127.0.0.1", port: int = 5099, timeout: float = 2.0) -> dict:
    """Send one request to a bench's control port; raises OSError if it cannot be reached."""
    with socket.create_connection((host, port), timeout=timeout) as sock, sock.makefile("rwb") as stream:
        stream.write(json.dumps(request).encode() + b"\n")
        stream.flush()
        line = stream.readline()
    if not line:
        raise OSError("control port closed the connection")
    return json.loads(line)
