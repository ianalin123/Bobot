import json
import sys
from pathlib import Path

import numpy as np
import pytest

from bob import persona
from bob.providers import decode_wav
from bob.providers_cloud import wav_from_pcm16

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render_phrases  # noqa: E402

NAMELESS = [g for g in persona.GREETINGS if "{name}" not in g]
NAMED = [g for g in persona.GREETINGS if "{name}" in g]
FULL_COUNT = len(persona.STOCK_PHRASES) + len(persona.GREETINGS)
FULL_CHARS = sum(len(t) for t in persona.STOCK_PHRASES.values()) + sum(
    len(g.format(name=render_phrases.GENERIC_NAME)) for g in persona.GREETINGS
)


def make_wav(seconds: float = 0.25) -> bytes:
    t = np.arange(int(16000 * seconds)) / 16000
    return wav_from_pcm16((np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("<i2"), 16000)


class FakeSynth:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[str] = []
        self.fail_on = fail_on

    async def __call__(self, text: str) -> bytes:
        self.calls.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("boom")
        return make_wav()


def test_build_plan_full_budget_includes_everything():
    plan = render_phrases.build_plan(budget=6000)
    assert len(plan) == FULL_COUNT
    assert sum(item.chars for item in plan) == FULL_CHARS
    assert FULL_CHARS <= 6000
    keys = [item.key for item in plan]
    assert keys[: len(persona.STOCK_PHRASES)] == [f"phrase_{k}" for k in persona.STOCK_PHRASES]
    assert len(keys) == len(set(keys))
    for item in plan:
        assert "{name}" not in item.text and "{" not in item.text
        assert item.chars == len(item.text)
    generic = [item for item in plan if item.key.startswith("greet_") and item.generic]
    assert len(generic) == len(NAMED)
    assert all(render_phrases.GENERIC_NAME in item.text for item in generic)
    nameless = [item for item in plan if item.key.startswith("greet_") and not item.generic]
    assert [item.text for item in nameless] == NAMELESS


def test_build_plan_small_budget_keeps_phrases_and_nameless_only():
    base = sum(len(t) for t in persona.STOCK_PHRASES.values()) + sum(len(g) for g in NAMELESS)
    plan = render_phrases.build_plan(budget=base)
    assert len(plan) == len(persona.STOCK_PHRASES) + len(NAMELESS)
    assert not any(item.generic for item in plan)
    # One more generic greeting fits once the budget grows by its length (list order).
    first_named = next(g for g in persona.GREETINGS if "{name}" in g)
    plan = render_phrases.build_plan(budget=base + len(first_named.format(name=render_phrases.GENERIC_NAME)))
    assert sum(item.generic for item in plan) == 1


def test_greeting_keys_are_zero_padded_list_indices():
    plan = render_phrases.build_plan(budget=6000)
    by_key = {item.key: item for item in plan}
    assert "greet_000" in by_key
    assert by_key["greet_000"].text == persona.GREETINGS[0].format(name=render_phrases.GENERIC_NAME)
    assert by_key["greet_099"].text == persona.GREETINGS[99]


def test_dry_run_prints_plan_without_synthesizer(tmp_path, capsys):
    code = render_phrases.main(["--dry-run", "--out", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert f"{FULL_COUNT} clips" in out
    assert f"{FULL_CHARS} chars" in out
    assert "budget 6000" in out
    assert not list(tmp_path.glob("*.wav"))


def test_dry_run_with_tiny_budget_counts_fewer(tmp_path, capsys):
    code = render_phrases.main(["--dry-run", "--budget", "600", "--out", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert f"{len(persona.STOCK_PHRASES) + len(NAMELESS)} clips" in out


def test_render_writes_files_and_manifest(tmp_path):
    synth = FakeSynth()
    code = render_phrases.main(["--out", str(tmp_path), "--provider", "openai"], synthesizer=synth)
    assert code == 0
    assert len(synth.calls) == FULL_COUNT
    wavs = sorted(tmp_path.glob("*.wav"))
    assert len(wavs) == FULL_COUNT
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest) == FULL_COUNT
    for key, entry in manifest.items():
        assert set(entry) >= {"file", "text", "chars", "provider", "model"}
        assert entry["file"] == f"{key}.wav"
        assert entry["chars"] == len(entry["text"])
        assert entry["provider"] == "openai"
        samples = decode_wav((tmp_path / entry["file"]).read_bytes())
        assert len(samples) > 1000
    assert manifest["phrase_bello"]["text"] == "Bello!"


def test_render_skips_existing_files_unless_force(tmp_path):
    first = FakeSynth()
    assert render_phrases.main(["--out", str(tmp_path)], synthesizer=first) == 0
    second = FakeSynth()
    assert render_phrases.main(["--out", str(tmp_path)], synthesizer=second) == 0
    assert second.calls == []
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest) == FULL_COUNT
    third = FakeSynth()
    assert render_phrases.main(["--out", str(tmp_path), "--force"], synthesizer=third) == 0
    assert len(third.calls) == FULL_COUNT


def test_render_merges_existing_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"custom_x": {"file": "custom_x.wav", "text": "x"}}))
    assert render_phrases.main(["--out", str(tmp_path)], synthesizer=FakeSynth()) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["custom_x"]["text"] == "x"
    assert len(manifest) == FULL_COUNT + 1


def test_only_filters_keys(tmp_path):
    synth = FakeSynth()
    code = render_phrases.main(
        ["--out", str(tmp_path), "--only", "phrase_bello", "--only", "greet_001"], synthesizer=synth
    )
    assert code == 0
    assert sorted(p.name for p in tmp_path.glob("*.wav")) == ["greet_001.wav", "phrase_bello.wav"]
    assert render_phrases.main(["--out", str(tmp_path), "--only", "nope"], synthesizer=synth) == 2


def test_budget_cutoff_excludes_greetings_when_small(tmp_path):
    synth = FakeSynth()
    code = render_phrases.main(["--out", str(tmp_path), "--budget", "600"], synthesizer=synth)
    assert code == 0
    names = {p.stem for p in tmp_path.glob("*.wav")}
    assert {f"phrase_{k}" for k in persona.STOCK_PHRASES} <= names
    assert len(names) == len(persona.STOCK_PHRASES) + len(NAMELESS)
    generic_texts = {g.format(name=render_phrases.GENERIC_NAME) for g in NAMED}
    assert not generic_texts & set(synth.calls)
    assert set(NAMELESS) <= set(synth.calls)


def test_pitch_shift_changes_audio_but_keeps_wav_valid(tmp_path):
    plain = tmp_path / "plain"
    shifted = tmp_path / "shifted"
    args = ["--only", "phrase_bello"]
    assert render_phrases.main(["--out", str(plain), "--pitch", "0", *args], synthesizer=FakeSynth()) == 0
    assert render_phrases.main(["--out", str(shifted), "--pitch", "4", *args], synthesizer=FakeSynth()) == 0
    a = decode_wav((plain / "phrase_bello.wav").read_bytes())
    b = decode_wav((shifted / "phrase_bello.wav").read_bytes())
    assert len(b) > 100
    assert not (len(a) == len(b) and np.allclose(a, b))


def test_default_pitch_depends_on_provider():
    assert render_phrases.default_pitch("elevenlabs") == 4.0
    assert render_phrases.default_pitch("openai") == 0.0


def test_synth_failure_returns_error_but_keeps_manifest(tmp_path):
    synth = FakeSynth(fail_on="Bananaaa! Hehehe!")
    code = render_phrases.main(
        ["--out", str(tmp_path), "--only", "phrase_bello", "--only", "phrase_banana"], synthesizer=synth
    )
    assert code == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "phrase_bello" in manifest
    assert "phrase_banana" not in manifest
    assert not (tmp_path / "phrase_banana.wav").exists()


def test_pick_voice_prefers_hint_then_first():
    voices = [{"name": "Rachel", "voice_id": "r1"}, {"name": "Josh", "voice_id": "j1"}]
    assert render_phrases.pick_voice(voices, "josh") == "j1"
    assert render_phrases.pick_voice(voices, "nobody") == "r1"
    with pytest.raises(RuntimeError):
        render_phrases.pick_voice([], "josh")
