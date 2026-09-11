"""Headless audio for the Jetson: energy VAD with pre-roll (port of web/vad.mjs), a cancellable
playback queue on the XVF3800, and a RobotClient that drives VoiceSession exactly like the browser
does (request ids per turn, playback_started/finished acknowledgements per segment)."""

import asyncio
import base64
import io
import logging
import threading
import wave
from collections import deque
from typing import Any, Awaitable, Callable

import numpy as np

from . import voicefx

log = logging.getLogger("bob.audio")

RATE = 16000
BLOCK = 320  # 20 ms at 16 kHz
DEFAULT_DEVICE = "XVF3800"
SILENCE, SPEECH_START, SPEECH, SPEECH_END = "silence", "speech_start", "speech", "speech_end"


def _as_float(frame) -> np.ndarray:
    frame = np.asarray(frame)
    if frame.ndim > 1:
        frame = frame[:, 0]
    if np.issubdtype(frame.dtype, np.integer):
        return frame.astype(np.float32) / 32768.0
    return frame.astype(np.float32, copy=False)


def rms(frame) -> float:
    data = _as_float(frame)
    return float(np.sqrt(np.mean(data * data))) if len(data) else 0.0


def encode_wav(samples: np.ndarray, rate: int = RATE) -> bytes:
    """Mono 16-bit PCM WAV from float (-1..1) or int16 samples."""
    samples = np.asarray(samples)
    if not np.issubdtype(samples.dtype, np.integer):
        samples = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def decode_pcm16k(data: bytes) -> np.ndarray:
    """Any 16-bit PCM WAV -> int16 mono samples at 16 kHz (channels averaged, linear resample)."""
    with wave.open(io.BytesIO(data), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        if width != 2:
            raise ValueError("Expected 16-bit PCM WAV.")
        raw = source.readframes(source.getnframes())
    samples = np.frombuffer(raw[: len(raw) - len(raw) % (2 * channels)], dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(samples):
        length = int(len(samples) * RATE / rate)
        positions = np.arange(length) * (rate / RATE)
        samples = np.interp(positions, np.arange(len(samples)), samples.astype(np.float32))
    return np.asarray(samples).astype("<i2")


class Vad:
    """Energy turn detector. feed() returns silence | speech_start | speech | speech_end per frame."""

    def __init__(
        self,
        rate: int = RATE,
        threshold: float = 0.015,
        pre_roll_s: float = 0.3,
        start_s: float = 0.14,
        trailing_s: float = 0.65,
        max_s: float = 15.0,
    ):
        self.rate, self.threshold = rate, threshold
        self.pre_roll_s, self.start_s, self.trailing_s, self.max_s = pre_roll_s, start_s, trailing_s, max_s
        self.turn: np.ndarray | None = None
        self.reset()

    def reset(self):
        self.active = False
        self.loud = self.quiet = self.duration = 0.0
        self.pre: deque[np.ndarray] = deque()
        self.pre_size = 0
        self.frames: list[np.ndarray] = []

    def feed(self, frame) -> str:
        frame = _as_float(frame)
        dt = len(frame) / self.rate
        speech = rms(frame) >= self.threshold
        if not self.active:
            self.pre.append(frame.copy())
            self.pre_size += len(frame)
            while self.pre_size > self.rate * self.pre_roll_s and len(self.pre) > 1:
                self.pre_size -= len(self.pre.popleft())
            self.loud = self.loud + dt if speech else 0.0
            if self.loud < self.start_s:
                return SILENCE
            self.active = True
            self.frames, self.pre, self.pre_size = list(self.pre), deque(), 0
            self.duration, self.quiet = self.loud, 0.0
            return SPEECH_START
        self.frames.append(frame.copy())
        self.duration += dt
        self.quiet = 0.0 if speech else self.quiet + dt
        if self.quiet >= self.trailing_s or self.duration >= self.max_s:
            self.turn = np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)
            self.reset()
            return SPEECH_END
        return SPEECH

    def take_turn(self) -> bytes:
        """WAV (16 kHz mono int16) of the last completed utterance, including its pre-roll."""
        if self.turn is None:
            raise RuntimeError("No completed utterance to take.")
        samples, self.turn = self.turn, None
        return encode_wav(samples, self.rate)


def find_device(devices, name: str, kind: str) -> int | None:
    """Index of the first device whose name contains `name` and has `kind` (input|output) channels."""
    needle = name.lower()
    for index, device in enumerate(devices):
        if needle in str(device.get("name", "")).lower() and device.get(f"max_{kind}_channels", 0) > 0:
            return index
    return None


class _Playing:
    __slots__ = ("segment_id", "samples", "position", "done", "epoch")

    def __init__(self, segment_id, samples, done, epoch):
        self.segment_id, self.samples, self.position, self.done, self.epoch = (
            segment_id,
            samples,
            0,
            done,
            epoch,
        )


class AudioIO:
    """Capture (VAD -> on_turn), playback queue (on_playback acks) and barge-in (on_barge_in).

    Callbacks run on the asyncio loop that called start(); PortAudio threads only hand frames over
    with loop.call_soon_threadsafe. `sounddevice` is imported lazily so the module loads without
    PortAudio (tests, the browser workbench on the Mac).
    """

    def __init__(
        self,
        settings,
        on_turn: Callable[[bytes], Awaitable] | None = None,
        on_barge_in: Callable[[], Awaitable] | None = None,
        on_playback: Callable[[int, bool], Any] | None = None,
        vad: Vad | None = None,
        barge_in: bool = True,
    ):
        self.settings = settings
        self.on_turn, self.on_barge_in, self.on_playback = on_turn, on_barge_in, on_playback
        self.vad = vad or Vad()
        self.barge_in = barge_in
        self.speaking = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.input = self.output = None
        self._pump_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._lock = threading.Lock()
        self._playing: _Playing | None = None
        self._epoch = 0
        self.device_info = {"input": None, "output": None}

    def start(self):
        import sounddevice as sd

        self.loop = asyncio.get_running_loop()
        name = self.settings.audio_device or DEFAULT_DEVICE
        devices = list(sd.query_devices())
        in_index, out_index = find_device(devices, name, "input"), find_device(devices, name, "output")
        if in_index is None or out_index is None:
            log.warning("Audio device %r not found; falling back to the default device", name)
        channels = min(2, devices[in_index]["max_input_channels"]) if in_index is not None else 1
        self.device_info = {"input": in_index, "output": out_index, "capture_channels": channels}
        self.input = sd.InputStream(
            samplerate=RATE,
            blocksize=BLOCK,
            dtype="int16",
            channels=channels,
            device=in_index,
            callback=self._input_callback,
        )
        self.output = sd.OutputStream(
            samplerate=RATE,
            blocksize=BLOCK,
            dtype="int16",
            channels=1,
            device=out_index,
            callback=self._output_callback,
        )
        self.input.start()
        self.output.start()
        self._pump_task = self.loop.create_task(self._pump())

    def close(self):
        self.cancel_playback()
        for stream in (self.input, self.output):
            if stream is not None:
                stream.stop()
                stream.close()
        self.input = self.output = None
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None

    # Capture -----------------------------------------------------------------------------------

    def _input_callback(self, indata, frames, time_info, status):
        if status:
            log.debug("input status: %s", status)
        frame = np.asarray(indata)[:, 0].copy() if np.ndim(indata) > 1 else np.asarray(indata).copy()
        self.loop.call_soon_threadsafe(self._on_frame, frame)

    def _on_frame(self, frame):
        state = self.vad.feed(frame)
        if state == SPEECH_START and self.speaking and self.barge_in and self.on_barge_in:
            self._spawn(self.on_barge_in())
        elif state == SPEECH_END and self.on_turn:
            self._spawn(self.on_turn(self.vad.take_turn()))

    def _spawn(self, coro):
        task = self.loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("audio callback failed", exc_info=task.exception())

    # Playback ----------------------------------------------------------------------------------

    def play(self, segment_id, wav: bytes):
        try:
            samples = decode_pcm16k(wav)
        except Exception as exc:
            log.error("dropping segment %s: undecodable WAV (%s)", segment_id, exc)
            return
        # Everything Bob says (live TTS, cached phrases, songs) leaves the speaker at full scale.
        self.queue.put_nowait((segment_id, voicefx.normalize(samples)))

    def cancel_playback(self):
        """Drop queued and current audio; the next output block is silence."""
        self._epoch += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        with self._lock:
            playing, self._playing = self._playing, None
        if playing is not None:
            playing.done.set()
        self.speaking = False

    async def _pump(self):
        while True:
            segment_id, samples = await self.queue.get()
            epoch = self._epoch
            playing = _Playing(segment_id, samples, asyncio.Event(), epoch)
            with self._lock:
                self._playing = playing
            self.speaking = True
            self._emit_playback(segment_id, True)
            await playing.done.wait()
            if epoch != self._epoch:
                continue  # Cancelled; the finished ack must not reach the session.
            if self.queue.empty():
                self.speaking = False
            self._emit_playback(segment_id, False)

    def _emit_playback(self, segment_id, started):
        if self.on_playback:
            self.on_playback(segment_id, started)

    def _output_callback(self, outdata, frames, time_info, status):
        if status:
            log.debug("output status: %s", status)
        out = np.asarray(outdata)
        finished = None
        with self._lock:
            playing = self._playing
            if playing is None:
                out.fill(0)
                return
            chunk = playing.samples[playing.position : playing.position + frames]
            count = len(chunk)
            out[:count, 0] = chunk
            out[count:] = 0
            playing.position += count
            if playing.position >= len(playing.samples):
                self._playing, finished = None, playing
        if finished is not None:
            self.loop.call_soon_threadsafe(finished.done.set)


class FakeAudioIO:
    """Same surface as AudioIO without devices. feed_wav() injects a turn (with a barge-in first
    when something is playing); drain() plays everything queued and emits the acks in order."""

    def __init__(self, on_turn=None, on_barge_in=None, on_playback=None):
        self.on_turn, self.on_barge_in, self.on_playback = on_turn, on_barge_in, on_playback
        self.speaking = False
        self.started = False
        self.played: list[tuple[Any, bytes]] = []
        self.pending: list[Any] = []
        self.cancels = 0
        self.epoch = 0
        self.log: list[tuple] = []
        self.device_info = {"input": None, "output": None}

    def start(self):
        self.started = True

    def close(self):
        self.cancel_playback()
        self.started = False

    def play(self, segment_id, wav: bytes):
        self.played.append((segment_id, wav))
        self.pending.append(segment_id)
        self.log.append(("play", segment_id))
        self.speaking = True

    def cancel_playback(self):
        self.cancels += 1
        self.epoch += 1
        self.pending.clear()
        self.log.append(("cancel",))
        self.speaking = False

    async def drain(self):
        """Play everything queued: started ack, one loop turn (a cancel here drops the rest), finished ack."""
        while self.pending:
            segment_id, epoch = self.pending.pop(0), self.epoch
            self.log.append(("started", segment_id))
            if self.on_playback:
                self.on_playback(segment_id, True)
            await asyncio.sleep(0)
            if epoch != self.epoch:
                return
            self.log.append(("finished", segment_id))
            if self.on_playback:
                self.on_playback(segment_id, False)
        self.speaking = False

    async def feed_wav(self, wav: bytes):
        if self.speaking and self.on_barge_in:
            await self.on_barge_in()
        if self.on_turn:
            await self.on_turn(wav)


class RobotClient:
    """The browser's app.mjs without a browser: owns request ids, feeds turns into a VoiceSession,
    routes `audio` events to the speaker and converts playback callbacks into acknowledgements."""

    def __init__(self, session, audio_io, history: int = 50):
        self.audio = audio_io
        self.request_id = 0
        self.events: deque[dict] = deque(maxlen=history)
        self.on_event: Callable[[dict], None] | None = None  # sync hook; the Director subscribes here
        self.lock = asyncio.Lock()
        self.session = session(self.send) if callable(session) else session
        self.session.send = self.send
        audio_io.on_turn = self.handle_turn
        audio_io.on_barge_in = self.handle_barge_in
        audio_io.on_playback = self.handle_playback

    def record(self, event: dict):
        event = {key: value for key, value in event.items() if key != "wav"}
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    async def send(self, event: dict):
        if "request_id" in event and event["request_id"] != self.request_id:
            return  # Stale turn, exactly as the browser drops it.
        self.record(event)
        if event["type"] == "audio":
            self.audio.play(event["segment_id"], base64.b64decode(event["wav"]))

    async def handle_turn(self, wav: bytes):
        async with self.lock:
            self.audio.cancel_playback()
            self.request_id += 1
            await self.session.start(self.request_id, audio=wav)

    async def text_turn(self, text: str):
        async with self.lock:
            self.audio.cancel_playback()
            self.request_id += 1
            await self.session.start(self.request_id, text=text)

    async def handle_barge_in(self):
        async with self.lock:
            self.audio.cancel_playback()
            self.request_id += 1
            self.record({"type": "barge_in", "request_id": self.request_id})
            await self.session.interrupt(self.request_id)

    def handle_playback(self, segment_id, started: bool):
        kind = "playback_started" if started else "playback_finished"
        self.record({"type": kind, "request_id": self.request_id, "segment_id": segment_id})
        self.session.acknowledge(self.request_id, segment_id, not started)
