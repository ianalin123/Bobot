// Pure signal logic, shared by hands-free capture and synthetic tests.
export const rms = data => Math.sqrt(data.reduce((sum, x) => sum + x * x, 0) / (data.length || 1));

export class TurnDetector {
  constructor(rate, {onStart, onEnd, threshold = 0.018}) {
    Object.assign(this, {rate, onStart, onEnd, threshold});
    this.reset();
  }
  reset() {
    this.active = false; this.loud = 0; this.quiet = 0; this.duration = 0;
    this.pre = []; this.preSize = 0; this.frames = [];
  }
  push(frame) {
    const dt = frame.length / this.rate;
    const speech = rms(frame) >= this.threshold;
    if (!this.active) {
      this.pre.push(frame.slice()); this.preSize += frame.length;
      while (this.preSize > this.rate * .3 && this.pre.length > 1) this.preSize -= this.pre.shift().length;
      this.loud = speech ? this.loud + dt : 0;
      if (this.loud >= .14) {
        this.active = true; this.frames = this.pre; this.pre = []; this.preSize = 0;
        this.duration = this.loud; this.quiet = 0; this.onStart();
      }
      return;
    }
    this.frames.push(frame.slice()); this.duration += dt;
    this.quiet = speech ? 0 : this.quiet + dt;
    if (this.quiet >= .65 || this.duration >= 15) {
      const frames = this.frames;
      this.reset();
      this.onEnd(concat(frames));
    }
  }
}

export function concat(frames) {
  const out = new Float32Array(frames.reduce((n, f) => n + f.length, 0));
  let offset = 0;
  for (const frame of frames) { out.set(frame, offset); offset += frame.length; }
  return out;
}

export function wav16(samples, sourceRate) {
  // Windowed averaging for the usual 44.1/48 kHz microphone -> 16 kHz speech path.
  const rate = 16000, length = Math.floor(samples.length * rate / sourceRate);
  const buffer = new ArrayBuffer(44 + length * 2), view = new DataView(buffer);
  const ascii = (offset, str) => [...str].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  ascii(0, 'RIFF'); view.setUint32(4, 36 + length * 2, true); ascii(8, 'WAVEfmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, 'data');
  view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * sourceRate / rate), end = Math.max(start + 1, Math.floor((i + 1) * sourceRate / rate));
    let sum = 0;
    for (let j = start; j < Math.min(end, samples.length); j++) sum += samples[j];
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    view.setInt16(44 + i * 2, value * (value < 0 ? 32768 : 32767), true);
  }
  return buffer;
}

export function base64(buffer) {
  let text = '';
  for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
  return btoa(text);
}

// An epoch guards async decoding too, not just queued or currently playing audio.
export class Epoch {
  value = 0;
  next() { return ++this.value; }
  current(value) { return value === this.value; }
}
