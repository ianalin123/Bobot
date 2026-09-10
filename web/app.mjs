import {AudioEngine} from './audio.mjs';
const $ = id => document.getElementById(id);
let ws, requestId = 0, online = false, voice = 'idle', state = {}, assistantLine = null;
let generated = false, micBusy = false;
const metrics = {}, scene = {person: null, doa: null};
const error = value => { $('error').textContent = value instanceof Error ? value.message : String(value); };
const send = data => { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data)); };
function setVoice(value) {
  voice = value; document.body.dataset.voice = value;
  $('voice-state').textContent = {idle: 'Ready to meet you', listening: 'Listening…', thinking: 'Thinking locally…',
    speaking: 'Bob is speaking · you can interrupt', transcribing: 'Recognizing speech locally…', stopped: 'Disconnected'}[value] || value;
  render();
}
function interrupt() {
  audio.cancel(); requestId++; generated = false; assistantLine = null;
  send({type: 'interrupt', request_id: requestId});
  setVoice(audio.stream ? 'listening' : 'idle');
}
const audio = new AudioEngine({
  onSpeech() { interrupt(); setVoice('listening'); },
  onTurn(wav) { $('error').textContent = ''; send({type: 'audio_turn', request_id: requestId, wav}); },
  onLevel(level) { $('level').style.width = `${Math.min(100, level * 700)}%`; },
  onPlayback(type, message) {
    if (message.request_id !== requestId) return;
    send({type, request_id: requestId, segment_id: message.segment_id});
    if (type === 'playback_started') setVoice('speaking');
  },
  onIdle() { setVoice(generated ? (audio.stream ? 'listening' : 'idle') : 'thinking'); },
  onError(value) { error(value); interrupt(); },
});
function line(role, text = '') {
  $('transcript').querySelector('.empty')?.remove();
  const p = document.createElement('p'); p.className = role; p.textContent = text;
  $('transcript').append(p);
  while ($('transcript').children.length > 40) $('transcript').firstChild.remove();
  return p;
}
function scroll() { $('transcript').scrollTop = $('transcript').scrollHeight; }
function renderScene() {
  const p = scene.person, d = scene.doa;
  const who = p ? `${p.name || 'stranger'} at x ${p.cx.toFixed(2)}, size ${p.size.toFixed(2)}${p.emotion ? ` · ${p.emotion}` : ''}` : 'nobody';
  $('scene').textContent = `Scene: ${who} · voice ${d ? `${d.angle}°${d.speech ? ' speaking' : ''}` : '—'}`;
}
function render() {
  const phase = state.phase || 'idle';
  $('robot').className = ['robot', state.expression || 'curious', phase, voice,
    ['presenting', 'waiting', 'releasing'].includes(phase) ? 'offering' : ''].join(' ');
  $('pocket').textContent = state.banana === 'compartment' ? '🍌' : '';
  $('held').textContent = state.banana === 'hand' ? '🍌' : '';
  $('status').textContent = `${state.stopped ? 'STOPPED · reset required' : phase.replaceAll('_', ' ')} · Banana: ${state.banana || '—'} · Deliveries: ${state.deliveries || 0}${state.error ? ` · ${state.error}` : ''}`;
  $('offer').disabled = !online || state.stopped || phase !== 'idle' || state.banana !== 'compartment';
  $('take').disabled = !online || state.stopped || phase !== 'waiting';
  $('reload').disabled = !online || state.stopped || phase !== 'idle' || state.banana === 'compartment';
  for (const id of ['stop', 'reset', 'send']) $(id).disabled = !online;
  $('mic').disabled = !online || micBusy;
  $('hold').disabled = !audio.stream || !online;
  $('events').replaceChildren(...(state.events || []).map(text => { const li = document.createElement('li'); li.textContent = text; return li; }));
}
function connect() {
  const token = new URLSearchParams(location.search).get('token');
  ws = new WebSocket(`ws://${location.host}/ws${token ? `?token=${encodeURIComponent(token)}` : ''}`);
  ws.onopen = () => { online = true; $('connection').textContent = 'Local runtime connected'; $('connection').classList.add('online'); render(); };
  ws.onclose = () => {
    online = false; audio.cancel(); audio.disableMic(); $('mic').textContent = 'Enable microphone';
    $('connection').textContent = 'Disconnected · reload to reconnect'; $('connection').classList.remove('online');
    setVoice('stopped'); error('Runtime disconnected, or another tab controls Bob. Check the runtime and reload.');
  };
  ws.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if ('request_id' in message && message.request_id !== requestId) return;
    switch (message.type) {
      case 'robot_state': state = message.state; render(); break;
      case 'transcript': line('user', message.text); scroll(); break;
      case 'assistant_delta': assistantLine ??= line('assistant'); assistantLine.textContent += message.text; scroll(); break;
      case 'voice_state': setVoice(message.state); break;
      case 'turn_started': assistantLine = null; generated = false; break;
      case 'audio': audio.enqueue(message); break;
      case 'turn_done': generated = true; if (!audio.pumping) setVoice(audio.stream ? 'listening' : 'idle'); break;
      case 'metric': metrics[message.name] = message.value; $('metrics').textContent = Object.entries(metrics).map(([k, v]) => `${k.replaceAll('_', ' ')}: ${v} ms`).join(' · '); break;
      case 'error': error(message.message); interrupt(); break;
      case 'action_result': if (!message.result.accepted) error(message.result.reason); break;
      case 'person': scene.person = message.person; renderScene(); break;
      case 'doa': scene.doa = message; renderScene(); break;
    }
  };
}
$('chat').onsubmit = async event => {
  event.preventDefault(); const text = $('message').value.trim(); if (!text || !online) return;
  try { await audio.unlock(); interrupt(); $('error').textContent = ''; $('message').value = ''; send({type: 'user_text', request_id: requestId, text}); }
  catch (value) { error(value); }
};
$('mic').onclick = async () => {
  if (audio.stream) { audio.disableMic(); interrupt(); $('mic').textContent = 'Enable microphone'; render(); return; }
  micBusy = true; render();
  try { await audio.enableMic(); $('mic').textContent = 'Microphone on · disable'; setVoice('listening'); $('error').textContent = ''; }
  catch (value) { error(`Microphone unavailable: ${value.message}`); }
  finally { micBusy = false; render(); }
};
$('mic-mode').onchange = () => { audio.setMode($('mic-mode').value); $('hold').hidden = audio.mode !== 'ptt'; };
$('hold').onpointerdown = event => { event.preventDefault(); $('hold').setPointerCapture(event.pointerId); audio.hold(); };
$('hold').onpointerup = () => audio.release(); $('hold').onpointercancel = () => audio.release();
$('hold').onkeydown = event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); audio.hold(); } };
$('hold').onkeyup = event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); audio.release(); } };
$('interrupt').onclick = interrupt;
for (const [id, action] of Object.entries({offer: 'offer_banana', take: 'take_banana', reload: 'reload', reset: 'reset_simulation', stop: 'stop_motion'})) {
  $(id).onclick = () => { if (id === 'stop') interrupt(); send({type: 'action', action}); };
}
document.querySelectorAll('[data-mood]').forEach(button => button.onclick = () => send({type: 'expression', expression: button.dataset.mood}));
$('face').onclick = () => document.body.classList.toggle('fullscreen');
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { interrupt(); send({type: 'action', action: 'stop_motion'}); document.body.classList.remove('fullscreen'); }
  if (event.key.toLowerCase() === 'f' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) $('face').click();
});
window.addEventListener('blur', () => audio.release());
window.addEventListener('pagehide', () => { audio.disableMic(); audio.cancel(); ws?.close(); });
setInterval(() => { $('robot').classList.add('blink'); setTimeout(() => $('robot').classList.remove('blink'), 140); }, 4200);
async function health() {
  try {
    const data = await (await fetch('/api/health')).json();
    $('setup').textContent = [['stt', 'Ears'], ['llm', 'Brain'], ['tts', 'Voice']].map(([key, label]) => `${label}: ${data[key].ready ? '✓' : 'not ready'} ${data[key].model || data[key].engine || ''}${data[key].error ? ` (${data[key].error})` : ''}`).join(' · ');
  } catch { $('setup').textContent = 'Local runtime unavailable. See README setup instructions.'; }
}
connect(); void health(); setInterval(health, 8000);
