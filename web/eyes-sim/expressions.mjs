// Expression geometry shared by the browser sim and the ESP32 firmware (firmware copies these numbers).
// upperLid / lowerLid: fraction of the eye height covered by that lid (0 = fully open, 1 = closed).
// irisScale / pupilScale: multipliers on the neutral iris (r=62 px) and pupil (r=28 px) at 360 px.
// pupilShape: 'round' | 'heart'.  tilt: upper-lid rotation in degrees, mirrored per eye
// (positive = inner corner raised, i.e. sad; negative = outer corner raised, i.e. angry brows).
// lidAsym: extra upper-lid opening on the RIGHT eye only, for the one-eye-wider "curious" look.
// blinkRateMs: mean interval between auto-blinks (the sim jitters it by +/-30 %).

export const EXPRESSION_NAMES = [
  'neutral', 'curious', 'happy', 'love', 'sleepy', 'surprised', 'sad', 'angry_playful',
];

export const EXPRESSIONS = {
  neutral:       {upperLid: 0.12, lowerLid: 0.05, irisScale: 1.00, pupilScale: 1.00, pupilShape: 'round', tilt:   0, lidAsym: 0.00, blinkRateMs: 4500},
  curious:       {upperLid: 0.04, lowerLid: 0.06, irisScale: 1.08, pupilScale: 1.15, pupilShape: 'round', tilt:  -5, lidAsym: 0.14, blinkRateMs: 4000},
  happy:         {upperLid: 0.18, lowerLid: 0.40, irisScale: 1.00, pupilScale: 1.00, pupilShape: 'round', tilt:   0, lidAsym: 0.00, blinkRateMs: 3500},
  love:          {upperLid: 0.10, lowerLid: 0.22, irisScale: 1.10, pupilScale: 1.35, pupilShape: 'heart', tilt:   4, lidAsym: 0.00, blinkRateMs: 4000},
  sleepy:        {upperLid: 0.55, lowerLid: 0.14, irisScale: 1.00, pupilScale: 0.90, pupilShape: 'round', tilt:   3, lidAsym: 0.00, blinkRateMs: 7000},
  surprised:     {upperLid: 0.00, lowerLid: 0.00, irisScale: 0.92, pupilScale: 0.70, pupilShape: 'round', tilt:   0, lidAsym: 0.00, blinkRateMs: 9000},
  sad:           {upperLid: 0.34, lowerLid: 0.10, irisScale: 1.00, pupilScale: 1.05, pupilShape: 'round', tilt:  16, lidAsym: 0.00, blinkRateMs: 5000},
  angry_playful: {upperLid: 0.40, lowerLid: 0.16, irisScale: 1.00, pupilScale: 0.95, pupilShape: 'round', tilt: -16, lidAsym: 0.00, blinkRateMs: 3000},
};

// Numeric ranges every expression must respect (checked by expressions.test.mjs; firmware clamps to the same).
export const RANGES = {
  upperLid: [0, 1], lowerLid: [0, 1], irisScale: [0.5, 1.5], pupilScale: [0.4, 2],
  tilt: [-30, 30], lidAsym: [0, 0.5], blinkRateMs: [1000, 15000],
};
export const PUPIL_SHAPES = ['round', 'heart'];
