"""A generic relay multiplexer card in slot 1 (channels 101, 102, ...)."""

from __future__ import annotations

from ..instrument import Call, Instrument, scpi
from ..scpi.params import parse_channel_list


class Multiplexer(Instrument):
    model = "MUX"
    serial = "SIM00003"

    def __init__(self, *, channels: int = 16):
        super().__init__()
        if not 1 <= channels <= 99:
            raise ValueError("channels must be between 1 and 99")
        self.valid_channels = range(101, 101 + channels)
        self.closed: set[int] = set()

    def reset(self) -> None:
        super().reset()
        self.closed.clear()

    def _channels(self, call: Call) -> list[int]:
        return parse_channel_list(call.arg(0), valid=self.valid_channels)

    @scpi("ROUTe:CLOSe")
    def close(self, call: Call) -> None:
        self.closed.update(self._channels(call))

    @scpi("ROUTe:OPEN")
    def open(self, call: Call) -> None:
        self.closed.difference_update(self._channels(call))

    @scpi("ROUTe:CLOSe?", max_args=1)
    def close_query(self, call: Call) -> list[bool]:
        return [ch in self.closed for ch in self._channels(call)]

    @scpi("ROUTe:OPEN?", max_args=1)
    def open_query(self, call: Call) -> list[bool]:
        return [ch not in self.closed for ch in self._channels(call)]

    @scpi("ROUTe:CLOSe:STATe?")
    def close_state(self, call: Call) -> str:
        return "(@" + ",".join(str(ch) for ch in sorted(self.closed)) + ")"

    @scpi("ROUTe:OPEN:ALL", max_args=0)
    def open_all(self, call: Call) -> None:
        self.closed.clear()
