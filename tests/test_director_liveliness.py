"""Eye liveliness: blinks, heartbeat, talk wobble and breathing all ride the existing wire protocol."""

import random

from bob.config import Settings
from bob.director import (
    BREATH_PUPIL,
    GREET_STABLE_S,
    HEARTBEAT_PUPIL,
    IDLE_SLEEPY_S,
    SURPRISE_S,
    Director,
    FakePhrasePlayer,
)
from bob.hardware.eyes import PUPIL_RANGE, EyeState, FakeEyes
from bob.robot import Robot, SimHardware
from bob.vision.faces import Person


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def make():
    clock = Clock()
    eyes = FakeEyes()
    director = Director(
        Robot(SimHardware(delay=0)), eyes, FakePhrasePlayer(), Settings(), clock=clock, rng=random.Random(0)
    )
    return director, clock, eyes


def person(name="Ana", cx=0.0, size=0.3):
    return Person(name=name, score=0.9, bbox=(0, 0, 10, 10), cx=cx, cy=0.0, size=size)


async def run(director, clock, seconds, persons=(), doa=None):
    states = []
    for _ in range(int(round(seconds * 10))):
        clock.now = round(clock.now + 0.1, 6)
        await director.tick(list(persons), doa)
        states.append(director.eye_state)
    return states


def blink_edges(states: list[EyeState]) -> int:
    edges, prev = 0, False
    for s in states:
        if s.blink and not prev:
            edges += 1
        prev = s.blink
    return edges


async def test_idle_pupil_breathes_within_a_tiny_range():
    director, clock, _ = make()
    states = await run(director, clock, 5)
    pupils = {round(s.pupil, 3) for s in states}
    assert len(pupils) > 5  # actually moving, not one static value
    assert all(abs(s.pupil - 1.0) <= BREATH_PUPIL + 1e-9 for s in states)
    assert all(s.expression == "neutral" and not s.blink for s in states)


async def test_love_pupil_pulses_like_a_heartbeat():
    director, clock, _ = make()
    director.on_transcript("banana")
    states = await run(director, clock, 2.0)
    low, high = HEARTBEAT_PUPIL
    assert all(s.expression == "love" for s in states)
    pupils = [s.pupil for s in states]
    assert min(pupils) < low + 0.05 and max(pupils) > high - 0.1
    assert all(PUPIL_RANGE[0] <= p <= PUPIL_RANGE[1] for p in pupils)
    ups = sum(1 for a, b in zip(pupils, pupils[1:]) if b > a + 0.02)
    assert ups >= 2  # more than one beat in two seconds


async def test_transcript_and_speech_blink_once_each():
    director, clock, _ = make()
    states = await run(director, clock, 1.0)
    assert blink_edges(states) == 0
    director.on_transcript("hello bob")
    states = await run(director, clock, 1.0)
    assert blink_edges(states) == 1
    assert not states[-1].blink  # the edge is released
    director.on_speech_start()
    states = await run(director, clock, 1.0)
    assert blink_edges(states) == 1


async def test_greeting_a_known_face_double_blinks():
    director, clock, _ = make()
    states = await run(director, clock, GREET_STABLE_S + 1.0, persons=[person("Ana")])
    assert blink_edges(states) == 2


async def test_talking_wobbles_gaze_and_widens_pupils_then_settles():
    director, clock, _ = make()
    still = await run(director, clock, 1.0, persons=[person(cx=0.0)])
    assert all(s.gx == 0.0 for s in still)
    director.on_speaking(True)
    talking = await run(director, clock, 1.0, persons=[person(cx=0.0)])
    assert all(s.expression == "happy" for s in talking)
    assert len({round(s.gx, 3) for s in talking}) > 3
    assert all(abs(s.gx) <= 0.06 + 1e-9 and 1.0 < s.pupil < 1.1 for s in talking)
    director.on_speaking(False)
    settled = await run(director, clock, 0.5, persons=[person(cx=0.0)])
    assert all(s.gx == 0.0 for s in settled)


async def test_barge_in_flashes_surprised_then_listens():
    director, clock, _ = make()
    director.on_speaking(True)
    director.handle_event({"type": "barge_in", "request_id": 3})
    director.handle_event({"type": "playback_finished", "request_id": 3})  # audio loop cancels playback
    states = await run(director, clock, SURPRISE_S - 0.1)
    assert all(s.expression == "surprised" for s in states)
    assert blink_edges(states) == 1
    states = await run(director, clock, 0.3)
    assert states[-1].expression == "curious"


async def test_sleepy_keeps_slow_blinks_and_small_pupils():
    director, clock, _ = make()
    states = await run(director, clock, IDLE_SLEEPY_S + 8)
    sleepy = [s for s in states if s.expression == "sleepy"]
    assert len(sleepy) >= 70
    assert blink_edges(sleepy) >= 2
    assert all(0.75 <= s.pupil <= 0.85 for s in sleepy)


async def test_every_state_stays_valid_on_the_wire():
    director, clock, _ = make()
    director.on_transcript("banana")
    director.on_speaking(True)
    states = await run(director, clock, 4.0, persons=[person(cx=0.99)], doa=(30, True))
    for s in states:
        assert EyeState.from_dict(s.to_dict()) is not None
        assert -1 <= s.gx <= 1 and -1 <= s.gy <= 1
        assert PUPIL_RANGE[0] <= s.pupil <= PUPIL_RANGE[1]
