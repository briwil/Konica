"""Hardware-free tests of the lens-mode trigger and raw merge.

Run on any machine with numpy + Pillow:  python3 tests/test_logic.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from konicaback import camera as cam_mod  # noqa: E402
from konicaback.camera import MISSED, Camera, Shot, merge_raw  # noqa: E402
from konicaback.config import load  # noqa: E402
from konicaback.storage import Storage  # noqa: E402

W, H = 8, 4  # tiny fake sensor


class FakeRequest:
    def __init__(self, luma, raw_value=256, main_value=0):
        self.luma = luma
        self.released = False
        self.config = {"raw": {"format": "SRGGB12", "size": (W, H), "stride": W * 2}}
        self.raw = np.full((H, W), raw_value, dtype=np.uint16)
        self.main = np.full((H, W, 3), main_value, dtype=np.uint8)

    def make_array(self, name):
        if name == "lores":
            w, h = cam_mod.LORES
            return np.full((h * 3 // 2, w), self.luma, dtype=np.uint8)
        if name == "raw":
            return self.raw.view(np.uint8)
        return self.main

    def get_metadata(self):
        return {"SensorBlackLevels": (4096, 4096, 4096, 4096)}

    def release(self):
        self.released = True


class FakePicam:
    def __init__(self, frames):
        self.frames = list(frames)

    def capture_request(self):
        return self.frames.pop(0)


def make_cam(frames, require_button=True):
    cfg = load(None)
    cfg["lens_mode"]["require_button"] = require_button
    c = Camera.__new__(Camera)
    c.picam2 = FakePicam(frames)
    c.lens = cfg["lens_mode"]
    c.window = 1.0
    c.baseline = None
    c.frames = 0
    c.armed_at = None
    c.mode = "lens"
    return c


def test_ignores_light_when_not_armed():
    frames = [FakeRequest(2) for _ in range(4)] + [FakeRequest(90), FakeRequest(2)]
    c = make_cam(frames)
    results = [c.poll_lens() for _ in range(6)]
    assert all(r is None for r in results), results
    assert all(f.released for f in frames)


def test_armed_shot_single_frame():
    frames = [FakeRequest(2) for _ in range(4)] + [FakeRequest(90), FakeRequest(2)]
    c = make_cam(frames)
    for _ in range(4):
        assert c.poll_lens() is None
    c.arm()
    shot = c.poll_lens()
    assert isinstance(shot, Shot) and not shot.merged
    assert frames[5].released  # next frame checked, was dark, released
    assert c.armed_at is None


def test_split_frames_merge():
    frames = [FakeRequest(2) for _ in range(4)] + [FakeRequest(50), FakeRequest(40)]
    c = make_cam(frames)
    for _ in range(4):
        c.poll_lens()
    c.arm()
    shot = c.poll_lens()
    assert shot.merged


def test_auto_mode_no_button():
    frames = [FakeRequest(2) for _ in range(4)] + [FakeRequest(90), FakeRequest(2)]
    c = make_cam(frames, require_button=False)
    for _ in range(4):
        c.poll_lens()
    assert isinstance(c.poll_lens(), Shot)


def test_missed_after_arm_expires():
    import time
    frames = [FakeRequest(2) for _ in range(6)]
    c = make_cam(frames)
    for _ in range(4):
        c.poll_lens()
    c.arm(time.monotonic() - 10)  # pressed long ago
    assert c.poll_lens() == MISSED


def test_merge_raw_math():
    a = FakeRequest(0, raw_value=256)   # black level only
    b = FakeRequest(0, raw_value=1256)  # black + 1000
    a.raw[0, 0] = 4000
    b.raw[0, 0] = 4000                  # would overflow -> clipped
    out = merge_raw(a, b).view(np.uint16).reshape(H, W)
    assert out[1, 1] == 1256, out[1, 1]  # 256 + 1256 - 256
    assert out[0, 0] == 4095


def test_merged_image_max():
    a = FakeRequest(0, main_value=0)
    b = FakeRequest(0, main_value=0)
    a.main[0] = (10, 20, 200)  # B,G,R
    b.main[1] = (30, 40, 50)
    img = Shot(None, a, second=b).image()
    assert img.getpixel((0, 0)) == (200, 20, 10)
    assert img.getpixel((0, 1)) == (50, 40, 30)


def test_storage_numbering():
    with tempfile.TemporaryDirectory() as d:
        s = Storage(d, "KS3_")
        b1 = s.next_base()
        (Path(str(b1) + ".jpg")).write_text("x")
        b2 = s.next_base()
        assert b1.name == "KS3_00001" and b2.name == "KS3_00002"


def test_config_example_parses():
    cfg = load(Path(__file__).resolve().parent.parent / "config.example.toml")
    assert cfg["capture"]["mode"] == "lens"
    assert cfg["buttons"]["shutter_gpio"] == 17 and cfg["buttons"]["power_gpio"] == -1
    fb = cfg["feedback"]
    assert (fb["red_gpio"], fb["green_gpio"], fb["blue_gpio"]) == (27, 18, 22)


def test_pin_collision_rejected():
    import tomllib  # noqa: F401
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "c.toml"
        f.write_text("[feedback]\ngreen_gpio = 17\n")  # 17 is the shutter button
        try:
            load(f)
        except ValueError as e:
            assert "GPIO17" in str(e)
        else:
            raise AssertionError("pin collision was not rejected")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} passed")
