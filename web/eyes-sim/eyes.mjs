// Browser twin of the ESP32 eye firmware: same 360 px geometry, same expression table, same protocol.
// Left canvas = green iris, right canvas = brown iris (Bob's heterochromia). Demo mode drives the eyes
// from the select box / mouse / keys; when the server pushes `eyes` events they take over.
import {EXPRESSIONS, EXPRESSION_NAMES} from './expressions.mjs';

const SIZE = 360, CENTER = 180, SCLERA_R = 150, IRIS_R = 62, PUPIL_R = 28, GAZE_PX = 60, SACCADE_PX = 6;
const SMOOTH = 0.25, BLINK_CLOSE_MS = 120, BLINK_OPEN_MS = 100, LIVE_HOLD_MS = 2000, SEND_INTERVAL_MS = 50;
const IRIS = {
  left:  {base: '#3F8F3A', light: '#62B35A', ring: '#245A22'},
  right: {base: '#6B3E1E', light: '#95602F', ring: '#3A2010'},
};

const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export class Eye {
  constructor(canvas, side) {
    this.canvas = canvas; this.side = side; this.ctx = canvas.getContext('2d');
    this.cur = {upperLid: 0.12, lowerLid: 0.05, irisScale: 1, pupilScale: 1, heart: 0, tilt: 0, gx: 0, gy: 0, sx: 0, sy: 0};
    this.target = {...this.cur};
    this.blinkStart = -1; this.nextBlink = 0; this.nextSaccade = 0; this.blinkRateMs = 4500;
  }

  setState(state) {
    const e = EXPRESSIONS[state.e] || EXPRESSIONS.neutral;
    const asym = this.side === 'right' ? e.lidAsym : 0;
    Object.assign(this.target, {
      upperLid: clamp(e.upperLid - asym, 0, 1), lowerLid: e.lowerLid, irisScale: e.irisScale,
      pupilScale: e.pupilScale * clamp(state.p ?? 1, 0.3, 2), heart: e.pupilShape === 'heart' ? 1 : 0,
      tilt: e.tilt, gx: clamp(state.gx ?? 0, -1, 1), gy: clamp(state.gy ?? 0, -1, 1),
    });
    this.blinkRateMs = e.blinkRateMs;
  }

  blink(now) { if (this.blinkStart < 0 || now - this.blinkStart > BLINK_CLOSE_MS + BLINK_OPEN_MS) this.blinkStart = now; }

  blinkAmount(now) {
    if (this.blinkStart < 0) return 0;
    const t = now - this.blinkStart;
    if (t < BLINK_CLOSE_MS) return t / BLINK_CLOSE_MS;
    if (t < BLINK_CLOSE_MS + BLINK_OPEN_MS) return 1 - (t - BLINK_CLOSE_MS) / BLINK_OPEN_MS;
    this.blinkStart = -1; return 0;
  }

  step(now) {
    if (now >= this.nextBlink) {
      if (this.nextBlink > 0) this.blink(now);
      this.nextBlink = now + this.blinkRateMs * (0.7 + Math.random() * 0.6);
    }
    if (now >= this.nextSaccade) {
      this.target.sx = (Math.random() * 2 - 1) * SACCADE_PX; this.target.sy = (Math.random() * 2 - 1) * SACCADE_PX;
      this.nextSaccade = now + 1000 + Math.random() * 2000;
    }
    for (const k in this.target) this.cur[k] = lerp(this.cur[k], this.target[k], SMOOTH);
    this.draw(this.blinkAmount(now));
  }

  draw(blink) {
    const {ctx, cur} = this;
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.save(); ctx.translate(CENTER, CENTER);
    ctx.beginPath(); ctx.arc(0, 0, SCLERA_R, 0, Math.PI * 2); ctx.clip();
    const sclera = ctx.createRadialGradient(0, -20, 40, 0, 0, SCLERA_R);
    sclera.addColorStop(0, '#fffdf4'); sclera.addColorStop(0.8, '#f4f1e4'); sclera.addColorStop(1, '#cfcdbf');
    ctx.fillStyle = sclera; ctx.fillRect(-SCLERA_R, -SCLERA_R, SIZE, SIZE);

    const ix = cur.gx * GAZE_PX + cur.sx, iy = cur.gy * GAZE_PX + cur.sy, ir = IRIS_R * cur.irisScale;
    const colours = IRIS[this.side];
    ctx.save(); ctx.translate(ix, iy);
    const iris = ctx.createRadialGradient(0, 0, ir * 0.3, 0, 0, ir);
    iris.addColorStop(0, colours.light); iris.addColorStop(0.55, colours.base); iris.addColorStop(1, colours.ring);
    ctx.beginPath(); ctx.arc(0, 0, ir, 0, Math.PI * 2); ctx.fillStyle = iris; ctx.fill();
    ctx.lineWidth = ir * 0.08; ctx.strokeStyle = colours.ring; ctx.stroke();
    ctx.save(); ctx.globalAlpha = 0.35; ctx.strokeStyle = colours.ring; ctx.lineWidth = 1.5;
    for (let a = 0; a < Math.PI * 2; a += Math.PI / 14) {
      ctx.beginPath(); ctx.moveTo(Math.cos(a) * ir * 0.45, Math.sin(a) * ir * 0.45);
      ctx.lineTo(Math.cos(a) * ir * 0.95, Math.sin(a) * ir * 0.95); ctx.stroke();
    }
    ctx.restore();
    const pr = PUPIL_R * cur.pupilScale;
    ctx.fillStyle = '#0b0b0b';
    if (cur.heart > 0.5) this.heart(ctx, pr * 1.15); else { ctx.beginPath(); ctx.arc(0, 0, pr, 0, Math.PI * 2); ctx.fill(); }
    ctx.fillStyle = 'rgba(255,255,255,0.92)';
    ctx.beginPath(); ctx.arc(-ir * 0.38, -ir * 0.4, ir * 0.2, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,0.6)';
    ctx.beginPath(); ctx.arc(ir * 0.32, ir * 0.36, ir * 0.09, 0, Math.PI * 2); ctx.fill();
    ctx.restore();

    const upper = Math.max(cur.upperLid, blink), lower = Math.max(cur.lowerLid, blink * 0.35);
    const sign = this.side === 'left' ? -1 : 1;
    ctx.fillStyle = '#000';
    ctx.save(); ctx.rotate(sign * cur.tilt * Math.PI / 180);
    const upperEdge = -SCLERA_R + upper * SCLERA_R * 2;
    ctx.beginPath(); ctx.moveTo(-SIZE, -SIZE); ctx.lineTo(SIZE, -SIZE); ctx.lineTo(SIZE, upperEdge);
    ctx.quadraticCurveTo(0, upperEdge + 36 * (1 - blink), -SIZE, upperEdge); ctx.closePath(); ctx.fill();
    ctx.restore();
    const lowerEdge = SCLERA_R - lower * SCLERA_R * 2;
    ctx.beginPath(); ctx.moveTo(-SIZE, SIZE); ctx.lineTo(SIZE, SIZE); ctx.lineTo(SIZE, lowerEdge);
    ctx.quadraticCurveTo(0, lowerEdge - 44 * lower, -SIZE, lowerEdge); ctx.closePath(); ctx.fill();
    ctx.restore();
  }

  heart(ctx, r) {  // two circles + a triangle, mirroring the firmware
    ctx.beginPath(); ctx.arc(-r * 0.5, -r * 0.3, r * 0.55, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(r * 0.5, -r * 0.3, r * 0.55, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.moveTo(-r * 1.02, -r * 0.12); ctx.lineTo(r * 1.02, -r * 0.12); ctx.lineTo(0, r); ctx.closePath(); ctx.fill();
  }
}

if (typeof document !== 'undefined') {
  const eyes = [new Eye(document.getElementById('left'), 'left'), new Eye(document.getElementById('right'), 'right')];
  const select = document.getElementById('expression'), badge = document.getElementById('link'), hint = document.getElementById('state');
  for (const name of EXPRESSION_NAMES) select.append(new Option(name, name));
  const state = {e: 'neutral', gx: 0, gy: 0, blink: false, p: 1};
  let ws = null, lastSent = '', liveUntil = 0, sendTimer = 0, beforeLove = 'neutral', firstMessage = true;
  const live = () => performance.now() < liveUntil;

  const apply = (next, {fromServer = false} = {}) => {
    const blinkEdge = next.blink && !state.blink;
    Object.assign(state, next);
    for (const eye of eyes) { eye.setState(state); if (blinkEdge) eye.blink(performance.now()); }
    hint.textContent = JSON.stringify(state);
    if (!fromServer) queueSend();
  };
  const queueSend = () => {
    if (sendTimer || ws?.readyState !== WebSocket.OPEN) return;
    sendTimer = setTimeout(() => {
      sendTimer = 0;
      const payload = {e: state.e, gx: +state.gx.toFixed(2), gy: +state.gy.toFixed(2), blink: state.blink, p: +state.p.toFixed(2)};
      lastSent = JSON.stringify(payload);
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type: 'eyes', ...payload}));
      if (state.blink) state.blink = false;  // blink is an edge, not a level
    }, SEND_INTERVAL_MS);
  };

  select.onchange = () => { if (!live()) apply({e: select.value}); };
  window.addEventListener('mousemove', ({clientX, clientY}) => {
    if (live()) return;
    apply({gx: clamp((clientX / innerWidth - 0.5) * 2, -1, 1), gy: clamp((clientY / innerHeight - 0.5) * 2, -1, 1)});
  });
  window.addEventListener('keydown', ({key}) => {
    const k = key.toLowerCase();
    if (k === 'b') { for (const eye of eyes) eye.blink(performance.now()); apply({blink: true}); state.blink = false; }
    else if (k === 'h') { const love = state.e !== 'love'; if (love) beforeLove = state.e; select.value = love ? 'love' : beforeLove; apply({e: select.value}); }
    else if (k === 'f') document.body.classList.toggle('hide-bar');
  });

  const connect = () => {
    try { ws = new WebSocket(`ws://${location.host}/ws`); } catch { return; }
    ws.onmessage = ({data}) => {
      let message; try { message = JSON.parse(data); } catch { return; }
      if (message.type !== 'eyes' || !message.state) return;
      const incoming = JSON.stringify(message.state);
      const echoed = firstMessage || incoming === lastSent;
      firstMessage = false;
      if (!echoed) { liveUntil = performance.now() + LIVE_HOLD_MS; select.value = message.state.e; }
      apply(message.state, {fromServer: true});
    };
    ws.onerror = () => {};  // 1013 "one controller at a time" and refused sockets both land here; demo mode carries on
    ws.onclose = () => { ws = null; firstMessage = true; setTimeout(connect, 5000); };
  };
  connect();

  const frame = () => {
    const now = performance.now();
    badge.textContent = live() ? 'live' : ws?.readyState === WebSocket.OPEN ? 'demo · echo' : 'demo';
    badge.classList.toggle('live', live());
    for (const eye of eyes) eye.step(now);
    requestAnimationFrame(frame);
  };
  apply({e: 'neutral'}); frame();
}
