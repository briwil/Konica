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
    """One two-colour LED, one GPIO per colour (common ground).

    ok colour:   ready (1 blink), armed (solid), saved (2 blinks)
    warn colour: nothing captured in time, or an error (1 long blink)
    """

    def __init__(self, fb: dict):
        self.ok = self.warn = None
        if fb["ok_gpio"] >= 0 or fb["warn_gpio"] >= 0:
            from gpiozero import LED
            if fb["ok_gpio"] >= 0:
                self.ok = LED(fb["ok_gpio"])
            if fb["warn_gpio"] >= 0:
                self.warn = LED(fb["warn_gpio"])

    @staticmethod
    def _blink(led, n: int, on: float = 0.08, off: float = 0.15) -> None:
        if led:
            led.blink(on_time=on, off_time=off, n=n, background=True)

    def ready(self): self._blink(self.ok, 1)
    def saved(self): self._blink(self.ok, 2)
    def missed(self): self._blink(self.warn, 1, on=0.8)
    error = missed

    def armed(self):
        """Solid: ready for the camera's real shutter."""
        if self.warn:
            self.warn.off()
        if self.ok:
            self.ok.on()

    def off(self):
        for led in (self.ok, self.warn):
            if led:
                led.off()

    def close(self):
        for led in (self.ok, self.warn):
            if led:
                led.close()


def test_led(cfg: dict, pins: list[int]) -> None:
    """Light each LED colour in turn so you can see which pin is which."""
    from gpiozero import LED

    used = {cfg["buttons"]["shutter_gpio"], cfg["buttons"]["power_gpio"]} - {-1}
    clash = [p for p in pins if p in used]
    if clash:
        raise SystemExit(f"GPIO{clash[0]} is a button pin in config.toml; "
                         "driving it as an LED could damage the Pi. Not running.")
    leds = [(p, LED(p)) for p in pins]
    print("Lighting each pin in turn, 3 rounds. Don't press any buttons.", flush=True)
    try:
        for _ in range(3):
            for pin, led in leds:
                print(f"  GPIO{pin} (header pin {PHYSICAL.get(pin, '?')}) on", flush=True)
                led.on()
                time.sleep(1.0)
                led.off()
                time.sleep(0.4)
    finally:
        for _, led in leds:
            led.close()
    print("Done. Note which colour went with which GPIO. [feedback] ok_gpio is the "
          "'good' colour (armed/saved), warn_gpio the 'miss' colour; swap the two "
          "numbers in config.toml if they're the wrong way round.")


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
