"""A device console on a raw TCP port, like a serial line behind a terminal server.

Everything the device writes to its ``console`` goes to every connected client;
every line a client types goes to ``device.console_line()``. pyserial reaches it
as ``socket://host:port``. A client that does not keep up loses output, like a
serial line overrunning a terminal server's buffer; it stays connected.
"""

from __future__ import annotations

import asyncio
import sys

from .device import Device
from .transport import MAX_LINE, TcpServer

MAX_PENDING = 256 * 1024  # bytes queued for one client before its output is dropped


class ConsoleServer(TcpServer):
    def __init__(self, device: Device, host: str = "127.0.0.1", port: int = 0, name: str = "device"):
        super().__init__(host, port, name=f"scpi-sim-console-{name}")
        self.device = device
        self.device_name = name
        self._reported = False

    def __enter__(self) -> ConsoleServer:
        self.start()
        return self

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()

        def write(text: str) -> None:
            if not writer.is_closing() and writer.transport.get_write_buffer_size() < MAX_PENDING:
                writer.write(text.encode("utf-8", errors="replace"))

        def sink(text: str) -> None:  # called from any thread
            loop.call_soon_threadsafe(write, text)

        self.device.console.attach(sink)
        try:
            buffer = bytearray()
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buffer += chunk
                while (end := buffer.find(b"\n")) >= 0:
                    line = bytes(buffer[:end]).decode("utf-8", errors="replace").rstrip("\r")
                    del buffer[:end + 1]
                    self._typed(line)
                if len(buffer) > MAX_LINE:  # no newline in sight: drop it
                    buffer.clear()
        finally:
            self.device.console.detach(sink)

    def _typed(self, line: str) -> None:
        try:
            self.device.console_line(line)
        except Exception as error:
            if not self._reported:
                self._reported = True
                print(f"scpi-sim: device {self.device_name}: console_line() failed: "
                      f"{type(error).__name__}: {error}", file=sys.stderr)
