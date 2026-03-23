from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import serial
import serial.tools.list_ports


@dataclass
class UsbGpio:
    baud: int = 115200
    open_delay_s: float = 2.0
    read_timeout_s: float = 0.25
    reply_window_s: float = 2.0
    serial_handle: Optional[serial.Serial] = field(default=None, init=False)
    port: Optional[str] = field(default=None, init=False)
    last_error: str = field(default="", init=False)
    last_lines: list[str] = field(default_factory=list, init=False)

    def set_error(self, message: str) -> None:
        self.last_error = str(message).strip()
        if self.last_error:
            print(f"USB GPIO ERROR: {self.last_error}")

    def clear_error(self) -> None:
        self.last_error = ""

    def is_open(self) -> bool:
        return self.serial_handle is not None and self.serial_handle.is_open

    def detect_port(self) -> Optional[str]:
        try:
            ports = list(serial.tools.list_ports.comports())
        except Exception as e:
            self.set_error(f"Could not enumerate serial ports: {e}")
            return None

        preferred: list[str] = []
        fallback: list[str] = []

        for p in ports:
            desc = (p.description or "").lower()
            manu = (p.manufacturer or "").lower()
            hwid = (p.hwid or "").lower()
            text = " ".join([desc, manu, hwid])

            if any(key in text for key in ("arduino", "ch340", "usb serial", "cp210", "ftdi", "wch")):
                preferred.append(p.device)
            else:
                fallback.append(p.device)

        for device in preferred + fallback:
            if self._probe_port(device):
                return device
        return None

    def open(self) -> bool:
        self.close()
        self.clear_error()
        self.last_lines = []

        device = self.detect_port()
        if not device:
            if not self.last_error:
                self.set_error("No compatible USB GPIO device was detected.")
            return False

        try:
            ser = serial.Serial(device, self.baud, timeout=self.read_timeout_s)
            time.sleep(self.open_delay_s)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            self.serial_handle = ser
            self.port = device
        except Exception as e:
            self.set_error(f"Failed to open {device}: {e}")
            self.serial_handle = None
            self.port = None
            return False

        if not self.ping():
            self.set_error(f"Opened {device} but did not receive OK from GPIO.")
            self.close()
            return False

        return True

    def close(self) -> None:
        if self.serial_handle is not None:
            try:
                self.serial_handle.close()
            except Exception:
                pass
        self.serial_handle = None
        self.port = None

    def is_present(self) -> bool:
        if self.is_open():
            return self.ping()
        return self.open()

    def ping(self) -> bool:
        return self.send_command("PING", accepted_replies={"OK"})

    def light_on(self) -> bool:
        return self.send_command("LIGHT ON", accepted_replies={"LIGHT ON", "OK"})

    def light_off(self) -> bool:
        return self.send_command("LIGHT OFF", accepted_replies={"LIGHT OFF", "OK"})

    def send_command(self, command: str, accepted_replies: Optional[set[str]] = None) -> bool:
        if accepted_replies is None:
            accepted_replies = {command.strip().upper(), "OK"}

        if not self.is_open():
            self.set_error("USB GPIO is not open.")
            return False

        assert self.serial_handle is not None
        ser = self.serial_handle

        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write((command.strip() + "\n").encode("utf-8"))
            ser.flush()
        except Exception as e:
            self.set_error(f"Failed to send {command!r}: {e}")
            return False

        lines = self._collect_lines(ser)
        self.last_lines = lines
        normalized = {line.strip().upper() for line in lines}

        if normalized & accepted_replies:
            self.clear_error()
            return True

        self.set_error(f"Command {command!r} got replies {lines!r}")
        return False

    def _probe_port(self, device: str) -> bool:
        try:
            with serial.Serial(device, self.baud, timeout=self.read_timeout_s) as ser:
                time.sleep(self.open_delay_s)
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.write(b"PING\n")
                ser.flush()
                lines = self._collect_lines(ser)
                normalized = {line.strip().upper() for line in lines}
                if "OK" in normalized:
                    return True
        except Exception as e:
            print(f"USB GPIO probe failed on {device}: {e}")
        return False

    def _collect_lines(self, ser: serial.Serial) -> list[str]:
        deadline = time.monotonic() + self.reply_window_s
        lines: list[str] = []

        while time.monotonic() < deadline:
            try:
                raw = ser.readline()
            except Exception as e:
                self.set_error(f"Read failed: {e}")
                break

            if not raw:
                continue

            reply = raw.decode(errors="ignore").strip()
            if not reply:
                continue

            lines.append(reply)
            print(f"USB GPIO REPLY: {reply!r}")

        return lines
