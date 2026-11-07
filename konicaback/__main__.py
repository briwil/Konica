"""Command line.

    python -m konicaback run            # the service (what systemd runs)
    python -m konicaback shoot          # take one photo from an SSH session
    python -m konicaback buttons        # find which GPIOs the buttons are on
    python -m konicaback led [GPIO]     # blink a pin to find the LED (default 18)
    python -m konicaback info           # camera modes and memory check
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config as config_mod

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="konicaback")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--mode", choices=config_mod.MODES, help="override capture.mode")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="main loop (default)")
    sp = sub.add_parser("shoot", help="take one photo now")
    sp.add_argument("--timeout", type=float, default=30, help="lens mode: seconds to wait")
    sub.add_parser("buttons", help="report GPIO presses")
    lp = sub.add_parser("led", help="blink a GPIO to find the LED")
    lp.add_argument("gpio", type=int, nargs="?", default=18)
    sub.add_parser("info", help="camera and memory info")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cmd = args.cmd or "run"

    if cmd == "buttons":
        from .hardware import find_buttons
        find_buttons()
        return 0

    if cmd == "led":
        from .hardware import test_led
        test_led(args.gpio)
        return 0

    if cmd == "info":
        return info()

    cfg = config_mod.load(args.config)
    if args.mode:
        cfg["capture"]["mode"] = args.mode

    if cmd == "run":
        from .app import run
        run(cfg)
        return 0

    if cmd == "shoot":
        return shoot(cfg, args.timeout)
    return 1


def shoot(cfg: dict, timeout: float) -> int:
    from .app import make_heif_worker, save_shot
    from .camera import Camera
    from .storage import Storage

    storage = Storage(cfg["capture"]["photo_dir"], cfg["capture"]["filename_prefix"])
    heif = make_heif_worker(cfg)
    cam = Camera(cfg)
    mode = cfg["capture"]["mode"]
    cam.start(mode)
    try:
        if mode == "lens":
            print(f"Fire the camera's shutter within {timeout:.0f} s…", flush=True)
            shot = cam.wait_for_lens_shot(timeout)
            if shot is None:
                print("No light detected.")
                return 2
        else:
            shot = cam.capture_sensor()
        for p in save_shot(shot, storage, cfg, heif):
            print(p)
    finally:
        cam.close()
        if heif is not None:
            heif.drain()
    return 0


def info() -> int:
    from picamera2 import Picamera2

    cams = Picamera2.global_camera_info()
    if not cams:
        print("No camera detected. Check the ribbon cable and /boot/firmware/config.txt.")
        return 1
    print("Cameras:", cams)
    p = Picamera2()
    print("Properties:", p.camera_properties)
    print("Sensor modes:")
    for m in p.sensor_modes:
        print("  ", {k: m[k] for k in ("size", "format", "unpacked", "bit_depth", "fps") if k in m})
    p.close()
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(("MemTotal", "MemAvailable", "CmaTotal", "CmaFree")):
                print(line.strip())
    print("\nLens mode at full resolution needs roughly 130 MB of CMA. If CmaTotal is\n"
          "smaller, see 'Memory' in the README.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
