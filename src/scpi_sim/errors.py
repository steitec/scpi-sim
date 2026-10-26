"""SCPI error codes as reported through the error queue (SYSTem:ERRor?)."""

from __future__ import annotations

STANDARD_ERRORS: dict[int, str] = {
    0: "No error",
    -100: "Command error",
    -102: "Syntax error",
    -104: "Data type error",
    -108: "Parameter not allowed",
    -109: "Missing parameter",
    -113: "Undefined header",
    -114: "Header suffix out of range",
    -131: "Invalid suffix",
    -221: "Settings conflict",
    -222: "Data out of range",
    -223: "Too much data",
    -224: "Illegal parameter value",
    -300: "Device-specific error",
    -350: "Queue overflow",
}


class ScpiError(Exception):
    """An error that ends up in the instrument's error queue."""

    def __init__(self, code: int, message: str | None = None):
        self.code = code
        self.message = message if message is not None else STANDARD_ERRORS[code]
        super().__init__(str(self))

    def __str__(self) -> str:
        return f'{self.code},"{self.message}"'
