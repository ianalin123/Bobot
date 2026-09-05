import {TurnDetector, concat, rms, wav16, base64, Epoch} from './vad.mjs';

export class AudioEngine {
  constructor({onSpeech, onTurn, onLevel, onPlayback, onIdle, onError}) {
    Object.assign(this, {onSpeech, onTurn, onLevel, onPlayback, onIdle, onError});
    this.epoch = new Epoch(); this.queue = []; this.source = null; this.pumping = false;
    this.mode = 'handsfree'; this.holding = false; this.frames = []; this.stream = null;
  }
  async unlock() {
    this.context ??= new AudioContext();
    await this.context.resume();
  }
  cancel() {
    this.epoch.next(); this.queue = [];
    if (this.source) { this.source.onended = null; this.source.stop(); this.source.disconnect(); this.source = null; }
    this.resolvePlayback?.(); this.resolvePlayback = null;
    this.pumping = false;
  }
  enqueue(message) {
    this.queue.push(message);
    if (!this.pumping) void this.pump(this.epoch.value).catch(this.onError);
  }
  async pump(epoch) {
    this.pumping = true;
    while (this.queue.length && this.epoch.current(epoch)) {
      const message = this.queue.shift();
      const bytes = Uint8Array.from(atob(message.wav), c => c.charCodeAt(0));
      const buffer = await this.context.decodeAudioData(bytes.buffer);
      if (!this.epoch.current(epoch)) return;
      const source = this.context.createBufferSource(); source.buffer = buffer;
      source.connect(this.context.destination); this.source = source;
      this.onPlayback('playback_started', message);
      await new Promise(resolve => { this.resolvePlayback = resolve; source.onended = resolve; source.start(); });
      if (!this.epoch.current(epoch)) return;
      source.disconnect(); this.source = null;
      this.onPlayback('playback_finished', message);
    }
    if (this.epoch.current(epoch)) { this.pumping = false; this.onIdle(); }
  }
  async enableMic() {
    if (this.stream) return;
    await this.unlock();
    const stream = await navigator.mediaDevices.getUserMedia({audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
    }});
    try {
      await this.context.audioWorklet.addModule('/assets/mic-worklet.js');
      this.stream = stream;
      this.input = this.context.createMediaStreamSource(stream);
      this.worklet = new AudioWorkletNode(this.context, 'bob-microphone');
      this.detector = new TurnDetector(this.context.sampleRate, {
        onStart: () => this.onSpeech(), onEnd: samples => this.submit(samples),
      });
      this.worklet.port.onmessage = ({data}) => {
        this.onLevel(rms(data));
        if (this.mode === 'handsfree') this.detector.push(data);
        else if (this.holding) {
          this.frames.push(data);
          if (this.frames.length * 1024 > this.context.sampleRate * 15) this.release();
        }
      };
      this.input.connect(this.worklet); this.worklet.connect(this.context.destination);
    } catch (error) { stream.getTracks().forEach(track => track.stop()); this.stream = null; throw error; }
  }
  disableMic() {
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    this.input?.disconnect(); this.worklet?.disconnect();
    if (this.worklet) this.worklet.port.onmessage = null;
    this.frames = []; this.holding = false; this.onLevel(0);
  }
  setMode(mode) { this.mode = mode; this.detector?.reset(); this.frames = []; this.holding = false; }
  hold() {
    if (!this.stream || this.mode !== 'ptt' || this.holding) return;
    this.frames = []; this.holding = true; this.onSpeech();
  }
  release() {
    if (!this.holding) return;
    this.holding = false; const samples = concat(this.frames); this.frames = [];
    this.submit(samples);
  }
  submit(samples) {
    if (samples.length < this.context.sampleRate * .2) { this.onIdle(); return; }
    this.onTurn(base64(wav16(samples, this.context.sampleRate)));
  }
}
