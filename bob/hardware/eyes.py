"""Serial link to the two ESP32 eye displays and the shared expression vocabulary.

Protocol (spec section 6): one JSON line per update at up to 20 Hz, e.g.
``{"e":"happy","gx":0.3,"gy":-0.1,"blink":false,"p":1.0}``. The firmware interpolates,
so dropping intermediate states while coalescing is harmless. The browser sim in
``web/eyes-sim`` consumes the same payload through the ``eyes`` websocket event.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

EXPRESSIONS = ("neutral", "curious", "happy", "love", "sleepy", "surprised", "sad", "angry_playful")
MAX_RATE_HZ = 20
MIN_INTERVAL_S = 1.0 / MAX_RATE_HZ
GAZE_RANGE = (-1.0, 1.0)
PUPIL_RANGE = (0.3, 2.0)


def _clamp(value, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Eye numbers must be numeric")
    return min(max(float(value), low), high)


@dataclass
class EyeState:
    expression: str = "neutral"
    gx: float = 0.0
    gy: float = 0.0
    blink: bool = False
    pupil: float = 1.0

    def to_dict(self) -> dict:
        """Wire form with fixed key order e, gx, gy, blink, p and 2-decimal rounding."""
        return {
            "e": self.expression,
            "gx": round(float(self.gx), 2),
            "gy": round(float(self.gy), 2),
            "blink": bool(self.blink),
            "p": round(float(self.pupil), 2),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict, base: EyeState | None = None) -> EyeState:
        """Build a validated state from wire keys; missing keys fall back to ``base``."""
        base = base or cls()
        expression = data.get("e", base.expression)
        if expression not in EXPRESSIONS:
            raise ValueError(f"Unknown expression {expression!r}")
        blink = data.get("blink", base.blink)
        if not isinstance(blink, bool):
            raise ValueError("blink must be a boolean")
        return cls(
            expression=expression,
            gx=_clamp(data.get("gx", base.gx), *GAZE_RANGE),
            gy=_clamp(data.get("gy", base.gy), *GAZE_RANGE),
            blink=blink,
            pupil=_clamp(data.get("p", base.pupil), *PUPIL_RANGE),
        )


def _open_pyserial(port: str, baud: int):
    import serial  # lazy: pyserial is only installed with the robot group

    return serial.Serial(port, baud, timeout=0.3, write_timeout=0.3)


class Eyes:
    """Writes eye states to every eye board. Missing ports degrade to a log line, never an error."""

    def __init__(
        self,
        ports: tuple[str, ...],
        baud: int = 115200,
        clock: Callable[[], float] = time.monotonic,
        serial_factory: Callable[[str, int], object] = _open_pyserial,
        min_interval: float = MIN_INTERVAL_S,
    ):
        self.ports = tuple(ports)
        self.baud = baud
        self._clock = clock
        self._serial_factory = serial_factory
        self._min_interval = min_interval
        self._links: dict[str, object] = {}
        self._last_sent_at: float | None = None
        self._pending: EyeState | None = None
        self.last: EyeState | None = None

    @property
    def connected(self) -> bool:
        return bool(self._links)

    def open(self) -> None:
        for port in self.ports:
            try:
                self._links[port] = self._serial_factory(port, self.baud)
            except Exception as exc:  # SerialException, OSError, ImportError
                log.warning("Eye port %s unavailable: %s", port, str(exc)[:120])
        if not self._links:
            log.warning("No eye boards connected (ports=%s); eyes run blind.", self.ports)

    def set(self, state: EyeState) -> bool:
        """Queue a state. Sends now if the 20 Hz budget allows, else keeps the newest for later.

        Returns True when a line was written in this call.
        """
        now = self._clock()
        if self._last_sent_at is not None and now - self._last_sent_at < self._min_interval:
            self._pending = state
            return False
        self._pending = None
        self._write(state, now)
        return True

    def flush(self) -> bool:
        """Send a coalesced state once the rate budget allows (call from a periodic tick)."""
        if self._pending is None:
            return False
        now = self._clock()
        if self._last_sent_at is not None and now - self._last_sent_at < self._min_interval:
            return False
        state, self._pending = self._pending, None
        self._write(state, now)
        return True

    def _write(self, state: EyeState, now: float) -> None:
        self.last = state
        self._last_sent_at = now
        self._write_line(state.to_json())

    def _write_line(self, line: str) -> None:
        payload = (line + "\n").encode()
        for port, link in list(self._links.items()):
            try:
                link.write(payload)
            except Exception as exc:
                log.warning("Eye port %s dropped: %s", port, str(exc)[:120])
                self._drop(port)

    def _drop(self, port: str) -> None:
        link = self._links.pop(port, None)
        try:
            if link is not None:
                link.close()
        except Exception:
            pass

    def ping(self) -> dict[str, dict]:
        """Ask every board for ``{"ok":1,"side":"L","fps":N}``; unreachable boards report ``ok: 0``."""
        replies: dict[str, dict] = {}
        request = b'{"cmd":"ping"}\n'
        for port in self.ports:
            link = self._links.get(port)
            if link is None:
                replies[port] = {"ok": 0, "error": "not connected"}
                continue
            try:
                link.write(request)
                raw = link.readline()
                text = raw.decode(errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
                replies[port] = json.loads(text) if text else {"ok": 0, "error": "no reply"}
            except Exception as exc:
                replies[port] = {"ok": 0, "error": str(exc)[:120]}
        return replies

    def close(self) -> None:
        for port in list(self._links):
            self._drop(port)
        self._pending = None


class FakeEyes:
    """Records every state so the Director tests can assert on what the eyes were told."""

    def __init__(self):
        self.states: list[EyeState] = []
        self.opened = False
        self.closed = False

    @property
    def last(self) -> EyeState | None:
        return self.states[-1] if self.states else None

    @property
    def connected(self) -> bool:
        return self.opened and not self.closed

    def open(self) -> None:
        self.opened = True

    def set(self, state: EyeState) -> bool:
        self.states.append(state)
        return True

    def flush(self) -> bool:
        return False

    def ping(self) -> dict[str, dict]:
        return {"fake": {"ok": 1, "side": "L", "fps": 60}}

    def close(self) -> None:
        self.closed = True
