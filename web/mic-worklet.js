class MicrophoneCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Float32Array(1024); this.offset = 0; }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) for (const sample of channel) {
      this.buffer[this.offset++] = sample;
      if (this.offset === this.buffer.length) {
        this.port.postMessage(this.buffer.slice()); this.offset = 0;
      }
    }
    // Leave all outputs silent: capture must never monitor the microphone.
    return true;
  }
}
registerProcessor('bob-microphone', MicrophoneCapture);
