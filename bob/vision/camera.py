"""USB camera capture on a background thread. `cv2` is imported only when a real camera opens."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np


class Camera:
    """Grabs frames continuously; `latest()` returns the newest BGR frame without blocking."""

    def __init__(self, index: int = -1, width: int = 640, height: int = 480):
        self.index = index
        self.width = width
        self.height = height
        self.capture: Any = None
        self.device: str | int | None = None
        self._frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- device discovery -------------------------------------------------

    def _candidates(self) -> list[int]:
        if self.index >= 0:
            return [self.index]
        if sys.platform.startswith("linux"):
            return [i for i in range(10) if Path(f"/dev/video{i}").exists()]
        return [0]

    def _try_open(self, cv2: Any, index: int) -> Any:
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_AVFOUNDATION
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        if sys.platform.startswith("linux"):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        ok, frame = cap.read()
        if not ok or frame is None or frame.size == 0:
            cap.release()
            return None
        return cap

    def open(self) -> "Camera":
        """Open the first working device and start the capture thread. Raises if none works."""
        import cv2

        for index in self._candidates():
            cap = self._try_open(cv2, index)
            if cap is not None:
                self.capture = cap
                self.device = f"/dev/video{index}" if sys.platform.startswith("linux") else index
                break
        if self.capture is None:
            raise RuntimeError(f"no camera found (tried {self._candidates()})")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()
        return self

    # -- frames -----------------------------------------------------------

    def read(self) -> np.ndarray | None:
        """Blocking single grab straight from the device (bypasses the thread)."""
        if self.capture is None:
            return None
        ok, frame = self.capture.read()
        if not ok or frame is None:
            return None
        with self._lock:
            self._frame = frame
        return frame

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return self._frame

    def _loop(self) -> None:
        while not self._stop.is_set() and self.capture is not None:
            ok, frame = self.capture.read()
            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
            else:
                time.sleep(0.02)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def __enter__(self) -> "Camera":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeCamera:
    """Cycles through a list of frames; `latest()` advances by one frame per call. No frames → None."""

    def __init__(self, frames: list[np.ndarray] | None = None):
        self.frames = list(frames or [])
        self.device = "fake"
        self._i = 0

    def open(self) -> "FakeCamera":
        return self

    def read(self) -> np.ndarray | None:
        return self.latest()

    def latest(self) -> np.ndarray | None:
        if not self.frames:
            return None
        frame = self.frames[self._i % len(self.frames)]
        self._i += 1
        return frame

    @property
    def running(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeCamera":
        return self

    def __exit__(self, *exc: object) -> None:
        pass
