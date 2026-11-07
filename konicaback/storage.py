"""File naming, disk space, and background HEIF encoding."""
from __future__ import annotations

import logging
import queue
import shutil
import threading
from pathlib import Path

log = logging.getLogger(__name__)

EXTS = (".dng", ".jpg", ".heic")


class Storage:
    def __init__(self, photo_dir: str, prefix: str):
        self.dir = Path(photo_dir).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.counter = self.dir / ".counter"

    def next_base(self) -> Path:
        """Return e.g. ~/DCIM/KS3_00042 (no extension), never reusing a number."""
        try:
            n = int(self.counter.read_text().strip()) + 1
        except (FileNotFoundError, ValueError):
            n = 1
        while any((self.dir / f"{self.prefix}{n:05d}{e}").exists() for e in EXTS):
            n += 1
        self.counter.write_text(str(n))
        return self.dir / f"{self.prefix}{n:05d}"

    def free_mb(self) -> float:
        return shutil.disk_usage(self.dir).free / 1e6


class HeifWorker(threading.Thread):
    """Encodes HEIF in a background thread so the camera is free sooner.

    The queue holds one image, so at most two full-size images are in memory;
    if a third shot arrives first, saving waits. That keeps a 512 MB Pi safe.
    """

    def __init__(self, quality: int):
        super().__init__(daemon=True, name="heif")
        import pillow_heif  # raises ImportError if not installed

        pillow_heif.register_heif_opener()
        self.quality = quality
        self.q: queue.Queue = queue.Queue(maxsize=1)

    def submit(self, image, path: Path) -> None:
        self.q.put((image, path))

    def run(self) -> None:
        while True:
            image, path = self.q.get()
            try:
                tmp = path.with_suffix(".heic.part")
                image.save(tmp, format="HEIF", quality=self.quality)
                tmp.rename(path)
                log.info("Saved %s", path.name)
            except Exception:
                log.exception("HEIF encode failed for %s", path.name)
            finally:
                self.q.task_done()

    def drain(self) -> None:
        self.q.join()
