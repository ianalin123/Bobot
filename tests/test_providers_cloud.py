"""Cloud provider adapters tested against duck-typed fake SDK clients (no network)."""

import asyncio
import io
import json
import types
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from bob import voice
from bob.providers import decode_wav
from bob.providers_cloud import (
    ElevenLabsTTS,
    FallbackSTT,
    FallbackTTS,
    OpenAILLM,
    OpenAISTT,
    OpenAITTS,
    build_providers,
    openai_messages,
    openai_tools,
    pitch_shift_wav,
    wav_from_pcm16,
)
from bob.robot import Robot, SimHardware


def sine_wav(seconds=1.0, rate=16000, amplitude=0.3, hz=440.0):
    t = np.arange(int(seconds * rate)) / rate
    samples = (np.sin(2 * np.pi * hz * t) * amplitude * 32767).astype("<i2")
    return wav_from_pcm16(samples, rate)


# --- fake OpenAI client -----------------------------------------------------


def chunk(content=None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)])


def tool_delta(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)


class FakeSpeechResponse:
    def __init__(self, pcm: bytes):
        self.pcm = pcm

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_bytes(self, chunk_size=4096):
        for start in range(0, len(self.pcm), chunk_size):
            yield self.pcm[start : start + chunk_size]


class FakeOpenAI:
    def __init__(self, chunks=(), transcript="bello", pcm=b""):
        self.calls = []
        client = self

        class Transcriptions:
            def create(_, **kwargs):
                client.calls.append(("transcribe", kwargs))
                return SimpleNamespace(text=transcript)

        class Completions:
            async def create(_, **kwargs):
                client.calls.append(("chat", kwargs))
                return FakeStream(chunks)

        class Streaming:
            def create(_, **kwargs):
                client.calls.append(("speech", kwargs))
                return FakeSpeechResponse(pcm)

        self.audio = SimpleNamespace(
            transcriptions=Transcriptions(),
            speech=SimpleNamespace(with_streaming_response=Streaming()),
        )
        self.chat = SimpleNamespace(completions=Completions())


# --- STT --------------------------------------------------------------------


def test_stt_skips_short_or_silent_audio():
    client = FakeOpenAI(transcript="should not be called")
    stt = OpenAISTT(client, "gpt-4o-mini-transcribe")
    assert stt.transcribe(sine_wav(seconds=0.2)) == ""
    assert stt.transcribe(sine_wav(seconds=1.0, amplitude=0.0005)) == ""
    assert client.calls == []


def test_stt_sends_wav_and_returns_text():
    client = FakeOpenAI(transcript="  Bello Bob!  ")
    stt = OpenAISTT(client, "gpt-4o-mini-transcribe")
    assert stt.transcribe(sine_wav()) == "Bello Bob!"
    kind, kwargs = client.calls[0]
    assert kind == "transcribe" and kwargs["model"] == "gpt-4o-mini-transcribe"
    name, handle, mime = kwargs["file"]
    assert name == "turn.wav" and mime == "audio/wav"
    assert len(decode_wav(handle.read())) == 16000


# --- LLM --------------------------------------------------------------------


async def collect(agen):
    return [item async for item in agen]


async def test_llm_stream_yields_deltas_then_parsed_tool_calls():
    chunks = [
        chunk(content="Bello! "),
        chunk(content="One moment."),
        chunk(tool_calls=[tool_delta(0, id="call_1", name="set_expression", arguments="")]),
        chunk(tool_calls=[tool_delta(0, arguments='{"expr')]),
        chunk(tool_calls=[tool_delta(0, arguments='ession": "happy"}')]),
        chunk(tool_calls=[tool_delta(1, id="call_2", name="offer_banana", arguments="")]),
        chunk(content=None),
    ]
    client = FakeOpenAI(chunks=chunks)
    llm = OpenAILLM(client, "gpt-4.1-mini")
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    out = await collect(llm.stream(messages, voice.TOOLS))
    assert out[:2] == [{"content": "Bello! "}, {"content": "One moment."}]
    assert len(out) == 3 and "tool_calls" in out[2]
    calls = out[2]["tool_calls"]
    assert calls[0]["id"] == "call_1"
    assert calls[0]["function"] == {"name": "set_expression", "arguments": {"expression": "happy"}}
    assert calls[1]["id"] == "call_2"
    assert calls[1]["function"] == {"name": "offer_banana", "arguments": {}}
    kind, kwargs = client.calls[0]
    assert kind == "chat" and kwargs["stream"] is True and kwargs["model"] == "gpt-4.1-mini"
    assert kwargs["messages"] == messages
    assert kwargs["tools"][0]["type"] == "function"
    assert {t["function"]["name"] for t in kwargs["tools"]} == {
        "offer_banana",
        "stop_motion",
        "look_at_speaker",
        "set_expression",
        "sing_song",
    }


async def test_llm_stream_without_tool_calls_yields_only_content():
    client = FakeOpenAI(chunks=[chunk(content="Hi"), chunk(content=None)])
    out = await collect(OpenAILLM(client, "m").stream([{"role": "user", "content": "x"}], voice.TOOLS))
    assert out == [{"content": "Hi"}]


async def test_llm_generates_ids_for_calls_without_one():
    client = FakeOpenAI(chunks=[chunk(tool_calls=[tool_delta(0, name="stop_motion", arguments="{}")])])
    out = await collect(OpenAILLM(client, "m").stream([{"role": "user", "content": "x"}], voice.TOOLS))
    call = out[0]["tool_calls"][0]
    assert call["id"] and call["function"] == {"name": "stop_motion", "arguments": {}}


def test_openai_message_conversion_round_trips_tool_results():
    calls = [
        {"id": "call_a", "function": {"name": "offer_banana", "arguments": {}}},
        {"id": "call_b", "function": {"name": "set_expression", "arguments": {"expression": "happy"}}},
    ]
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "banana please"},
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_name": "offer_banana", "content": '{"accepted": true}'},
        {"role": "tool", "tool_name": "set_expression", "content": '{"accepted": true}'},
    ]
    converted = openai_messages(history)
    assert converted[0] == {"role": "system", "content": "sys"}
    assert converted[2]["role"] == "assistant" and converted[2]["content"] is None
    assert converted[2]["tool_calls"][0] == {
        "id": "call_a",
        "type": "function",
        "function": {"name": "offer_banana", "arguments": "{}"},
    }
    assert json.loads(converted[2]["tool_calls"][1]["function"]["arguments"]) == {"expression": "happy"}
    assert converted[3] == {"role": "tool", "tool_call_id": "call_a", "content": '{"accepted": true}'}
    assert converted[4] == {"role": "tool", "tool_call_id": "call_b", "content": '{"accepted": true}'}


def test_openai_message_conversion_answers_unanswered_tool_calls():
    # voice.py only executes the first two calls; OpenAI requires a reply for every call id.
    calls = [{"id": f"call_{i}", "function": {"name": "stop_motion", "arguments": {}}} for i in range(3)]
    history = [
        {"role": "user", "content": "stop"},
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_name": "stop_motion", "content": "{}"},
        {"role": "tool", "tool_name": "stop_motion", "content": "{}"},
        {"role": "user", "content": "thanks"},
    ]
    converted = openai_messages(history)
    tool_ids = [m["tool_call_id"] for m in converted if m["role"] == "tool"]
    assert tool_ids == ["call_0", "call_1", "call_2"]
    assert converted[-1] == {"role": "user", "content": "thanks"}


def test_openai_tools_pass_through_and_wrap_bare_functions():
    assert openai_tools(voice.TOOLS) == voice.TOOLS
    bare = [{"name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    assert openai_tools(bare) == [{"type": "function", "function": bare[0]}]


# --- TTS --------------------------------------------------------------------


def pcm_sine(seconds=0.5, rate=24000, hz=440.0):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * hz * t) * 0.5 * 32767).astype("<i2").tobytes()


async def test_openai_tts_returns_16k_wav_and_passes_instructions():
    client = FakeOpenAI(pcm=pcm_sine(seconds=0.5))
    tts = OpenAITTS(client, "gpt-4o-mini-tts", "ash", "Talk like a minion.", pitch_semitones=0.0, speed=1.0)
    wav_bytes = await tts.synthesize("Bello!")
    samples = decode_wav(wav_bytes)
    assert abs(len(samples) - 8000) <= 8
    assert np.abs(samples).max() > 0.3
    kind, kwargs = client.calls[0]
    assert kind == "speech"
    assert kwargs["response_format"] == "pcm" and kwargs["instructions"] == "Talk like a minion."
    assert kwargs["voice"] == "ash" and kwargs["model"] == "gpt-4o-mini-tts" and kwargs["input"] == "Bello!"


async def test_openai_tts_pitch_shift_raises_dominant_frequency():
    client = FakeOpenAI(pcm=pcm_sine(seconds=1.0, hz=300.0))
    tts = OpenAITTS(client, "m", "ash", "", pitch_semitones=12.0)
    samples = decode_wav(await tts.synthesize("Bello!"))
    spectrum = np.abs(np.fft.rfft(samples))
    peak_hz = np.fft.rfftfreq(len(samples), 1 / 16000)[np.argmax(spectrum)]
    assert 540 < peak_hz < 660


def test_pitch_shift_zero_is_identity_and_wav_helper_round_trips():
    pcm = np.frombuffer(pcm_sine(seconds=0.25, rate=16000), dtype="<i2")
    shifted = pitch_shift_wav(pcm, 16000, 0.0)
    assert shifted.dtype == np.int16 and np.array_equal(shifted, pcm)
    assert np.array_equal(decode_wav(wav_from_pcm16(pcm, 16000)), pcm.astype(np.float32) / 32768.0)
    floats = pcm.astype(np.float32) / 32768.0
    assert np.array_equal(decode_wav(wav_from_pcm16(floats, 16000)), decode_wav(wav_from_pcm16(pcm, 16000)))


def test_pitch_shift_numpy_fallback_changes_pitch(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_librosa(name, *args, **kwargs):
        if name.startswith("librosa"):
            raise ImportError("librosa disabled for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_librosa)
    pcm = np.frombuffer(pcm_sine(seconds=1.0, rate=16000, hz=300.0), dtype="<i2")
    shifted = pitch_shift_wav(pcm, 16000, 12.0)
    assert shifted.dtype == np.int16
    assert abs(len(shifted) - len(pcm) / 2) <= 2  # duration halves in the chipmunk fallback
    spectrum = np.abs(np.fft.rfft(shifted.astype(np.float32)))
    peak_hz = np.fft.rfftfreq(len(shifted), 1 / 16000)[np.argmax(spectrum)]
    assert 570 < peak_hz < 630


async def test_elevenlabs_tts_wraps_pcm16000_into_wav():
    calls = []

    class Speech:
        def convert(self, **kwargs):
            calls.append(kwargs)
            data = pcm_sine(seconds=0.5, rate=16000)
            return iter([data[:1000], data[1000:]])

    client = SimpleNamespace(text_to_speech=Speech())
    tts = ElevenLabsTTS(client, "voice123", pitch_semitones=0.0, speed=1.0)
    samples = decode_wav(await tts.synthesize("Bello!"))
    assert len(samples) == 8000
    assert calls[0]["output_format"] == "pcm_16000" and calls[0]["voice_id"] == "voice123"
    assert calls[0]["model_id"] == "eleven_flash_v2_5" and calls[0]["text"] == "Bello!"


# --- Fallback ---------------------------------------------------------------


class BoomSTT:
    def transcribe(self, data):
        raise RuntimeError("cloud down")


class OkSTT:
    def transcribe(self, data):
        return "local text"


class BoomTTS:
    async def synthesize(self, text):
        raise RuntimeError("cloud down")


class OkTTS:
    async def synthesize(self, text):
        return b"local-wav"


async def test_fallbacks_use_secondary_on_exception_and_record_error():
    stt = FallbackSTT(BoomSTT(), OkSTT())
    assert stt.last_error is None
    assert stt.transcribe(b"x") == "local text"
    assert isinstance(stt.last_error, RuntimeError)

    tts = FallbackTTS(BoomTTS(), OkTTS())
    assert await tts.synthesize("hi") == b"local-wav"
    assert isinstance(tts.last_error, RuntimeError)

    good = FallbackSTT(OkSTT(), BoomSTT())
    assert good.transcribe(b"x") == "local text" and good.last_error is None


async def test_fallback_tts_does_not_swallow_cancellation():
    class Hanging:
        async def synthesize(self, text):
            await asyncio.Event().wait()

    tts = FallbackTTS(Hanging(), OkTTS())
    task = asyncio.create_task(tts.synthesize("x"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tts.last_error is None


# --- build_providers --------------------------------------------------------


def test_build_providers_local_and_cloud(monkeypatch):
    from bob.config import Settings
    from bob.providers import LocalLLM, LocalSTT, LocalTTS

    stt, llm, tts = build_providers(Settings(mode="local"))
    assert isinstance(stt, LocalSTT) and isinstance(llm, LocalLLM) and isinstance(tts, LocalTTS)

    import sys

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda api_key: SimpleNamespace(kind="sync", key=api_key)
    fake_openai.AsyncOpenAI = lambda api_key: SimpleNamespace(kind="async", key=api_key)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    settings = Settings(mode="cloud", openai_api_key="sk-test", pitch_semitones=3.0)
    stt, llm, tts = build_providers(settings)
    assert isinstance(stt, OpenAISTT) and stt.client.kind == "sync" and stt.model == settings.stt_model
    assert isinstance(llm, OpenAILLM) and llm.client.kind == "async" and llm.model == settings.llm_model
    assert isinstance(tts, OpenAITTS) and tts.client.kind == "async" and tts.pitch_semitones == 3.0
    assert tts.voice == "ash" and tts.instructions

    stt, llm, tts = build_providers(Settings(mode="cloud", openai_api_key="sk-test", local_fallback=True))
    assert isinstance(stt, FallbackSTT) and isinstance(stt.primary, OpenAISTT)
    assert isinstance(stt.secondary, LocalSTT)
    assert isinstance(tts, FallbackTTS) and isinstance(tts.primary, OpenAITTS)
    assert isinstance(tts.secondary, LocalTTS)
    assert isinstance(llm, OpenAILLM)

    with pytest.raises(ValueError):
        build_providers(Settings(mode="sim"))


# --- VoiceSession tool routing ----------------------------------------------


def make_session(llm, on_tool=None):
    events = []

    async def send(event):
        events.append(event)

    class TTS:
        async def synthesize(self, text):
            return b"fake-wav"

    session = VoiceSessionFactory(send, llm, TTS(), on_tool)
    return session, events


def VoiceSessionFactory(send, llm, tts, on_tool):
    robot = Robot(SimHardware(0.001))
    if on_tool is None:
        return voice.VoiceSession(send, None, llm, tts, robot)
    return voice.VoiceSession(send, None, llm, tts, robot, on_tool=on_tool)


class DirectorToolLLM:
    async def stream(self, messages, tools):
        if messages[-1]["role"] == "user":
            yield {
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "set_expression", "arguments": {"expression": "happy"}},
                    },
                    {"id": "c2", "function": {"name": "look_at_speaker", "arguments": {}}},
                ]
            }
        else:
            yield {"content": "Bello!"}


def test_tools_include_director_tools_with_schema():
    by_name = {t["function"]["name"] for t in voice.TOOLS}
    assert by_name == {"offer_banana", "stop_motion", "look_at_speaker", "set_expression", "sing_song"}
    spec = next(t["function"] for t in voice.TOOLS if t["function"]["name"] == "set_expression")
    assert spec["parameters"]["required"] == ["expression"]
    assert spec["parameters"]["properties"]["expression"]["enum"] == [
        "neutral",
        "curious",
        "happy",
        "love",
        "sleepy",
        "surprised",
        "sad",
        "angry_playful",
    ]
    look = next(t["function"] for t in voice.TOOLS if t["function"]["name"] == "look_at_speaker")
    assert look["parameters"]["properties"] == {}


async def test_director_tools_route_to_on_tool_and_reject_without_director():
    seen = []

    async def on_tool(name, args):
        seen.append((name, args))
        return {"accepted": True, "name": name}

    session, events = make_session(DirectorToolLLM(), on_tool)
    await session.start(1, text="Look at me and be happy")
    await session.task
    results = [e for e in events if e["type"] == "tool_result"]
    assert [(r["name"], r["result"]["accepted"]) for r in results] == [
        ("set_expression", True),
        ("look_at_speaker", True),
    ]
    assert seen == [("set_expression", {"expression": "happy"}), ("look_at_speaker", {})]
    assert session.robot.state.phase == "idle"

    session, events = make_session(DirectorToolLLM())
    await session.start(1, text="Look at me and be happy")
    await session.task
    results = [e for e in events if e["type"] == "tool_result"]
    assert all(not r["result"]["accepted"] for r in results)
    assert results[0]["result"]["reason"] == "No director attached."


async def test_set_expression_rejects_bad_arguments_and_look_rejects_arguments():
    seen = []

    async def on_tool(name, args):
        seen.append(name)
        return {"accepted": True}

    class BadArgsLLM:
        async def stream(self, messages, tools):
            if messages[-1]["role"] == "user":
                yield {
                    "tool_calls": [
                        {"function": {"name": "set_expression", "arguments": {"expression": "furious"}}},
                        {"function": {"name": "look_at_speaker", "arguments": {"target": "left"}}},
                    ]
                }
            else:
                yield {"content": "Hm."}

    session, events = make_session(BadArgsLLM(), on_tool)
    await session.start(1, text="hi")
    await session.task
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    assert results and all(not r["accepted"] for r in results)
    assert seen == []


async def test_old_tools_still_ignore_on_tool_and_reject_arguments():
    called = []

    async def on_tool(name, args):
        called.append(name)
        return {"accepted": True}

    class OldToolLLM:
        async def stream(self, messages, tools):
            if messages[-1]["role"] == "user":
                yield {
                    "tool_calls": [
                        {"function": {"name": "offer_banana", "arguments": {}}},
                        {"function": {"name": "stop_motion", "arguments": {"hard": True}}},
                    ]
                }
            else:
                yield {"content": "Okay."}

    session, events = make_session(OldToolLLM(), on_tool)
    await session.start(1, text="hi")
    await session.task
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["name"] == "offer_banana" and results[0]["result"]["accepted"]
    assert results[1]["result"] == {"accepted": False, "reason": "This tool accepts no arguments."}
    assert called == []
    await session.robot.task
    assert session.robot.state.phase == "waiting"


def wav_header_only():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"")
    return buffer.getvalue()


def test_wav_from_pcm16_empty_is_valid_header():
    assert wav_from_pcm16(np.zeros(0, dtype=np.int16), 16000) == wav_header_only()


async def test_openai_tts_speed_shortens_output():
    pytest.importorskip("librosa")
    client = FakeOpenAI(pcm=pcm_sine(seconds=1.0, hz=300.0))
    tts = OpenAITTS(client, "m", "ash", "", pitch_semitones=0.0, speed=2.0)
    samples = decode_wav(await tts.synthesize("Bello!"))
    assert abs(len(samples) - 8000) <= 400


def test_tts_instructions_describe_a_minion_reading_minionese():
    from bob.providers_cloud import TTS_INSTRUCTIONS

    assert "Minion" in TTS_INSTRUCTIONS
    assert "Minionese" in TTS_INSTRUCTIONS and "as written" in TTS_INSTRUCTIONS


def test_build_providers_passes_speed_to_tts(monkeypatch):
    import sys

    from bob.config import Settings

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda api_key: SimpleNamespace(kind="sync", key=api_key)
    fake_openai.AsyncOpenAI = lambda api_key: SimpleNamespace(kind="async", key=api_key)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    _, _, tts = build_providers(Settings(mode="cloud", openai_api_key="sk-test", speed=1.3))
    assert tts.speed == 1.3


async def test_elevenlabs_tts_applies_voice_fx():
    pytest.importorskip("librosa")

    class Speech:
        def convert(self, **kwargs):
            return iter([pcm_sine(seconds=1.0, rate=16000)])

    tts = ElevenLabsTTS(SimpleNamespace(text_to_speech=Speech()), "v", pitch_semitones=0.0, speed=2.0)
    samples = decode_wav(await tts.synthesize("Bello!"))
    assert abs(len(samples) - 8000) <= 400


def test_build_providers_can_use_elevenlabs_for_live_tts(monkeypatch):
    import sys

    from bob.config import Settings

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda api_key: SimpleNamespace(kind="sync", key=api_key)
    fake_openai.AsyncOpenAI = lambda api_key: SimpleNamespace(kind="async", key=api_key)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    fake_eleven = types.ModuleType("elevenlabs")
    fake_eleven.ElevenLabs = lambda api_key: SimpleNamespace(kind="eleven", key=api_key)
    monkeypatch.setitem(sys.modules, "elevenlabs", fake_eleven)

    settings = Settings(
        mode="cloud",
        openai_api_key="sk-test",
        tts_provider="elevenlabs",
        elevenlabs_api_key="el-test",
        elevenlabs_voice_id="v1",
        pitch_semitones=2.0,
        speed=1.1,
    )
    _, _, tts = build_providers(settings)
    assert isinstance(tts, ElevenLabsTTS) and tts.client.key == "el-test" and tts.voice_id == "v1"
    assert tts.pitch_semitones == 2.0 and tts.speed == 1.1

    with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID"):
        build_providers(
            Settings(mode="cloud", openai_api_key="sk", tts_provider="elevenlabs", elevenlabs_api_key="el")
        )
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        build_providers(
            Settings(mode="cloud", openai_api_key="sk", tts_provider="elevenlabs", elevenlabs_voice_id="v")
        )


async def test_elevenlabs_tts_sends_expressive_voice_settings():
    calls = []

    class Speech:
        def convert(self, **kwargs):
            calls.append(kwargs)
            return iter([pcm_sine(seconds=0.1, rate=16000)])

    tts = ElevenLabsTTS(SimpleNamespace(text_to_speech=Speech()), "v", pitch_semitones=0.0, speed=1.0)
    await tts.synthesize("Bello!")
    settings = calls[0]["voice_settings"]
    get = settings.get if isinstance(settings, dict) else lambda key: getattr(settings, key)
    assert 0.0 <= get("stability") <= 0.5  # low stability: more expressive, more Minion
    assert get("style") >= 0.3 and get("similarity_boost") >= 0.7
    custom = ElevenLabsTTS(
        SimpleNamespace(text_to_speech=Speech()),
        "v",
        pitch_semitones=0.0,
        speed=1.0,
        voice_settings={"stability": 0.9},
    )
    await custom.synthesize("Bello!")
    settings = calls[1]["voice_settings"]
    get = settings.get if isinstance(settings, dict) else lambda key: getattr(settings, key)
    assert get("stability") == 0.9


async def test_cloud_tts_outputs_are_full_scale():
    quiet = (np.frombuffer(pcm_sine(seconds=0.5, rate=24000), dtype="<i2") // 8).astype("<i2").tobytes()
    tts = OpenAITTS(FakeOpenAI(pcm=quiet), "m", "ash", "", pitch_semitones=0.0, speed=1.0)
    assert 0.9 <= np.abs(decode_wav(await tts.synthesize("Bello!"))).max() <= 0.99

    class Speech:
        def convert(self, **kwargs):
            return iter(
                [(np.frombuffer(pcm_sine(seconds=0.5, rate=16000), dtype="<i2") // 8).astype("<i2").tobytes()]
            )

    eleven = ElevenLabsTTS(SimpleNamespace(text_to_speech=Speech()), "v", pitch_semitones=0.0, speed=1.0)
    assert 0.9 <= np.abs(decode_wav(await eleven.synthesize("Bello!"))).max() <= 0.99
