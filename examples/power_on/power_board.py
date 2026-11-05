"""A board whose power-on you can watch on an oscilloscope.

Input ``vin`` (needs >= 10 V). Output ``3V3`` is its regulator: it starts after
2 ms, ramps up in 3 ms and has 30 mV of ripple once it runs. Output ``isense``
is a 100 mV/A current probe on ``vin``: an inrush peak while the input capacitors
charge, a step when the CPU starts, and then the idle current.
"""

import time

from scpi_sim import Device, Signal, Transient

MIN_VIN = 10.0
VOLTS_PER_AMP = 0.1
RIPPLE = Signal("sine", frequency=300e3, vpp=0.03)


class PowerBoard(Device):
    terminals = {"vin": "input", "3V3": "output", "isense": "output"}

    def __init__(self, inrush_amps: float = 2.2):
        super().__init__()
        self.inrush_amps = inrush_amps
        self.rail = None             # the latest Transient of each output, None before the first power-on
        self.sense = None

    # -- called by the bench after every instrument message -----------------------

    def update(self):
        powered = self.input_voltage("vin") >= MIN_VIN
        rail, now = self.rail, time.monotonic()
        running = rail is not None and rail.points[-1][1] > 0
        if powered and not running:
            self.rail = Transient(now, ((0, 0), (2e-3, 0), (5e-3, 3.3)), ripple=RIPPLE)
            self.sense = Transient(now, self._amps_to_volts(
                (0, 0), (0, self.inrush_amps), (1e-3, 0.3), (6e-3, 0.3), (6e-3, 1.3), (16e-3, 1.3), (16e-3, 0.85)))
        elif running and not powered:
            self.rail = Transient(now, ((0, 3.3), (20e-3, 0)))  # the output capacitors discharge
            self.sense = Transient(now, ((0, 0.85 * VOLTS_PER_AMP), (0, 0)))

    @staticmethod
    def _amps_to_volts(*points):
        return tuple((t, amps * VOLTS_PER_AMP) for t, amps in points)

    # -- what instruments see (fast, read the state once) ---------------------------

    def waveform(self, terminal):
        return self.rail if terminal == "3V3" else self.sense

    def voltage(self, terminal):
        waveform = self.waveform(terminal)
        return 0.0 if waveform is None else waveform.at(time.monotonic() - waveform.start)

    def current(self, terminal, volts):
        return self.voltage("isense") / VOLTS_PER_AMP
