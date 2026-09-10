"""Behavior layer (spec section 8): people + voice direction + conversation events in, eyes,
phrases, banana offers and base motion out. Only the Director talks to Robot, Base and Eyes.

Pure and clock-driven so tests script it: ``tick()`` runs at ~10 Hz with the current scene,
the ``on_*`` hooks receive conversation events (``handle_event`` maps RobotClient events onto
them), and ``on_tool`` is what ``VoiceSession`` calls for ``look_at_speaker``/``set_expression``.
"""

from __future__ import annotations

import inspect
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bob.hardware.eyes import EXPRESSIONS, EyeState
from bob.persona import GREETINGS, STOCK_PHRASES, pick_greeting
from bob.vision.faces import Person

# Rules from spec section 8.
GREET_STABLE_S = 1.0  # same recognized name this long before greeting
SEEN_COOLDOWN_S = 600  # no re-greeting within this window
LOVE_S = 3.0  # "banana" heard or said -> love this long
SONG_S = 20.0  # heart eyes while Bob sings
IDLE_SLEEPY_S = 60  # nothing happening this long -> sleepy
GIFT_MIN_S = 240  # random banana gift window while engaged
GIFT_MAX_S = 480
GIFT_KNOWN_FACTOR = 3  # recognized people get 3x the odds (interval / 3)
TURN_DEADBAND_DEG = 20  # no turning toward voices inside +-this
CLOSE_SIZE = 0.35  # bbox height / frame height that counts as "close"
UNKNOWN_HELLO_S = 3.0  # unknown face stable this long -> maybe a hello
UNKNOWN_HELLO_P = 0.2

# Motion (P1/P2). DoA angle convention is 0..359; DOA_SIGN flips left/right after the bench check.
DOA_SIGN = 1
TURN_GAIN = 0.5  # deg/s per degree of bearing
MAX_OMEGA_DEG_S = 40.0
APPROACH_VX = 0.1  # m/s creep toward a far person
APPROACH_OMEGA_GAIN = 30.0  # deg/s per unit of face x-offset (-1..1)

# Attention and gaze timing.
LISTEN_S = 2.0  # "curious" after speech is detected
VOICE_GAZE_S = 2.0  # keep looking toward the last voice this long
LOOK_AT_S = 3.0  # look_at_speaker tool: hold the gaze this long
TOOL_EXPRESSION_S = 5.0  # set_expression tool: hold the expression this long
GAZE_BEARING_FULL_DEG = 60.0  # bearing that maps to gx = +-1
GAZE_Y_SCALE = 0.6
YAWN_EVERY_S = 120.0
SLEEPY_BLINK_PERIOD_S = 4.0
SLEEPY_BLINK_S = 0.4

DIRECTOR_TOOLS = {"look_at_speaker", "set_expression"}
NAMED_GREETINGS = tuple(g for g in GREETINGS if "{name}" in g)
GREETING_TRIES = 8


def clamp(value: float, low: float, high: float) -> float:
    return min(max(float(value), low), high)


def bearing_from_doa(angle: int | float) -> float:
    """DoA angle 0..359 -> signed bearing relative to the front, positive = clockwise/right."""
    return DOA_SIGN * (((float(angle) + 180.0) % 360.0) - 180.0)


def omega_for_bearing(
    bearing_deg: float, deadband_deg: float = TURN_DEADBAND_DEG, max_omega: float = MAX_OMEGA_DEG_S
) -> float:
    """P controller: 0 inside the deadband, else TURN_GAIN * bearing clamped to +-max_omega."""
    if abs(bearing_deg) <= deadband_deg:
        return 0.0
    return clamp(TURN_GAIN * bearing_deg, -max_omega, max_omega)


async def _call(func: Callable[..., Any] | None, *args) -> Any:
    """Call a sync or async callable."""
    if func is None:
        return None
    result = func(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


@dataclass
class Console:
    """Phone console state the server keeps up to date. Both default to the safe value."""

    armed: bool = False
    estop: bool = False


class PhrasePlayer:
    """``say(text_or_key)``: a STOCK_PHRASES key plays its cached WAV when one exists (via ``play``),
    otherwise the phrase text (or any other line) goes to ``say`` for live TTS."""

    def __init__(
        self,
        say: Callable[[str], Awaitable | None],
        play: Callable[[Path], Awaitable | None] | None = None,
        phrases_dir: str | Path = "assets/phrases",
    ):
        self._say = say
        self._play = play
        self.phrases_dir = Path(phrases_dir)

    async def say(self, text_or_key: str) -> str:
        """Returns what was spoken: the cached file name or the text handed to TTS."""
        if text_or_key in STOCK_PHRASES:
            path = self.phrases_dir / f"{text_or_key}.wav"
            if self._play is not None and path.exists():
                await _call(self._play, path)
                return path.name
            text = STOCK_PHRASES[text_or_key]
        else:
            text = text_or_key
        await _call(self._say, text)
        return text


class FakePhrasePlayer:
    """Records every ``say`` call (keys stay keys so tests can assert on them)."""

    def __init__(self):
        self.calls: list[str] = []

    async def say(self, text_or_key: str) -> str:
        self.calls.append(text_or_key)
        return text_or_key


class Director:
    def __init__(
        self,
        robot,
        eyes,
        phrases,
        settings,
        base=None,
        doa=None,
        console=None,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self.robot = robot
        self.eyes = eyes
        self.phrases = phrases
        self.settings = settings
        self.base = base
        self.doa = doa
        self.console = console if console is not None else Console()
        self._clock = clock
        self.rng = rng if rng is not None else random.Random(0)

        now = clock()
        self.eye_state = EyeState()
        self._eyes_sent = False
        self.engaged: Person | None = None
        self._offering = False
        self._moving = False
        self._estop_stopped = False
        self._speaking = False
        self._last_activity = now
        self._listening_until = -math.inf
        self._voice_gaze_until = -math.inf
        self._look_at_until = -math.inf
        self._last_bearing: float | None = None
        self._override: tuple[str, float] | None = None  # (expression, until)
        self._seen: dict[str, float] = {}
        self._candidate: str | None = None
        self._candidate_since = now
        self._greeted_candidate = False
        self._unknown_since: float | None = None
        self._unknown_rolled = False
        self._next_gift_at: float | None = None
        self._sleepy_since: float | None = None
        self._next_yawn_at = -math.inf

    # -- state ------------------------------------------------------------------------

    @property
    def mode(self) -> str:
        if self._offering:
            return "offering"
        if self.engaged is not None:
            return "engaged"
        if self._moving:
            return "wandering"
        return "idle"

    @property
    def speaking(self) -> bool:
        return self._speaking

    def snapshot(self) -> dict:
        return {
            "mode": self.mode,
            "person": self.engaged.name if self.engaged else None,
            "eyes": self.eye_state.to_dict(),
            "speaking": self._speaking,
            "moving": self._moving,
        }

    # -- conversation hooks ----------------------------------------------------------------

    def _touch(self, now: float | None = None) -> float:
        now = self._clock() if now is None else now
        self._last_activity = now
        return now

    def on_speech_start(self) -> None:
        now = self._touch()
        self._listening_until = now + LISTEN_S

    def on_transcript(self, text: str) -> None:
        now = self._touch()
        if "banana" in (text or "").lower():
            self._override = ("love", now + LOVE_S)

    def on_assistant_text(self, text: str) -> None:
        now = self._touch()
        if "banana" in (text or "").lower():
            self._override = ("love", now + LOVE_S)

    def on_speaking(self, is_speaking: bool) -> None:
        self._touch()
        self._speaking = bool(is_speaking)

    async def on_tool(self, name: str, args: dict | None) -> dict:
        """What VoiceSession's ``on_tool`` receives for look_at_speaker / set_expression."""
        now = self._touch()
        args = args or {}
        if name == "set_expression":
            expression = args.get("expression")
            if expression not in EXPRESSIONS:
                return {"accepted": False, "reason": "expression must be one of: " + ", ".join(EXPRESSIONS)}
            self._override = (expression, now + TOOL_EXPRESSION_S)
            return {"accepted": True, "expression": expression}
        if name == "sing_song":
            self._override = ("love", now + SONG_S)
            spoken = await self.phrases.say("song")
            return {"accepted": True, "played": str(spoken).endswith(".wav"), "spoken": spoken}
        if name == "look_at_speaker":
            self._look_at_until = now + LOOK_AT_S
            bearing = None if self._last_bearing is None else round(self._last_bearing)
            return {"accepted": True, "bearing": bearing}
        return {"accepted": False, "reason": f"Unknown director tool {name!r}."}

    def handle_event(self, event: dict) -> None:
        """Map RobotClient / VoiceSession events onto the hooks. Unknown kinds are ignored."""
        kind = event.get("type")
        if kind == "transcript":
            self.on_transcript(event.get("text", ""))
        elif kind == "assistant_delta":
            self.on_assistant_text(event.get("text", ""))
        elif kind == "playback_started":
            self.on_speaking(True)
        elif kind == "playback_finished":
            self.on_speaking(False)
        elif kind in ("barge_in", "speech_start"):
            self.on_speech_start()

    # -- tick ----------------------------------------------------------------------------

    async def tick(
        self, persons: list[Person], doa: tuple[int, bool] | None = None, now: float | None = None
    ):
        now = self._clock() if now is None else now
        if doa is None and self.doa is not None:
            doa = self.doa.read()
        speech = self._observe_doa(doa, now)
        target = self._pick_person(persons)
        if target is not None:
            self._last_activity = now
        await self._update_engagement(target, now)
        self._update_base(target, speech, now)
        await self._update_eyes(target, now)

    def _observe_doa(self, doa, now: float) -> bool:
        if doa is None:
            return False
        angle, speech = doa
        if not speech:
            return False
        self._last_bearing = bearing_from_doa(angle)
        self._voice_gaze_until = now + VOICE_GAZE_S
        self._listening_until = max(self._listening_until, now + LISTEN_S)
        self._last_activity = now
        return True

    @staticmethod
    def _pick_person(persons: list[Person]) -> Person | None:
        if not persons:
            return None
        return max(persons, key=lambda p: (p.size, p.name is not None))

    # -- engagement: greetings, hellos, gifts ---------------------------------------------

    async def _update_engagement(self, target: Person | None, now: float) -> None:
        was_engaged = self.engaged is not None
        self.engaged = target
        if self._offering and (self.robot.state.phase == "idle" or self.robot.state.stopped):
            self._offering = False
            self._schedule_gift(target, now)
        if target is None:
            self._candidate = None
            self._greeted_candidate = False
            self._unknown_since = None
            self._unknown_rolled = False
            self._next_gift_at = None
            return
        if not was_engaged:
            self._schedule_gift(target, now)
        await self._maybe_greet(target, now)
        await self._maybe_hello_unknown(target, now)
        await self._maybe_gift(target, now)

    async def _maybe_greet(self, target: Person, now: float) -> None:
        name = target.name
        if name != self._candidate:
            self._candidate, self._candidate_since, self._greeted_candidate = name, now, False
        if name is None or self._greeted_candidate:
            return
        if now - self._candidate_since < GREET_STABLE_S:
            return
        self._greeted_candidate = True
        if self._seen_recently(name, now):
            return
        self._seen[name] = now
        self._touch(now)
        await self.phrases.say(self._greeting(name))

    def _greeting(self, name: str) -> str:
        """A greeting that actually says the name: GREETINGS also holds nameless lines for strangers."""
        for _ in range(GREETING_TRIES):
            line = pick_greeting(name, self.rng)
            if name in line:
                return line
        return self.rng.choice(NAMED_GREETINGS).format(name=name)

    def _seen_recently(self, name: str, now: float) -> bool:
        last = self._seen.get(name)
        return last is not None and now - last < SEEN_COOLDOWN_S

    async def _maybe_hello_unknown(self, target: Person, now: float) -> None:
        if target.name is not None:
            self._unknown_since, self._unknown_rolled = None, False
            return
        if self._unknown_since is None:
            self._unknown_since = now
        if self._unknown_rolled or now - self._unknown_since < UNKNOWN_HELLO_S:
            return
        self._unknown_rolled = True
        if self.rng.random() < UNKNOWN_HELLO_P:
            await self.phrases.say("bello")

    def _schedule_gift(self, target: Person | None, now: float) -> None:
        if target is None:
            self._next_gift_at = None
            return
        interval = self.rng.uniform(GIFT_MIN_S, GIFT_MAX_S)
        if target.name is not None:
            interval /= GIFT_KNOWN_FACTOR
        self._next_gift_at = now + interval

    async def _maybe_gift(self, target: Person, now: float) -> None:
        if self._offering or self._next_gift_at is None or now < self._next_gift_at:
            return
        state = self.robot.state
        if state.banana != "compartment" or state.phase != "idle" or state.stopped:
            self._schedule_gift(target, now)  # try again later, never right after a reload
            return
        self._next_gift_at = None
        await self.phrases.say("para_tu")
        result = await self.robot.command("offer_banana")
        if isinstance(result, dict) and result.get("accepted"):
            self._offering = True
            self._override = ("love", now + LOVE_S)
        else:
            self._schedule_gift(target, now)

    # -- base motion -------------------------------------------------------------------

    def _update_base(self, target: Person | None, speech: bool, now: float) -> None:
        base, console = self.base, self.console
        if base is None:
            return
        if console.estop:
            if not self._estop_stopped:
                base.stop()
                self._estop_stopped = True
                self._moving = False
            return
        self._estop_stopped = False
        allowed = (
            self.settings.real_hardware
            and console.armed
            and not self._offering
            and self.robot.state.phase == "idle"
            and not self.robot.state.stopped
        )
        command = self._motion_command(target, speech) if allowed else None
        if command is not None:
            base.drive(*command)
            self._moving = True
        elif self._moving:
            base.stop()
            self._moving = False

    def _motion_command(self, target: Person | None, speech: bool) -> tuple[float, float, float] | None:
        if target is not None:
            if target.size >= CLOSE_SIZE:
                return None
            omega = clamp(APPROACH_OMEGA_GAIN * target.cx, -MAX_OMEGA_DEG_S, MAX_OMEGA_DEG_S)
            return (APPROACH_VX, 0.0, omega)
        if speech and self._last_bearing is not None:
            omega = omega_for_bearing(self._last_bearing)
            if omega != 0.0:
                return (0.0, 0.0, omega)
        return None

    # -- eyes ------------------------------------------------------------------------------

    def _expression(self, target: Person | None, now: float) -> str:
        if self._override is not None:
            expression, until = self._override
            if now < until:
                return expression
            self._override = None
        if self._speaking:
            return "happy"
        if now < self._listening_until:
            return "curious"
        if target is not None:
            return "happy"
        if now - self._last_activity >= IDLE_SLEEPY_S:
            return "sleepy"
        return "neutral"

    def _gaze(self, target: Person | None, now: float) -> tuple[float, float]:
        if target is not None:
            return clamp(target.cx, -1, 1), clamp(target.cy * GAZE_Y_SCALE, -1, 1)
        if self._last_bearing is not None and (now < self._voice_gaze_until or now < self._look_at_until):
            return clamp(self._last_bearing / GAZE_BEARING_FULL_DEG, -1, 1), 0.0
        return 0.4 * math.sin(now * 0.5), 0.15 * math.sin(now * 0.31)

    async def _update_eyes(self, target: Person | None, now: float) -> None:
        expression = self._expression(target, now)
        gx, gy = self._gaze(target, now)
        blink, pupil = False, 1.0
        if expression == "sleepy":
            if self._sleepy_since is None:
                self._sleepy_since = now
                self._next_yawn_at = now
            blink = (now - self._sleepy_since) % SLEEPY_BLINK_PERIOD_S < SLEEPY_BLINK_S
            pupil = 0.8
            if now >= self._next_yawn_at:
                self._next_yawn_at = now + YAWN_EVERY_S
                await self.phrases.say("yawn")
        else:
            self._sleepy_since = None
            if expression == "love":
                pupil = 1.3
        state = EyeState(expression=expression, gx=gx, gy=gy, blink=blink, pupil=pupil)
        if not self._eyes_sent or state != self.eye_state:
            self.eye_state = state
            self._eyes_sent = True
            self.eyes.set(state)
