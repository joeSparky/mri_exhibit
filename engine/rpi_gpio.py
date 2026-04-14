from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

try:
    import RPi.GPIO as GPIO
except Exception as e:  # pragma: no cover - depends on Pi runtime
    GPIO = None
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


@dataclass
class RpiGpio:
    light_pin: int = 18
    active_high: bool = True
    mode: str = "BCM"
    opened: bool = field(default=False, init=False)
    port: Optional[str] = field(default="rpi-gpio", init=False)
    last_error: str = field(default="", init=False)
    last_lines: list[str] = field(default_factory=list, init=False)

    def set_error(self, message: str) -> None:
        self.last_error = str(message).strip()
        if self.last_error:
            print(f"GPIO ERROR: {self.last_error}")

    def clear_error(self) -> None:
        self.last_error = ""

    def is_open(self) -> bool:
        return self.opened

    def _on_level(self) -> int:
        return GPIO.HIGH if self.active_high else GPIO.LOW

    def _off_level(self) -> int:
        return GPIO.LOW if self.active_high else GPIO.HIGH

    def open(self) -> bool:
        self.close()
        self.clear_error()
        self.last_lines = []

        if GPIO is None:
            self.set_error(f"RPi.GPIO import failed: {_IMPORT_ERROR}")
            return False

        try:
            if self.mode.upper() == "BOARD":
                GPIO.setmode(GPIO.BOARD)
            else:
                GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.light_pin, GPIO.OUT, initial=self._off_level())
            self.opened = True
            return True
        except Exception as e:
            self.set_error(f"Failed to initialize Raspberry Pi GPIO pin {self.light_pin}: {e}")
            self.opened = False
            return False

    def close(self) -> None:
        if GPIO is None:
            self.opened = False
            return

        try:
            if self.opened:
                GPIO.output(self.light_pin, self._off_level())
        except Exception:
            pass

        try:
            GPIO.cleanup(self.light_pin)
        except Exception:
            pass

        self.opened = False

    def is_present(self) -> bool:
        if self.is_open():
            return True
        return self.open()

    def ping(self) -> bool:
        if self.is_open():
            self.clear_error()
            return True
        return self.open()

    def light_on(self) -> bool:
        if not self.is_open() and not self.open():
            return False

        try:
            GPIO.output(self.light_pin, self._on_level())
            self.clear_error()
            return True
        except Exception as e:
            self.set_error(f"Failed to set GPIO pin {self.light_pin} ON: {e}")
            return False

    def light_off(self) -> bool:
        if not self.is_open() and not self.open():
            return False

        try:
            GPIO.output(self.light_pin, self._off_level())
            self.clear_error()
            return True
        except Exception as e:
            self.set_error(f"Failed to set GPIO pin {self.light_pin} OFF: {e}")
            return False
