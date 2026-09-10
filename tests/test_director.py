import random

import pytest

from bob.config import Settings
from bob.director import (
    CLOSE_SIZE,
    GIFT_MAX_S,
    GIFT_MIN_S,
    GREET_STABLE_S,
    IDLE_SLEEPY_S,
    MAX_OMEGA_DEG_S,
    SEEN_COOLDOWN_S,
    SURPRISE_S,
    UNKNOWN_HELLO_S,
    Console,
    Director,
    FakePhrasePlayer,
    PhrasePlayer,
    bearing_from_doa,
    omega_for_bearing,
)
from bob.hardware.eyes import FakeEyes
from bob.persona import STOCK_PHRASES
from bob.robot import Robot, SimHardware
from bob.vision.faces import Person


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeBase:
    def __init__(self):
        self.drives: list[tuple[float, float, float]] = []
        self.stops = 0

    def drive(self, vx, vy, omega):
        self.drives.append((vx, vy, omega))

    def stop(self):
        self.stops += 1


class ScriptedRng(random.Random):
    """random.Random whose random() draws come from a script first (then fall back to the seed)."""

    def __init__(self, draws=(), seed=0):
        super().__init__(seed)
        self.draws = list(draws)

    def random(self):
        if self.draws:
            return self.draws.pop(0)
        return super().random()

    def uniform(self, a, b):  # gift scheduling must not eat the scripted hello draws
        return a + (b - a) * super().random()


def person(name=None, cx=0.0, cy=0.0, size=0.2):
    return Person(name=name, score=0.9, bbox=(0, 0, 10, 10), cx=cx, cy=cy, size=size)


def make(hardware="sim", armed=False, estop=False, base=True, rng=None):
    clock = Clock()
    robot = Robot(SimHardware(delay=0))
    eyes = FakeEyes()
    phrases = FakePhrasePlayer()
    fake_base = FakeBase() if base else None
    console = Console(armed=armed, estop=estop)
    director = Director(
        robot,
        eyes,
        phrases,
        Settings(hardware=hardware),
        base=fake_base,
        console=console,
        clock=clock,
        rng=rng or random.Random(0),
    )
    return director, clock, robot, eyes, phrases, fake_base, console


async def run(director, clock, seconds, persons=(), doa=None, step=0.1):
    """Tick at 10 Hz for `seconds` of fake time with a constant scene."""
    ticks = int(round(seconds / step))
    for _ in range(ticks):
        clock.now = round(clock.now + step, 6)
        await director.tick(list(persons), doa)


# -- pure helpers ---------------------------------------------------------------


def test_bearing_from_doa_wraps_to_signed_degrees():
    assert bearing_from_doa(0) == 0
    assert bearing_from_doa(30) == 30
    assert bearing_from_doa(330) == -30
    assert bearing_from_doa(180) == -180


def test_omega_for_bearing_deadband_gain_and_clamp():
    assert omega_for_bearing(0) == 0.0
    assert omega_for_bearing(15) == 0.0
    assert omega_for_bearing(-19) == 0.0
    assert omega_for_bearing(30) == pytest.approx(15.0)
    assert omega_for_bearing(-30) == pytest.approx(-15.0)
    assert omega_for_bearing(170) == MAX_OMEGA_DEG_S
    assert omega_for_bearing(-170) == -MAX_OMEGA_DEG_S


# -- greetings -------------------------------------------------------------------


async def test_greets_recognized_person_once_after_stable_second():
    director, clock, _, eyes, phrases, _, _ = make()
    await run(director, clock, GREET_STABLE_S - 0.1, persons=[person("Ana")])
    assert phrases.calls == []
    assert director.mode == "engaged"
    await run(director, clock, 0.3, persons=[person("Ana")])
    assert len(phrases.calls) == 1
    assert "Ana" in phrases.calls[0]
    assert eyes.last.expression == "happy"
    await run(director, clock, 5, persons=[person("Ana")])
    assert len(phrases.calls) == 1


async def test_greeting_cooldown_then_greets_again():
    director, clock, _, _, phrases, _, _ = make()
    await run(director, clock, 1.5, persons=[person("Ana")])
    assert len(phrases.calls) == 1
    await run(director, clock, 2, persons=[])
    assert director.mode == "idle"
    await run(director, clock, 30, persons=[person("Ana")])
    assert len(phrases.calls) == 1
    await run(director, clock, 2, persons=[])
    clock.now += SEEN_COOLDOWN_S
    await run(director, clock, 1.5, persons=[person("Ana")])
    assert len(phrases.calls) == 2


async def test_identity_flicker_resets_stability():
    director, clock, _, _, phrases, _, _ = make()
    await run(director, clock, 0.6, persons=[person("Ana")])
    await run(director, clock, 0.2, persons=[person("Ben")])
    await run(director, clock, 0.6, persons=[person("Ana")])
    assert phrases.calls == []
    await run(director, clock, 0.6, persons=[person("Ana")])
    assert len(phrases.calls) == 1


async def test_unknown_face_hello_after_three_seconds_with_probability():
    director, clock, _, _, phrases, _, _ = make(rng=ScriptedRng(draws=[0.1]))
    await run(director, clock, UNKNOWN_HELLO_S - 0.1, persons=[person(None)])
    assert phrases.calls == []
    await run(director, clock, 0.2, persons=[person(None)])
    assert phrases.calls == ["bello"]
    await run(director, clock, 10, persons=[person(None)])
    assert phrases.calls == ["bello"]  # rolled once per visit


async def test_unknown_face_hello_once_then_cooldown():
    director, clock, _, _, phrases, _, _ = make()
    await run(director, clock, UNKNOWN_HELLO_S + 1, persons=[person(None)])
    assert phrases.calls == ["bello"]
    # face leaves and a new stranger appears within the cooldown: no second Bello
    await run(director, clock, 1.0, persons=[])
    await run(director, clock, UNKNOWN_HELLO_S + 1, persons=[person(None)])
    assert phrases.calls == ["bello"]


async def test_recognized_greeting_starts_with_bello_and_spoken_name():
    director, clock, _, _, phrases, _, _ = make()
    await run(director, clock, GREET_STABLE_S + 0.2, persons=[person("Sissi")])
    assert len(phrases.calls) == 1
    line = phrases.calls[0]
    assert line.lower().startswith("bello") and "See-see" in line and "Sissi" not in line


# -- expressions ---------------------------------------------------------------


async def test_banana_in_transcript_gives_love_while_the_song_plays():
    from bob.director import SONG_S

    director, clock, _, eyes, _, _, _ = make()
    await run(director, clock, 0.1)
    director.on_transcript("Me want banana please")
    await run(director, clock, 0.1)
    assert eyes.last.expression == "love"
    await run(director, clock, SONG_S - 0.3)
    assert eyes.last.expression == "love"
    await run(director, clock, 0.4)
    assert eyes.last.expression != "love"


async def test_banana_in_assistant_text_gives_love():
    director, clock, _, eyes, _, _, _ = make()
    director.on_assistant_text("Bananaaa! Hehehe")
    await run(director, clock, 0.1)
    assert eyes.last.expression == "love"


async def test_idle_goes_sleepy_after_sixty_seconds_and_wakes_on_speech():
    director, clock, _, eyes, phrases, _, _ = make()
    await run(director, clock, IDLE_SLEEPY_S - 0.5)
    assert eyes.last.expression == "neutral"
    await run(director, clock, 1.0)
    assert eyes.last.expression == "sleepy"
    assert "yawn" in phrases.calls
    director.on_speech_start()
    await run(director, clock, 0.1)
    assert eyes.last.expression == "curious"


async def test_doa_speech_flag_wakes_sleepy():
    director, clock, _, eyes, _, _, _ = make()
    await run(director, clock, IDLE_SLEEPY_S + 1)
    assert eyes.last.expression == "sleepy"
    await run(director, clock, 0.1, doa=(0, True))
    assert eyes.last.expression == "curious"


async def test_speaking_happy_beats_listening_curious():
    director, clock, _, eyes, _, _, _ = make()
    director.on_speech_start()
    await run(director, clock, 0.1)
    assert eyes.last.expression == "curious"
    director.on_speaking(True)
    await run(director, clock, 0.1)
    assert eyes.last.expression == "happy"
    director.on_speaking(False)
    await run(director, clock, 5)
    assert eyes.last.expression == "neutral"


async def test_gaze_follows_engaged_person_and_doa_bearing():
    director, clock, _, eyes, _, _, _ = make()
    await run(director, clock, 0.1, persons=[person("Ana", cx=0.5, cy=-0.5)])
    assert eyes.last.gx == pytest.approx(0.5)
    assert eyes.last.gy == pytest.approx(-0.3)
    await run(director, clock, 0.1, doa=(30, True))
    assert eyes.last.gx == pytest.approx(0.5)
    await run(director, clock, 0.1, doa=(330, True))
    assert eyes.last.gx == pytest.approx(-0.5)


# -- base motion -----------------------------------------------------------------


async def test_turn_to_voice_drives_with_bearing_sign_then_stops_inside_deadband():
    director, clock, _, _, _, base, _ = make(hardware="real", armed=True)
    await run(director, clock, 0.1, doa=(30, True))
    assert base.drives[-1][2] > 0
    assert director.mode == "wandering"
    await run(director, clock, 0.1, doa=(330, True))
    assert base.drives[-1][2] < 0
    count = len(base.drives)
    await run(director, clock, 0.1, doa=(5, True))
    assert base.stops == 1
    assert len(base.drives) == count
    assert director.mode == "idle"


async def test_turn_omega_is_clamped():
    director, clock, _, _, _, base, _ = make(hardware="real", armed=True)
    await run(director, clock, 0.1, doa=(170, True))
    assert base.drives[-1] == (0.0, 0.0, MAX_OMEGA_DEG_S)


async def test_no_drive_without_speech_or_when_sim_or_disarmed():
    director, clock, _, _, _, base, _ = make(hardware="real", armed=True)
    await run(director, clock, 0.5, doa=(90, False))
    assert base.drives == []
    director, clock, _, _, _, base, _ = make(hardware="sim", armed=True)
    await run(director, clock, 0.5, doa=(90, True))
    assert base.drives == []
    director, clock, _, _, _, base, _ = make(hardware="real", armed=False)
    await run(director, clock, 0.5, doa=(90, True))
    assert base.drives == []


async def test_estop_never_drives_and_stops_once():
    director, clock, _, _, _, base, console = make(hardware="real", armed=True, estop=True)
    await run(director, clock, 1.0, doa=(90, True), persons=[person("Ana", cx=0.8, size=0.1)])
    assert base.drives == []
    assert base.stops == 1
    console.estop = False
    await run(director, clock, 0.1, doa=(90, True))
    assert len(base.drives) == 1
    console.estop = True
    await run(director, clock, 0.5, doa=(90, True))
    assert len(base.drives) == 1
    assert base.stops == 2


async def test_disarming_while_moving_stops_base():
    director, clock, _, _, _, base, console = make(hardware="real", armed=True)
    await run(director, clock, 0.1, doa=(90, True))
    assert len(base.drives) == 1
    console.armed = False
    await run(director, clock, 0.1, doa=(90, True))
    assert base.stops == 1
    assert len(base.drives) == 1


async def test_approach_far_person_then_stop_when_close():
    director, clock, _, _, _, base, _ = make(hardware="real", armed=True)
    await run(director, clock, 0.1, persons=[person("Ana", cx=0.5, size=0.2)])
    vx, vy, omega = base.drives[-1]
    assert vx == pytest.approx(0.1)
    assert vy == 0.0
    assert omega > 0
    await run(director, clock, 0.1, persons=[person("Ana", cx=-0.5, size=0.2)])
    assert base.drives[-1][2] < 0
    await run(director, clock, 0.1, persons=[person("Ana", cx=0.0, size=CLOSE_SIZE)])
    assert base.stops == 1
    count = len(base.drives)
    await run(director, clock, 0.5, persons=[person("Ana", cx=0.0, size=CLOSE_SIZE + 0.1)])
    assert len(base.drives) == count
    assert base.stops == 1


async def test_approach_stops_when_person_lost():
    director, clock, _, _, _, base, _ = make(hardware="real", armed=True)
    await run(director, clock, 0.1, persons=[person("Ana", cx=0.0, size=0.2)])
    assert len(base.drives) == 1
    await run(director, clock, 0.1, persons=[])
    assert base.stops == 1


async def test_no_base_means_no_motion_errors():
    director, clock, _, _, _, _, _ = make(hardware="real", armed=True, base=False)
    await run(director, clock, 0.5, doa=(90, True), persons=[person("Ana", size=0.1)])
    assert director.mode == "engaged"


# -- random gift -------------------------------------------------------------------


async def test_gift_fires_within_window_for_recognized_person():
    director, clock, robot, _, phrases, _, _ = make()
    start = clock.now
    fired_at = None
    for _ in range(int(GIFT_MAX_S * 10)):
        clock.now += 0.1
        await director.tick([person("Ana")], None)
        if "para_tu" in phrases.calls:
            fired_at = clock.now - start
            break
    assert fired_at is not None
    assert GIFT_MIN_S / 3 <= fired_at <= GIFT_MAX_S / 3
    assert robot.state.phase != "idle"
    assert director.mode == "offering"


async def test_gift_window_for_unknown_person_is_full_length():
    director, clock, _, _, phrases, _, _ = make(rng=ScriptedRng(draws=[0.9]))
    start = clock.now
    fired_at = None
    for _ in range(int(GIFT_MAX_S * 10) + 10):
        clock.now += 0.1
        await director.tick([person(None)], None)
        if "para_tu" in phrases.calls:
            fired_at = clock.now - start
            break
    assert fired_at is not None
    assert GIFT_MIN_S <= fired_at <= GIFT_MAX_S


async def test_gift_needs_banana_loaded_and_engagement():
    director, clock, robot, _, phrases, _, _ = make()
    robot.state.banana = "delivered"
    await run(director, clock, GIFT_MAX_S + 5, persons=[person("Ana")])
    assert "para_tu" not in phrases.calls
    assert robot.state.phase == "idle"

    director, clock, robot, _, phrases, _, _ = make()
    await run(director, clock, GIFT_MAX_S + 5, persons=[])
    assert "para_tu" not in phrases.calls
    assert robot.state.phase == "idle"


async def test_gift_skipped_when_robot_stopped():
    director, clock, robot, _, phrases, _, _ = make()
    await robot.command("stop_motion")
    await run(director, clock, GIFT_MAX_S + 5, persons=[person("Ana")])
    assert "para_tu" not in phrases.calls


# -- tool hooks and event mapping ------------------------------------------------


async def test_on_tool_set_expression_is_accepted_and_shown():
    director, clock, _, eyes, _, _, _ = make()
    result = await director.on_tool("set_expression", {"expression": "surprised"})
    assert result["accepted"] is True
    assert result["expression"] == "surprised"
    await run(director, clock, 0.1)
    assert eyes.last.expression == "surprised"


async def test_on_tool_look_at_speaker_turns_gaze_to_last_voice():
    director, clock, _, eyes, _, _, _ = make()
    await run(director, clock, 0.1, doa=(45, True))
    await run(director, clock, 5)  # voice memory fades, gaze drifts
    result = await director.on_tool("look_at_speaker", {})
    assert result["accepted"] is True
    assert result["bearing"] == 45
    await run(director, clock, 0.1)
    assert eyes.last.gx == pytest.approx(0.75)


async def test_on_tool_rejects_unknown_and_bad_expression():
    director, *_ = make()
    assert (await director.on_tool("dance", {}))["accepted"] is False
    assert (await director.on_tool("set_expression", {"expression": "grumpy"}))["accepted"] is False


async def test_handle_event_maps_robot_client_events():
    director, clock, _, eyes, _, _, _ = make()
    director.handle_event({"type": "transcript", "text": "banana!"})
    await run(director, clock, 0.1)
    assert eyes.last.expression == "love"
    from bob.director import SONG_S

    await run(director, clock, SONG_S + 0.5)
    director.handle_event({"type": "playback_started", "segment_id": 1})
    await run(director, clock, 0.1)
    assert director.speaking is True
    assert eyes.last.expression == "happy"
    director.handle_event({"type": "playback_finished", "segment_id": 1})
    await run(director, clock, 0.1)
    assert director.speaking is False
    director.handle_event({"type": "barge_in", "request_id": 2})
    await run(director, clock, 0.1)
    assert eyes.last.expression == "surprised"  # talked over: a flash of surprise, then listening
    await run(director, clock, SURPRISE_S)
    assert eyes.last.expression == "curious"
    director.handle_event({"type": "assistant_delta", "text": "one banana"})
    await run(director, clock, 0.1)
    assert eyes.last.expression == "love"
    director.handle_event({"type": "unknown_kind"})  # ignored


# -- phrase player -------------------------------------------------------------


async def test_phrase_player_maps_keys_to_cached_phrases_and_text_to_tts(tmp_path):
    (tmp_path / "bello.wav").write_bytes(b"RIFF")
    spoken, played = [], []

    async def say(text):
        spoken.append(text)

    player = PhrasePlayer(say, play=played.append, phrases_dir=tmp_path)
    await player.say("bello")
    assert played == [tmp_path / "bello.wav"]
    await player.say("banana")  # key without a cached file: speak the phrase text
    assert spoken == [STOCK_PHRASES["banana"]]
    await player.say("Hello Ana")
    assert spoken[-1] == "Hello Ana"


async def test_phrase_player_without_cache_speaks_phrase_text():
    spoken = []
    player = PhrasePlayer(spoken.append)
    await player.say("para_tu")
    assert spoken == [STOCK_PHRASES["para_tu"]]


@pytest.mark.parametrize("seed", range(12))
async def test_recognized_greeting_always_names_the_person(seed):
    director, clock, _, _, phrases, _, _ = make(rng=random.Random(seed))
    await run(director, clock, 1.5, persons=[person("Ana")])
    assert len(phrases.calls) == 1
    assert "Ana" in phrases.calls[0]


async def test_sing_song_tool_plays_song_and_hearts(director_factory=None):
    import random

    from bob.config import Settings
    from bob.director import Director, FakePhrasePlayer
    from bob.hardware.eyes import FakeEyes
    from bob.robot import Robot, SimHardware

    phrases = FakePhrasePlayer()
    director = Director(Robot(SimHardware(delay=0)), FakeEyes(), phrases, Settings(), rng=random.Random(0))
    result = await director.on_tool("sing_song", {})
    assert result["accepted"] is True
    assert "song" in phrases.calls
    await director.tick([], None)
    assert director.eye_state.expression == "love"


async def test_banana_mention_plays_song_clip_with_cooldown():
    from bob.director import SONG_COOLDOWN_S

    director, clock, _, eyes, phrases, _, _ = make()
    await run(director, clock, 0.1)
    director.on_transcript("I love banana")
    await run(director, clock, 0.1)
    assert phrases.calls.count("song") == 1
    assert eyes.last.expression == "love"
    director.on_transcript("banana again")
    await run(director, clock, 0.1)
    assert phrases.calls.count("song") == 1  # within cooldown
    clock.now += SONG_COOLDOWN_S
    director.on_transcript("banana!")
    await run(director, clock, 0.1)
    assert phrases.calls.count("song") == 2
