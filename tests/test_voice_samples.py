"""scripts/voice_samples.py: A/B clips of Bob's voice so a human can pick voice, pitch and speed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import voice_samples  # noqa: E402


def test_plan_covers_every_voice_at_defaults_and_a_pitch_speed_grid_for_the_default_voice():
    plan = voice_samples.build_plan(
        voices=["ash", "coral"], pitches=[4.0, 6.0], speeds=[1.0, 1.2], default="ash"
    )
    names = [item.name for item in plan]
    assert names[:2] == ["voice-ash", "voice-coral"]
    assert "ash-pitch4-speed1" in names and "ash-pitch6-speed1.2" in names
    assert len(plan) == 2 + 4
    assert all(item.text.strip() and item.speed >= 1.0 for item in plan)
    by_name = {item.name: item for item in plan}
    assert by_name["voice-coral"].voice == "coral" and by_name["ash-pitch4-speed1"].pitch == 4.0


def test_plan_names_are_unique_and_filesystem_safe():
    plan = voice_samples.build_plan()
    names = [item.name for item in plan]
    assert len(names) == len(set(names))
    assert all(name.replace("-", "").replace(".", "").isalnum() for name in names)
