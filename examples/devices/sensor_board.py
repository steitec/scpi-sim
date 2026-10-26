"""A simulated sensor board -- an example of writing your own scpi-sim device.

Input ``vin`` (needs >= 4.5 V), output ``3V3`` (its regulator). After
power-on it draws an inrush of 0.4 A for 0.2 s, then 80 mA; it boots for about
a second, printing to its console, and then answers ``id?`` and ``temp?``.
"""

import threading
import time

from scpi_sim import Device

MIN_VIN = 4.5
BOOT_LOG = ["SensorBoard bootloader 1.2", "loading firmware ... ok", "sensor init ... ok"]


class SensorBoard(Device):
    terminals = {"vin": "input", "3V3": "output"}

    def __init__(self, serial: str = "SB-00-0001", boot_seconds: float = 1.0):
        super().__init__()
        self.serial = serial
        self.boot_seconds = boot_seconds
        self.powered_at = None      # time.monotonic() of power-on, None while off
        self.ready = False
        self._generation = 0        # a power cycle invalidates a running boot

    # -- called by the bench after every instrument message -----------------------

    def update(self):
        powered = self.input_voltage("vin") >= MIN_VIN
        if powered and self.powered_at is None:
            self.powered_at = time.monotonic()
            self._generation += 1
            threading.Thread(target=self._boot, args=(self._generation,), daemon=True).start()
        elif not powered and self.powered_at is not None:
            self.powered_at = None
            self.ready = False
            self._generation += 1

    def _boot(self, generation):
        for line in BOOT_LOG:
            time.sleep(self.boot_seconds / len(BOOT_LOG))
            if generation != self._generation:
                return  # switched off meanwhile
            self.console.write(line + "\n")
        self.ready = True
        self.console.write("sensor> ")

    # -- electrical behaviour (fast, no input_voltage() in current()) ---------------

    def current(self, terminal, volts):
        if volts < 1.0:
            return 0.0
        if self.powered_at is None:
            return 0.002                     # leakage below the start threshold
        if time.monotonic() - self.powered_at < 0.2:
            return 0.4                       # inrush
        return 0.08

    def voltage(self, terminal):
        return 3.3 if self.input_voltage("vin") >= MIN_VIN else 0.0

    # -- console ------------------------------------------------------------------

    def console_line(self, line):
        if not self.ready:
            return
        command = line.strip()
        if command == "id?":
            answer = self.serial
        elif command == "temp?":
            answer = "23.5"
        elif command:
            answer = f"unknown command: {command}"
        else:
            answer = ""
        self.console.write((answer + "\n" if answer else "") + "sensor> ")
