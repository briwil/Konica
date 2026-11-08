"""Load config.toml on top of built-in defaults."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "capture": {
        "mode": "lens",
        "formats": ["dng", "jpg"],
        "jpeg_quality": 93,
        "heif_quality": 85,
        "photo_dir": "~/DCIM",
        "filename_prefix": "KS3_",
        "hflip": False,
        "vflip": False,
        "tuning": "",
    },
    "lens_mode": {
        "require_button": True,
        "arm_seconds": 3.0,
        "window_s": 1.0,
        "gain": 1.0,
        "wb_gains": [2.0, 1.8],
        "trigger_delta": 6.0,
        "merge_split_frames": True,
    },
    "buttons": {
        "shutter_gpio": 18,
        "power_gpio": -1,
        "power_hold_s": 2.0,
        "bounce_s": 0.03,
    },
    "feedback": {"ok_gpio": 22, "warn_gpio": 27},
    "controls": {},
}

MODES = ("lens", "sensor")
FORMATS = ("dng", "jpg", "heif")


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str | Path | None) -> dict:
    data = {}
    if path and Path(path).exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    cfg = _merge(DEFAULTS, data)

    cap = cfg["capture"]
    if cap["mode"] not in MODES:
        raise ValueError(f"capture.mode must be one of {MODES}, got {cap['mode']!r}")
    fmts = [f.lower().replace("jpeg", "jpg").replace("heic", "heif") for f in cap["formats"]]
    bad = [f for f in fmts if f not in FORMATS]
    if bad or not fmts:
        raise ValueError(f"capture.formats must be from {FORMATS}, got {cap['formats']!r}")
    cap["formats"] = fmts

    # A pin used twice is a wiring mistake; driving a button's pin as an LED
    # output can damage the Pi, so refuse to start.
    pins = {}
    for section, keys in (("buttons", ("shutter_gpio", "power_gpio")),
                          ("feedback", ("ok_gpio", "warn_gpio"))):
        for key in keys:
            n = cfg[section][key]
            if n < 0:
                continue
            if n in pins:
                raise ValueError(f"GPIO{n} is used by both {pins[n]} and {section}.{key}")
            pins[n] = f"{section}.{key}"
    return cfg
