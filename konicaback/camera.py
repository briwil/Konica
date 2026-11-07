"""IMX477 control.

Lens mode (the main one)
------------------------
The Konica's leaf shutter sits in front of the sensor and is closed except
for the instant you fire it. So the sensor streams continuous long exposures
(``window_s``, e.g. 1 s each, back to back) at fixed gain and white balance.
Between shots those frames are black. When the leaf shutter opens, the frame
that is integrating at that moment gets the light.

When you press the back's shutter button, the camera is "armed" for
``arm_seconds``. Any frame overlapping that period which is clearly brighter
than the dark baseline is kept. You don't need to time anything; press the
back's button, then fire the camera.

The sensor has a rolling shutter: each row's exposure starts slightly later
than the row above. If the leaf shutter opens right at a frame boundary, some
rows get the light in one frame and the rest in the next. When that happens
the two raw frames are added together (minus the black level), which gives the
correct image again.

Sensor mode
-----------
The leaf shutter is held open, and the back behaves like an ordinary digital
camera: a low-power preview keeps auto exposure and auto white balance running,
then the back switches to full resolution for the capture.
"""
from __future__ import annotations

import logging
import time

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

LORES = (320, 240)
MISSED = "missed"


# ---------------------------------------------------------------------------
# A captured photo
# ---------------------------------------------------------------------------
class Shot:
    """One photo: a single camera request, or two requests that a split
    exposure landed in. Call release() when finished so the camera gets its
    buffers back."""

    def __init__(self, picam2, request, second=None, luma: float = 0.0):
        self.picam2 = picam2
        self.request = request
        self.second = second
        self.luma = luma

    @property
    def merged(self) -> bool:
        return self.second is not None

    @property
    def metadata(self) -> dict:
        return self.request.get_metadata()

    def save_dng(self, path) -> list:
        if not self.merged:
            self.request.save_dng(str(path))
            return [path]
        try:
            buf = merge_raw(self.request, self.second)
            self.picam2.helpers.save_dng(
                buf, self.metadata, self.request.config["raw"], str(path)
            )
            return [path]
        except Exception:
            log.exception("Raw merge failed; saving both frames separately")
            b = path.with_name(path.stem + "_b" + path.suffix)
            self.request.save_dng(str(path))
            self.second.save_dng(str(b))
            return [path, b]

    def image(self) -> Image.Image:
        """Processed full-resolution RGB image."""
        if not self.merged:
            return self.request.make_image("main")
        # Each row got its light in one frame or the other, and the other
        # frame is near-black there, so a per-pixel maximum rebuilds the image.
        a = self.request.make_array("main")
        b = self.second.make_array("main")
        bgr = np.maximum(a, b)  # libcamera "RGB888" is B,G,R in memory
        del a, b
        b_, g_, r_ = Image.fromarray(bgr, "RGB").split()
        return Image.merge("RGB", (r_, g_, b_))

    def save_jpeg(self, path, quality: int) -> list:
        if not self.merged:
            self.request.save("main", str(path))
        else:
            self.image().save(path, "JPEG", quality=quality)
        return [path]

    def release(self) -> None:
        for r in (self.request, self.second):
            if r is not None:
                try:
                    r.release()
                except Exception:
                    log.exception("Failed to release request")
        self.request = self.second = None


def _bit_depth(fmt: str) -> int:
    digits = "".join(c for c in fmt.split("_")[0] if c.isdigit())
    return int(digits) if digits else 12


def merge_raw(req_a, req_b) -> np.ndarray:
    """Add two unpacked Bayer frames: a + b - black, clipped to the white level.

    Returns a flat uint8 buffer with the same stride layout as the input, so
    Picamera2's own DNG writer can save it."""
    cfg = req_a.config["raw"]
    fmt = str(cfg["format"])
    if "CSI2P" in fmt or "PISP" in fmt:
        raise ValueError(f"raw format {fmt} is packed; can't merge")
    bits = _bit_depth(fmt)
    a = req_a.make_array("raw")
    b = req_b.make_array("raw")
    if a.dtype != np.uint16:
        a = a.view(np.uint16)
        b = b.view(np.uint16)
    black16 = req_a.get_metadata().get("SensorBlackLevels", [256 << (16 - bits)])[0]
    black = int(black16) >> (16 - bits)
    white = (1 << bits) - 1
    out = a.astype(np.int32)
    out += b
    out -= black
    np.clip(out, 0, white, out=out)
    return out.astype(np.uint16).view(np.uint8).ravel()


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
class Camera:
    def __init__(self, cfg: dict):
        from libcamera import Transform
        from picamera2 import Picamera2

        cap = cfg["capture"]
        if cap["tuning"]:
            log.info("Using tuning file %s", cap["tuning"])
            self.picam2 = Picamera2(tuning=Picamera2.load_tuning_file(cap["tuning"]))
        else:
            self.picam2 = Picamera2()
        self.picam2.options["quality"] = int(cap["jpeg_quality"])
        self.transform = Transform(hflip=bool(cap["hflip"]), vflip=bool(cap["vflip"]))
        self.extra = dict(cfg["controls"])
        self.lens = cfg["lens_mode"]

        modes = self.picam2.sensor_modes
        full = max(modes, key=lambda m: m["size"][0] * m["size"][1])
        self.full_size = tuple(full["size"])
        # Unpacked raw (16 bits per pixel) so split frames can be merged.
        self.raw_format = str(full.get("unpacked") or full["format"])
        half = (self.full_size[0] // 2, self.full_size[1] // 2)
        self.binned_size = half if any(tuple(m["size"]) == half for m in modes) else None
        log.info("Sensor %s, full %s, raw %s", self.picam2.camera_properties.get("Model"),
                 self.full_size, self.raw_format)

        self.mode: str | None = None
        self.armed_at: float | None = None

    # -- lifecycle ----------------------------------------------------------
    def start(self, mode: str) -> None:
        self.stop()
        if mode == "lens":
            self._start_lens()
        else:
            self._start_sensor()
        self.mode = mode

    def stop(self) -> None:
        if self.mode is not None:
            self.picam2.stop()
            self.mode = None

    def close(self) -> None:
        self.stop()
        self.picam2.close()

    def _still_config(self, controls: dict, lores: bool, buffer_count: int):
        kwargs = dict(
            main={"size": self.full_size, "format": "RGB888"},
            raw={"size": self.full_size, "format": self.raw_format},
            transform=self.transform,
            buffer_count=buffer_count,
            controls=controls,
        )
        if lores:
            kwargs["lores"] = {"size": LORES, "format": "YUV420"}
        return self.picam2.create_still_configuration(**kwargs)

    # -- sensor mode --------------------------------------------------------
    def _start_sensor(self) -> None:
        p = self.picam2
        raw = {"size": self.binned_size} if self.binned_size else {}
        # Small, 10 fps preview keeps AE/AWB running without wasting battery.
        self.idle_config = p.create_preview_configuration(
            main={"size": (640, 480)}, raw=raw, transform=self.transform,
            buffer_count=3,
            controls={**self.extra, "FrameDurationLimits": (100_000, 100_000)},
        )
        self.still_config = self._still_config(dict(self.extra), lores=False, buffer_count=1)
        p.configure(self.idle_config)
        p.start()
        log.info("Sensor mode ready (lens shutter must be held open)")

    def capture_sensor(self) -> Shot:
        req = self.picam2.switch_mode_and_capture_request(self.still_config)
        md = req.get_metadata()
        log.info("Captured: exposure %s us, gain %.2f, ColourGains %s",
                 md.get("ExposureTime"), md.get("AnalogueGain", 0), md.get("ColourGains"))
        return Shot(self.picam2, req)

    # -- lens mode ----------------------------------------------------------
    def _start_lens(self) -> None:
        t = int(float(self.lens["window_s"]) * 1e6)
        controls = {
            **self.extra,
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": t,
            "AnalogueGain": float(self.lens["gain"]),
            "ColourGains": tuple(float(g) for g in self.lens["wb_gains"]),
            "FrameDurationLimits": (t, t),
        }
        # Two buffers: one can be held while the next frame is still recorded,
        # so a shot split across a frame boundary isn't lost.
        cfg = self._still_config(controls, lores=True, buffer_count=2)
        self.picam2.configure(cfg)
        self.picam2.start()
        self.window = t / 1e6
        self.baseline: float | None = None
        self.frames = 0
        log.info("Lens mode ready: %.2f s windows, gain %.2f", self.window, self.lens["gain"])

    def arm(self, at: float | None = None) -> None:
        """Called from the button thread at the moment of the press."""
        self.armed_at = time.monotonic() if at is None else at

    def _luma(self, req) -> float:
        yuv = req.make_array("lores")
        w, h = LORES
        return float(yuv[:h:4, :w:4].mean())

    def _next_frame(self):
        req = self.picam2.capture_request()
        return req, time.monotonic(), self._luma(req)

    def poll_lens(self):
        """Wait for one frame. Returns a Shot, MISSED, or None."""
        req, end, y = self._next_frame()
        self.frames += 1
        start = end - self.window

        if self.baseline is None or self.frames <= 3:
            self.baseline = y if self.baseline is None else min(self.baseline, y)
            req.release()
            return None

        bright = y >= self.baseline + float(self.lens["trigger_delta"])
        need_button = bool(self.lens["require_button"])
        armed = self.armed_at
        in_window = (not need_button) or (
            armed is not None and end >= armed
            and start <= armed + float(self.lens["arm_seconds"])
        )

        if not bright:
            self.baseline = 0.9 * self.baseline + 0.1 * y
            req.release()
            if need_button and armed is not None and start > armed + float(self.lens["arm_seconds"]):
                self.armed_at = None
                log.info("Armed, but no light from the lens shutter")
                return MISSED
            return None

        if not in_window:
            log.info("Light seen (luma %.1f) but not armed; ignored", y)
            req.release()
            return None

        self.armed_at = None
        log.info("Shot detected: luma %.1f (background %.1f)", y, self.baseline)
        shot = Shot(self.picam2, req, luma=y)

        if self.lens["merge_split_frames"]:
            req2, _, y2 = self._next_frame()
            if y2 >= self.baseline + float(self.lens["trigger_delta"]):
                log.info("Exposure split across two frames (luma %.1f + %.1f); merging", y, y2)
                shot.second = req2
                shot.luma = y + y2
            else:
                req2.release()
        return shot

    def wait_for_lens_shot(self, timeout: float):
        """For the command-line `shoot`: arm now and wait for the lens to fire."""
        self.arm()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.poll_lens()
            if isinstance(r, Shot):
                return r
            if r == MISSED:
                self.arm()  # keep waiting until the timeout
        self.armed_at = None
        return None
