"""Buttons and the optional LED/buzzer."""
from __future__ import annotations

import functools
import logging
import queue
import time
from pathlib import Path

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


def test_led(cfg: dict, pins: list[int], config_path: str | None = None) -> None:
    """Light one pin at a time and ask which colour it is, then save the answers.

    No timing to match: each pin stays lit until you answer."""
    import re
    from gpiozero import LED

    used = {cfg["buttons"]["shutter_gpio"], cfg["buttons"]["power_gpio"]} - {-1}
    clash = [p for p in pins if p in used]
    if clash:
        raise SystemExit(f"GPIO{clash[0]} is a button pin in config.toml; "
                         "driving it as an LED could damage the Pi. Not running.")
    colour = {"r": "red", "g": "green", "b": "blue"}
    answers = {}
    print("Each pin stays lit until you answer. Don't press any buttons.\n", flush=True)
    for pin in pins:
        led = LED(pin)
        try:
            led.on()
            while True:
                a = input(f"GPIO{pin} (header pin {PHYSICAL.get(pin, '?')}) is ON now. "
                          "What colour? r = red, g = green, b = blue, n = nothing visible (a faint colour still counts): "
                          ).strip().lower()[:1]
                if a in ("r", "g", "b", "n"):
                    break
                print("  Please type r, g, b or n.")
        finally:
            led.off()
            led.close()
        answers[pin] = a

    chosen = {}
    for pin, a in answers.items():
        if a == "n":
            continue
        if colour[a] in chosen:
            print(f"\nYou gave {colour[a]} for two pins (GPIO{chosen[colour[a]]} and "
                  f"GPIO{pin}). Nothing saved; run the test again.")
            return
        chosen[colour[a]] = pin
    lines = {name: chosen.get(name, -1) for name in colour.values()}
    print("\nResult:")
    for name, pin in lines.items():
        print(f"  {name}_gpio = {pin}")
    if not config_path or not Path(config_path).exists():
        print("No config.toml found; put those lines under [feedback] yourself.")
        return
    text = Path(config_path).read_text()
    for name, pin in lines.items():
        text, n = re.subn(rf"(?m)^(\s*{name}_gpio\s*=\s*)-?\d+", rf"\g<1>{pin}", text)
        if n == 0:
            print(f"Couldn't find {name}_gpio in {config_path}; add it by hand.")
    Path(config_path).write_text(text)
    print(f"Saved to {config_path}.")


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
