import test from 'node:test';
import assert from 'node:assert/strict';
import {TurnDetector, wav16, Epoch} from './vad.mjs';
import {AudioEngine} from './audio.mjs';

test('VAD ignores silence and a brief spike; sustained speech interrupts then completes', () => {
  let starts = 0, turns = [];
  const vad = new TurnDetector(16000, {onStart: () => starts++, onEnd: x => turns.push(x)});
  const frame = value => new Float32Array(320).fill(value);
  for (let i = 0; i < 30; i++) vad.push(frame(0));
  vad.push(frame(.1)); vad.push(frame(0));
  assert.equal(starts, 0);
  for (let i = 0; i < 20; i++) vad.push(frame(.1));
  assert.equal(starts, 1);
  assert.equal(turns.length, 0);
  for (let i = 0; i < 40; i++) vad.push(frame(0));
  assert.equal(turns.length, 1);
  assert.ok(turns[0].length > 16000 * .8);
});

test('VAD caps continuous speech', () => {
  let turns = 0;
  const vad = new TurnDetector(16000, {onStart() {}, onEnd() { turns++; }});
  for (let i = 0; i < 760; i++) vad.push(new Float32Array(320).fill(.1));
  assert.equal(turns, 1);
});

test('WAV encoder emits mono 16 kHz PCM, resamples and clips', () => {
  const view = new DataView(wav16(new Float32Array(48000).fill(2), 48000));
  assert.equal(view.byteLength, 32044);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getInt16(44, true), 32767);
});

test('epoch invalidates old asynchronous work', () => {
  const epoch = new Epoch(); const old = epoch.value;
  epoch.next(); assert.equal(epoch.current(old), false);
});

test('interrupt during async audio decode prevents stale playback', async () => {
  let resolve, played = 0;
  const engine = new AudioEngine({onError: e => { throw e; }, onPlayback() {}, onIdle() {}});
  engine.context = {decodeAudioData: () => new Promise(r => { resolve = r; }), createBufferSource: () => { played++; }};
  engine.enqueue({wav: btoa('wav'), request_id: 1, segment_id: 0});
  engine.cancel(); resolve({});
  await new Promise(r => setImmediate(r));
  assert.equal(played, 0); assert.equal(engine.queue.length, 0);
});

test('interrupt stops active source and clears pending playback', async () => {
  let stopped = 0, acknowledged = [];
  const engine = new AudioEngine({onError: e => { throw e; }, onPlayback: t => acknowledged.push(t), onIdle() {}});
  engine.context = {decodeAudioData: async () => ({}), destination: {}, createBufferSource: () => ({
    connect() {}, disconnect() {}, start() {}, stop() { stopped++; },
  })};
  engine.enqueue({wav: btoa('wav'), request_id: 1, segment_id: 0});
  engine.enqueue({wav: btoa('wav'), request_id: 1, segment_id: 1});
  await new Promise(r => setImmediate(r));
  engine.cancel(); await new Promise(r => setImmediate(r));
  assert.equal(stopped, 1); assert.deepEqual(acknowledged, ['playback_started']);
  assert.equal(engine.queue.length, 0); assert.equal(engine.pumping, false);
});
