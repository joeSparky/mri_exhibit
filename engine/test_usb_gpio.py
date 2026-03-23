from __future__ import annotations

import sys
import time

from usb_gpio import UsbGpio


def main() -> int:
    gpio = UsbGpio()

    print("Opening USB GPIO...")
    if not gpio.open():
        print(f"Open failed: {gpio.last_error}")
        return 1

    print(f"USB GPIO opened on {gpio.port}")

    print("Pinging...")
    if not gpio.ping():
        print(f"Ping failed: {gpio.last_error}")
        gpio.close()
        return 1

    print("Turning light ON...")
    if not gpio.light_on():
        print(f"Light ON failed: {gpio.last_error}")
        gpio.close()
        return 1

    print("Waiting 2 seconds...")
    time.sleep(2.0)

    print("Turning light OFF...")
    if not gpio.light_off():
        print(f"Light OFF failed: {gpio.last_error}")
        gpio.close()
        return 1

    print("Closing USB GPIO...")
    gpio.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
