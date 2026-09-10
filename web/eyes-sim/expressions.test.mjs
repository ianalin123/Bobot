import test from 'node:test';
import assert from 'node:assert/strict';
import {EXPRESSIONS, EXPRESSION_NAMES, RANGES, PUPIL_SHAPES} from './expressions.mjs';

const SPEC = ['neutral', 'curious', 'happy', 'love', 'sleepy', 'surprised', 'sad', 'angry_playful'];

test('all 8 spec expressions exist and nothing else', () => {
  assert.deepEqual(EXPRESSION_NAMES, SPEC);
  assert.deepEqual(Object.keys(EXPRESSIONS).sort(), [...SPEC].sort());
});

test('every expression has every geometry field in range', () => {
  for (const name of SPEC) {
    const e = EXPRESSIONS[name];
    assert.ok(e, `missing ${name}`);
    for (const [field, [low, high]] of Object.entries(RANGES)) {
      assert.equal(typeof e[field], 'number', `${name}.${field} must be a number`);
      assert.ok(Number.isFinite(e[field]), `${name}.${field} must be finite`);
      assert.ok(e[field] >= low && e[field] <= high, `${name}.${field}=${e[field]} outside [${low}, ${high}]`);
    }
    assert.ok(PUPIL_SHAPES.includes(e.pupilShape), `${name}.pupilShape=${e.pupilShape}`);
    assert.ok(e.upperLid + e.lowerLid < 1, `${name} lids would fully close the eye`);
  }
});

test('mood cues from the spec: love is heart-shaped, sleepy droops, happy lifts the lower lid', () => {
  assert.equal(EXPRESSIONS.love.pupilShape, 'heart');
  assert.ok(EXPRESSIONS.sleepy.upperLid > EXPRESSIONS.neutral.upperLid + 0.3);
  assert.ok(EXPRESSIONS.happy.lowerLid > EXPRESSIONS.neutral.lowerLid + 0.2);
  assert.ok(EXPRESSIONS.curious.lidAsym > 0, 'curious opens one eye wider');
  assert.ok(EXPRESSIONS.surprised.pupilScale < 1 && EXPRESSIONS.love.pupilScale > 1);
  assert.ok(EXPRESSIONS.sad.tilt > 0 && EXPRESSIONS.angry_playful.tilt < 0);
});
