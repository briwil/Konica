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
    """One RGB LED (common ground), one GPIO per colour.

    green: armed (solid) and saved (2 blinks)
    red:   nothing captured in time, or an error (1 long blink)
    blue:  ready (1 blink)
    """

    NAMES = ("red", "green", "blue")

    def __init__(self, fb: dict):
        self.leds = {}
        pins = {n: fb.get(f"{n}_gpio", -1) for n in self.NAMES}
        if any(p >= 0 for p in pins.values()):
            from gpiozero import LED
            for name, pin in pins.items():
                if pin >= 0:
                    self.leds[name] = LED(pin)

    def _blink(self, name: str, n: int, on: float = 0.08, off: float = 0.15) -> None:
        led = self.leds.get(name)
        if led:
            led.blink(on_time=on, off_time=off, n=n, background=True)

    def ready(self): self._blink("blue", 1)
    def saved(self): self._blink("green", 2)
    def missed(self): self._blink("red", 1, on=0.8)
    error = missed

    def armed(self):
        """Solid green: ready for the camera's real shutter."""
        self.off()
        if "green" in self.leds:
            self.leds["green"].on()

    def off(self):
        for led in self.leds.values():
            led.off()

    def close(self):
        for led in self.leds.values():
            led.close()


def test_led(cfg: dict, pins: list[int]) -> None:
    """Light each LED pin in turn so you can see which colour is which."""
    from gpiozero import LED

    used = {cfg["buttons"]["shutter_gpio"], cfg["buttons"]["power_gpio"]} - {-1}
    clash = [p for p in pins if p in used]
    if clash:
        raise SystemExit(f"GPIO{clash[0]} is a button pin in config.toml; "
                         "driving it as an LED could damage the Pi. Not running.")
    names = {cfg["feedback"].get(f"{n}_gpio"): n for n in Feedback.NAMES}
    leds = [(p, LED(p)) for p in pins]
    print("Lighting each pin in turn, 3 rounds. Don't press any buttons.", flush=True)
    try:
        for _ in range(3):
            for pin, led in leds:
                label = f"config says {names[pin]}" if pin in names else "not in config"
                print(f"  GPIO{pin} (header pin {PHYSICAL.get(pin, '?')}, {label}) on", flush=True)
                led.on()
                time.sleep(1.0)
                led.off()
                time.sleep(0.4)
    finally:
        for _, led in leds:
            led.close()
    print("Done. Which colour did each GPIO show? Set red_gpio, green_gpio and "
          "blue_gpio in config.toml (under [feedback]) to match.")


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
