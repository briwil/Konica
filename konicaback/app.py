"""Main loop: wait for buttons / light, capture, save."""
from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess

from .camera import MISSED, Camera, Shot
from .storage import HeifWorker, Storage

log = logging.getLogger(__name__)

MIN_FREE_MB = 300


def make_heif_worker(cfg: dict):
    if "heif" not in cfg["capture"]["formats"]:
        return None
    try:
        w = HeifWorker(int(cfg["capture"]["heif_quality"]))
    except ImportError:
        log.warning("HEIF requested but pillow-heif isn't installed; skipping HEIF")
        return None
    w.start()
    return w


def save_shot(shot: Shot, storage: Storage, cfg: dict, heif) -> list:
    fmts = cfg["capture"]["formats"]
    base = storage.next_base()
    written = []
    heif_image = None
    try:
        if heif is not None:
            heif_image = shot.image()
        if "dng" in fmts:
            written += shot.save_dng(base.with_suffix(".dng"))
        if "jpg" in fmts:
            written += shot.save_jpeg(base.with_suffix(".jpg"), int(cfg["capture"]["jpeg_quality"]))
    finally:
        shot.release()  # give buffers back to the camera as early as possible
    if heif_image is not None:
        heif.submit(heif_image, base.with_suffix(".heic"))
    os.sync()  # the battery can die at any moment
    log.info("Saved %s", ", ".join(p.name for p in written) or "(nothing)")
    return written


def run(cfg: dict, use_gpio: bool = True) -> None:
    from .hardware import Buttons, Feedback

    events: queue.Queue = queue.Queue()
    storage = Storage(cfg["capture"]["photo_dir"], cfg["capture"]["filename_prefix"])
    heif = make_heif_worker(cfg)
    feedback = Feedback(cfg["feedback"]["gpio"] if use_gpio else -1)
    cam = Camera(cfg)
    mode = cfg["capture"]["mode"]

    def on_shutter(t):
        if mode == "lens":
            cam.arm(t)
            feedback.armed()

    buttons = Buttons(cfg, events, on_shutter=on_shutter) if use_gpio else None
    signal.signal(signal.SIGTERM, lambda *_: events.put(("quit", 0)))
    signal.signal(signal.SIGINT, lambda *_: events.put(("quit", 0)))

    cam.start(mode)
    feedback.ready()
    poweroff = False
    try:
        while True:
            if mode == "lens":
                result = cam.poll_lens()  # blocks for about one exposure window
                if isinstance(result, Shot):
                    feedback.off()  # photo caught; LED off while saving
                    _handle(result, storage, cfg, heif, feedback)
                elif result == MISSED:
                    feedback.off()
                    feedback.missed()
                ev = _drain(events)
            else:
                try:
                    ev = events.get(timeout=0.5)
                except queue.Empty:
                    ev = None
                if ev and ev[0] == "shutter":
                    _handle(cam.capture_sensor(), storage, cfg, heif, feedback)
                    ev = _drain(events)  # drop presses made while saving

            if ev and ev[0] == "quit":
                break
            if ev and ev[0] == "poweroff":
                poweroff = True
                break
    finally:
        log.info("Stopping")
        cam.close()
        if heif is not None:
            heif.drain()
        if buttons:
            buttons.close()
        feedback.close()
        os.sync()
    if poweroff:
        log.info("Power button held: shutting down")
        subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)


def _handle(shot, storage, cfg, heif, feedback) -> None:
    if storage.free_mb() < MIN_FREE_MB:
        log.error("SD card nearly full (%.0f MB free); not saving", storage.free_mb())
        shot.release()
        feedback.error()
        return
    try:
        save_shot(shot, storage, cfg, heif)
        feedback.saved()
    except Exception:
        log.exception("Saving failed")
        feedback.error()


def _drain(events: queue.Queue):
    """Return the most important pending event (quit > poweroff > shutter)."""
    found = None
    rank = {"quit": 3, "poweroff": 2, "shutter": 1}
    while True:
        try:
            ev = events.get_nowait()
        except queue.Empty:
            return found
        if found is None or rank[ev[0]] > rank[found[0]]:
            found = ev
