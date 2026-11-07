"""Buttons and the optional LED/buzzer."""
from __future__ import annotations

import functools
import logging
import queue
import time

log = logging.getLogger(__name__)

# BCM GPIO -> physical header pin
PHYSICAL = {
    2: 3, 3: 5, 4: 7, 17: 11, 27: 13, 22: 15, 10: 19, 9: 21, 11: 23, 5: 29,
    6: 31, 13: 33, 19: 35, 26: 37, 14: 8, 15: 10, 18: 12, 23: 16, 24: 18,
    25: 22, 8: 24, 7: 26, 12: 32, 16: 36, 20: 38, 21: 40,
}


class Buttons:
    """Puts ("shutter", t) and ("poweroff", t) into an event queue.

    on_shutter is also called directly from the GPIO thread at the moment of the
    press, so lens mode knows exactly when the press happened."""

    def __init__(self, cfg: dict, events: queue.Queue, on_shutter=None):
        from gpiozero import Button

        b = cfg["buttons"]
        self.devices = []
        if b["shutter_gpio"] >= 0:
            sh = Button(b["shutter_gpio"], pull_up=True, bounce_time=b["bounce_s"])

            def pressed():
                t = time.monotonic()
                if on_shutter:
                    on_shutter(t)
                events.put(("shutter", t))

            sh.when_pressed = pressed
            self.devices.append(sh)
        if b["power_gpio"] >= 0:
            pw = Button(b["power_gpio"], pull_up=True, bounce_time=b["bounce_s"],
                        hold_time=b["power_hold_s"])
            pw.when_held = lambda: events.put(("poweroff", time.monotonic()))
            self.devices.append(pw)
        log.info("Buttons: shutter GPIO%s, power GPIO%s", b["shutter_gpio"], b["power_gpio"])

    def close(self) -> None:
        for d in self.devices:
            d.close()


class Feedback:
    def __init__(self, gpio: int):
        self.led = None
        if gpio >= 0:
            from gpiozero import LED
            self.led = LED(gpio)

    def _blink(self, n: int, on: float = 0.08, off: float = 0.15) -> None:
        if self.led:
            self.led.blink(on_time=on, off_time=off, n=n, background=True)

    def ready(self): self._blink(1)
    def saved(self): self._blink(2)
    def missed(self): self._blink(1, on=0.8)
    error = missed

    def armed(self):
        """Solid on: ready for the camera's real shutter."""
        if self.led:
            self.led.on()

    def off(self):
        if self.led:
            self.led.off()

    def close(self):
        if self.led:
            self.led.close()


def test_led(gpio: int) -> None:
    """Blink a GPIO slowly so you can see whether it's the LED."""
    from gpiozero import LED

    print(f"Blinking GPIO{gpio} (header pin {PHYSICAL.get(gpio, '?')}) 5 times. "
          "Don't press any buttons while it runs.", flush=True)
    led = LED(gpio)
    try:
        for _ in range(5):
            led.on()
            time.sleep(0.5)
            led.off()
            time.sleep(0.5)
    finally:
        led.close()
    print("Done. If the LED blinked, set [feedback] gpio in config.toml to this number.")


def find_buttons() -> None:
    """Watch every free GPIO with a pull-up and report presses."""
    from gpiozero import Button

    def report(pin, what):
        print(f"  GPIO{pin:<2} (header pin {PHYSICAL[pin]:>2}) {what}", flush=True)

    devices = []
    for pin in sorted(PHYSICAL):
        try:
            btn = Button(pin, pull_up=True, bounce_time=0.05)
        except Exception as e:  # pin in use (UART, I2C, etc.)
            print(f"  skipping GPIO{pin}: {e}")
            continue
        if btn.is_pressed:
            report(pin, "is already LOW at start (an output, or a button stuck closed?)")
        btn.when_pressed = functools.partial(report, pin, "pressed")
        btn.when_released = functools.partial(report, pin, "released")
        devices.append(btn)
    print("\nPress each button on the back. Ctrl-C to stop.\n", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for d in devices:
            d.close()
