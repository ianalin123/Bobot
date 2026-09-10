import asyncio
import io
import wave

import pytest

from bob.providers import LocalLLM, decode_wav
from bob.robot import Robot, SimHardware
from bob.voice import VoiceSession, direct_action


class LLM:
    async def stream(self, messages, tools):
        yield {"content": "Bello! "}
        yield {"content": "A banana for you."}


class TTS:
    async def synthesize(self, text):
        return b"fake-wav"


def session(llm=None, tts=None, stt=None):
    events = []

    async def send(event):
        events.append(event)

    return VoiceSession(send, stt, llm or LLM(), tts or TTS(), Robot(SimHardware(0.001))), events


async def test_history_includes_only_played_sentences():
    voice, events = session()
    await voice.start(1, text="Hello")
    await voice.task
    assert len([e for e in events if e["type"] == "audio"]) == 2
    voice.acknowledge(1, 0, False)
    voice.acknowledge(1, 0, True)
    voice.acknowledge(1, 1, False)
    await voice.interrupt(2)
    assert voice.history[-1]["content"] == "Bello! [The next sentence was interrupted.]"
    assert "A banana for you." not in str(voice.history)


async def test_cancellation_kills_tts_and_no_stale_audio():
    started, cancelled = asyncio.Event(), asyncio.Event()

    class WaitingTTS:
        async def synthesize(self, text):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    voice, events = session(tts=WaitingTTS())
    await voice.start(1, text="Hello")
    await started.wait()
    await voice.interrupt(2)
    assert cancelled.is_set() and voice.task.cancelled()
    assert not any(e["type"] == "audio" for e in events)
    assert events[-1] == {"type": "interrupted", "request_id": 2}


async def test_late_stt_result_cannot_trigger_tool_or_speech():
    import threading

    entered, finish = threading.Event(), threading.Event()

    class STT:
        def transcribe(self, data):
            entered.set()
            finish.wait(timeout=2)
            return "Give me a banana"

    voice, events = session(stt=STT())
    await voice.start(1, audio=b"input")
    await asyncio.to_thread(entered.wait, 1)
    await voice.interrupt(2)
    finish.set()
    await asyncio.sleep(0.02)
    assert not any(e["type"] in {"transcript", "audio", "tool_result"} for e in events)
    assert voice.robot.state.phase == "idle"


async def test_tools_are_allowlisted_and_behavior_is_shared():
    class ToolLLM:
        async def stream(self, messages, tools):
            if messages[-1]["role"] == "user":
                yield {
                    "tool_calls": [
                        {"function": {"name": "offer_banana", "arguments": {}}},
                        {"function": {"name": "run_shell", "arguments": {}}},
                    ]
                }
            else:
                yield {"content": "I am getting your banana ready."}

    voice, events = session(llm=ToolLLM())
    await voice.start(1, text="Would you offer me one of your bananas?")
    await voice.task
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["result"]["accepted"]
    assert not results[1]["result"]["accepted"]
    await voice.robot.task
    assert voice.robot.state.phase == "waiting"


async def test_old_playback_ack_does_not_change_new_history():
    voice, _ = session()
    await voice.start(1, text="Hello")
    await voice.task
    await voice.start(2, text="Again")
    await voice.task
    voice.acknowledge(1, 0, True)
    voice.acknowledge(2, 1, True)  # Out of order.
    assert not voice.heard
    await voice.interrupt(3)


def wav(rate=16000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x00\x00" * 16000 * channels)
    return buffer.getvalue()


def test_wav_validation():
    assert len(decode_wav(wav())) == 16000
    for data in [b"bad", wav(48000), wav(channels=2), wav()[:-100]]:
        with pytest.raises(ValueError):
            decode_wav(data)


def test_cloud_and_remote_inference_are_rejected(monkeypatch):
    monkeypatch.setenv("BOB_OLLAMA_URL", "https://example.com")
    with pytest.raises(ValueError):
        LocalLLM()
    monkeypatch.setenv("BOB_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("BOB_MODEL", "some-model:cloud")
    with pytest.raises(ValueError):
        LocalLLM()


def test_exact_commands_do_not_match_negated_or_quoted_requests():
    assert direct_action("Please give me a banana.") == "offer_banana"
    assert direct_action("Stop moving!") == "stop_motion"
    for text in [
        "Don't give me a banana",
        "Do not stop",
        "Tell a story about a banana",
        "He said give me a banana",
        "I dislike bananas",
    ]:
        assert direct_action(text) is None


async def test_exact_command_works_even_when_model_is_unreliable():
    class BrokenLLM:
        async def stream(self, messages, tools):
            raise AssertionError("Exact commands must bypass the model")
            yield

    voice, events = session(llm=BrokenLLM())
    await voice.start(1, text="Please give me a banana.")
    await voice.task
    assert any(e["type"] == "tool_result" and e["source"] == "exact_command" for e in events)
    await voice.robot.task
    assert voice.robot.state.phase == "waiting"


def test_direct_action_sing():
    from bob.voice import direct_action

    assert direct_action("Bob, sing the banana song!") is None  # not an exact phrase
    assert direct_action("sing the banana song") == "sing_song"
    assert direct_action("Sing me a song please") == "sing_song"
    assert direct_action("please sing") == "sing_song"


class RecordingLLM:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, tools):
        self.calls.append(messages)
        yield {"content": "Bello!"}


async def test_llm_gets_the_minion_persona_prompt_with_robot_state():
    from bob import persona

    llm = RecordingLLM()
    voice, _ = session(llm=llm)
    await voice.start(1, text="Hi Bob")
    await voice.task
    system = llm.calls[0][0]
    assert system["role"] == "system"
    assert system["content"].startswith(persona.SYSTEM_PROMPT)
    assert persona.minionese_instruction("full") in system["content"]  # Bob speaks Minionese by default
    assert "Robot state:" in system["content"]


def test_exact_commands_switch_minionese_level():
    assert direct_action("speak minionese") == "speak_minionese"
    assert direct_action("Talk Minion, please!") == "speak_minionese"
    assert direct_action("only minionese") == "speak_minionese"
    assert direct_action("speak english") == "speak_english"
    assert direct_action("please speak English") == "speak_english"
    assert direct_action("do you speak minionese") is None
    assert direct_action("minionese is cute") is None


async def test_speak_minionese_switches_level_without_the_llm_and_changes_the_prompt():
    from bob import persona

    llm = RecordingLLM()
    voice, events = session(llm=llm)
    assert voice.minionese == "full"
    await voice.start(1, text="speak english")
    await voice.task
    assert voice.minionese == "english"
    assert [e for e in events if e["type"] == "minionese"][-1]["level"] == "english"
    audio = [e for e in events if e["type"] == "audio"]
    assert audio and audio[0]["text"] == persona.LEVEL_REPLIES["english"]
    assert not llm.calls
    await voice.start(2, text="how are you")
    await voice.task
    assert persona.minionese_instruction("english") in llm.calls[0][0]["content"]
    await voice.start(3, text="speak minionese")
    await voice.task
    assert voice.minionese == "full"
    assert [e for e in events if e["type"] == "minionese"][-1]["level"] == "full"
    assert [e for e in events if e["type"] == "audio"][-1]["text"] == persona.LEVEL_REPLIES["full"]
