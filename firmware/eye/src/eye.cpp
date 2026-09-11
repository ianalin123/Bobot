#include "eye.h"

#include <math.h>
#include <string.h>

#include <esp_random.h>

namespace {

// Sim geometry (360 px canvas: goggle 176, sclera 124, iris 52, pupil 23, gaze 48, saccade 5)
// scaled by 480/360. The eye sits inside Bob's silver goggle rim (SCLERA_R..GOGGLE_R).
constexpr int16_t SIZE = EYE_SIZE, CENTER = SIZE / 2, GOGGLE_R = 235, SCLERA_R = 165, IRIS_R = 69, PUPIL_R = 31;
constexpr float GAZE_PX = 64.f, SACCADE_PX = 7.f, SMOOTH = 0.25f;
constexpr uint32_t BLINK_CLOSE_MS = 120, BLINK_OPEN_MS = 100;

// ---- Expression table: COPIED from web/eyes-sim/expressions.mjs. Edit there first, then here. ----
const Expression EXPRESSIONS[] = {
    {"neutral",       0.12f, 0.05f, 1.00f, 1.00f, PupilShape::Round,   0.f, 0.00f, 4500},
    {"curious",       0.04f, 0.06f, 1.08f, 1.15f, PupilShape::Round,  -5.f, 0.14f, 4000},
    {"happy",         0.18f, 0.40f, 1.00f, 1.00f, PupilShape::Round,   0.f, 0.00f, 3500},
    {"love",          0.10f, 0.22f, 1.10f, 1.35f, PupilShape::Heart,   4.f, 0.00f, 4000},
    {"sleepy",        0.55f, 0.14f, 1.00f, 0.90f, PupilShape::Round,   3.f, 0.00f, 7000},
    {"surprised",     0.00f, 0.00f, 0.92f, 0.70f, PupilShape::Round,   0.f, 0.00f, 9000},
    {"sad",           0.34f, 0.10f, 1.00f, 1.05f, PupilShape::Round,  16.f, 0.00f, 5000},
    {"angry_playful", 0.40f, 0.16f, 1.00f, 0.95f, PupilShape::Round, -16.f, 0.00f, 3000},
};

// Colours are 24-bit like the sim; converted to RGB565 at the call site.
struct IrisColours { uint32_t base, light, ring; };
constexpr IrisColours IRIS_LEFT  = {0x3F8F3A, 0x62B35A, 0x245A22};  // green
constexpr IrisColours IRIS_RIGHT = {0x6B3E1E, 0x95602F, 0x3A2010};  // brown
constexpr uint32_t SCLERA_EDGE = 0xCFCDBF, SCLERA_MID = 0xF4F1E4, SCLERA_CENTER = 0xFFFDF4;
constexpr uint32_t PUPIL = 0x0B0B0B, WHITE = 0xFFFFFF, BLACK = 0x000000;
// Minion skin for the lids (sim SKIN) and the goggle metal (sim RIM).
constexpr uint32_t SKIN_LID = 0xF5C21C, SKIN_CREASE = 0xB8850F;
constexpr uint32_t RIM_LIGHT = 0xF6F6F6, RIM_MID = 0xC3C3C3, RIM_SHADE = 0x8A8A8A, RIM_DARK = 0x4B4B4B, RIM_EDGE = 0x2A2A2A;
// Glass glare: sim ellipse at (-28,-72) radii 66x22 rotated -0.45 rad, white at 16 %, scaled by 4/3.
constexpr float GLARE_X = -37.f, GLARE_Y = -96.f, GLARE_A = 88.f, GLARE_B = 29.f, GLARE_ROT = -0.45f, GLARE_ALPHA = 0.16f;
constexpr float LIP_START = 0.82f, LIP_ALPHA = 0.42f;  // shadow under the goggle lip, sim radial gradient
constexpr float CREASE_HALF = 2.f;  // lid crease stroke: sim lineWidth 3 -> 4 px here

constexpr uint16_t rgb565(uint32_t hex) {
  return static_cast<uint16_t>((((hex >> 16) & 0xF8) << 8) | (((hex >> 8) & 0xFC) << 3) | ((hex & 0xFF) >> 3));
}

// a + (b - a) * t per channel, t in 0..1 (stands in for the sim's gradients and alpha).
uint32_t mix(uint32_t a, uint32_t b, float t) {
  uint32_t out = 0;
  for (int shift = 0; shift <= 16; shift += 8) {
    float ca = (a >> shift) & 0xFF, cb = (b >> shift) & 0xFF;
    uint32_t c = static_cast<uint32_t>(lroundf(ca + (cb - ca) * t)) & 0xFF;
    out |= c << shift;
  }
  return out;
}

// Same blend on a packed RGB565 pixel (used per pixel in the lid pass; keeps the 5/6/5 precision).
inline uint16_t mix565(uint16_t px, uint32_t towards, float t) {
  uint32_t r = (px >> 11) & 0x1F, g = (px >> 5) & 0x3F, b = px & 0x1F;
  uint32_t tr = (towards >> 19) & 0x1F, tg = (towards >> 10) & 0x3F, tb = (towards >> 3) & 0x1F;
  r = static_cast<uint32_t>(r + (static_cast<float>(tr) - r) * t);
  g = static_cast<uint32_t>(g + (static_cast<float>(tg) - g) * t);
  b = static_cast<uint32_t>(b + (static_cast<float>(tb) - b) * t);
  return static_cast<uint16_t>((r << 11) | (g << 5) | b);
}

// Brushed-metal colour around the rim: the sim's conic gradient (start angle -0.7 rad) as piecewise lerps.
uint32_t rimMetal(float angle) {
  constexpr float TAU_F = 2.f * static_cast<float>(M_PI);
  float t = fmodf(angle + 0.7f + TAU_F * 2.f, TAU_F) / TAU_F;
  constexpr float STOP_T[] = {0.f, 0.10f, 0.22f, 0.38f, 0.50f, 0.62f, 0.78f, 0.90f, 1.f};
  constexpr uint32_t STOP_C[] = {RIM_LIGHT, RIM_MID, RIM_LIGHT, RIM_SHADE, RIM_MID, RIM_LIGHT, RIM_SHADE, RIM_MID, RIM_LIGHT};
  for (int i = 1; i < 9; i++) {
    if (t <= STOP_T[i]) return mix(STOP_C[i - 1], STOP_C[i], (t - STOP_T[i - 1]) / (STOP_T[i] - STOP_T[i - 1]));
  }
  return RIM_LIGHT;
}

inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
inline float rand01() { return (esp_random() & 0xFFFF) / 65535.f; }

}  // namespace

const Expression *findExpression(const char *name) {
  if (!name) return nullptr;
  for (const Expression &e : EXPRESSIONS) {
    if (strcmp(e.name, name) == 0) return &e;
  }
  return nullptr;
}

Eye::Eye(Arduino_Canvas *canvas, char side) : canvas_(canvas), side_(side == 'R' ? 'R' : 'L') {
  expression_ = &EXPRESSIONS[0];
  cur_ = {0.12f, 0.05f, 1.f, 1.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f};
  target_ = cur_;
}

bool Eye::setState(const char *expression, float gx, float gy, float pupil, bool blinkFlag) {
  const Expression *e = findExpression(expression);
  bool known = e != nullptr;
  if (!e) e = &EXPRESSIONS[0];
  expression_ = e;
  float asym = side_ == 'R' ? e->lidAsym : 0.f;
  target_.upperLid = clampf(e->upperLid - asym, 0.f, 1.f);
  target_.lowerLid = e->lowerLid;
  target_.irisScale = e->irisScale;
  target_.pupilScale = e->pupilScale * clampf(pupil, 0.3f, 2.f);
  target_.heart = e->pupilShape == PupilShape::Heart ? 1.f : 0.f;
  target_.tilt = e->tilt;
  target_.gx = clampf(gx, -1.f, 1.f);
  target_.gy = clampf(gy, -1.f, 1.f);
  blinkRateMs_ = e->blinkRateMs;
  if (blinkFlag && !lastBlinkFlag_) blink(millis());
  lastBlinkFlag_ = blinkFlag;
  return known;
}

void Eye::blink(uint32_t now) {
  if (blinkStart_ < 0 || now - static_cast<uint32_t>(blinkStart_) > BLINK_CLOSE_MS + BLINK_OPEN_MS) {
    blinkStart_ = static_cast<int32_t>(now);
  }
}

float Eye::blinkAmount(uint32_t now) {
  if (blinkStart_ < 0) return 0.f;
  uint32_t t = now - static_cast<uint32_t>(blinkStart_);
  if (t < BLINK_CLOSE_MS) return static_cast<float>(t) / BLINK_CLOSE_MS;
  if (t < BLINK_CLOSE_MS + BLINK_OPEN_MS) return 1.f - static_cast<float>(t - BLINK_CLOSE_MS) / BLINK_OPEN_MS;
  blinkStart_ = -1;
  return 0.f;
}

void Eye::step(uint32_t now) {
  if (now >= nextBlink_) {
    if (nextBlink_ > 0) blink(now);  // the very first schedule does not blink
    nextBlink_ = now + static_cast<uint32_t>(blinkRateMs_ * (0.7f + rand01() * 0.6f));
  }
  if (now >= nextSaccade_) {
    target_.sx = (rand01() * 2.f - 1.f) * SACCADE_PX;
    target_.sy = (rand01() * 2.f - 1.f) * SACCADE_PX;
    nextSaccade_ = now + 1000 + static_cast<uint32_t>(rand01() * 2000.f);
  }
  float *c = reinterpret_cast<float *>(&cur_);
  const float *t = reinterpret_cast<const float *>(&target_);
  for (size_t i = 0; i < sizeof(Params) / sizeof(float); i++) c[i] = lerpf(c[i], t[i], SMOOTH);

  draw(blinkAmount(now));
  canvas_->flush();

  fpsFrames_++;
  uint32_t elapsed = now - fpsWindowStart_;
  if (elapsed >= 1000) {
    fps_ = fpsFrames_ * 1000.f / elapsed;
    fpsFrames_ = 0;
    fpsWindowStart_ = now;
  }
}

void Eye::draw(float blink) {
  Arduino_GFX *g = canvas_;
  const Params &cur = cur_;

  // Sclera: the sim's radial gradient (centre offset -20 px sim = -27 px here) approximated by
  // concentric discs (sim radii 109/83/40 at offsets -3/-10/-17, scaled by 4/3).
  // Everything outside SCLERA_R (the goggle rim and black) is painted in applyLidsAndClip().
  g->fillCircle(CENTER, CENTER, SCLERA_R, rgb565(SCLERA_EDGE));
  g->fillCircle(CENTER, CENTER - 4, 145, rgb565(mix(SCLERA_MID, SCLERA_EDGE, 0.35f)));
  g->fillCircle(CENTER, CENTER - 13, 110, rgb565(SCLERA_MID));
  g->fillCircle(CENTER, CENTER - 22, 53, rgb565(SCLERA_CENTER));

  // Iris: gaze offset + saccade; radial gradient light -> base -> ring as concentric discs.
  const IrisColours &col = side_ == 'R' ? IRIS_RIGHT : IRIS_LEFT;
  float ir = IRIS_R * cur.irisScale;
  int16_t cx = static_cast<int16_t>(lroundf(CENTER + cur.gx * GAZE_PX + cur.sx));
  int16_t cy = static_cast<int16_t>(lroundf(CENTER + cur.gy * GAZE_PX + cur.sy));
  g->fillCircle(cx, cy, static_cast<int16_t>(ir * 1.04f), rgb565(col.ring));  // includes the ring stroke
  g->fillCircle(cx, cy, static_cast<int16_t>(ir * 0.90f), rgb565(mix(col.base, col.ring, 0.78f)));
  g->fillCircle(cx, cy, static_cast<int16_t>(ir * 0.75f), rgb565(mix(col.base, col.ring, 0.44f)));
  g->fillCircle(cx, cy, static_cast<int16_t>(ir * 0.60f), rgb565(col.base));
  g->fillCircle(cx, cy, static_cast<int16_t>(ir * 0.45f), rgb565(mix(col.light, col.base, 0.6f)));
  g->fillCircle(cx, cy, static_cast<int16_t>(ir * 0.30f), rgb565(col.light));
  uint16_t striation = rgb565(mix(col.base, col.ring, 0.6f));  // sim: ring colour at 35 % alpha
  for (int k = 0; k < 28; k++) {
    float a = k * static_cast<float>(M_PI) / 14.f, ca = cosf(a), sa = sinf(a);
    g->drawLine(static_cast<int16_t>(lroundf(cx + ca * ir * 0.45f)), static_cast<int16_t>(lroundf(cy + sa * ir * 0.45f)),
                static_cast<int16_t>(lroundf(cx + ca * ir * 0.95f)), static_cast<int16_t>(lroundf(cy + sa * ir * 0.95f)),
                striation);
  }

  // Pupil: round, or a heart (two circles + a triangle) for "love".
  float pr = PUPIL_R * cur.pupilScale;
  if (cur.heart > 0.5f) {
    drawHeart(cx, cy, pr * 1.15f, rgb565(PUPIL));
  } else {
    g->fillCircle(cx, cy, static_cast<int16_t>(pr), rgb565(PUPIL));
  }

  // Highlights (sim: white at 92 % and 60 % alpha).
  g->fillCircle(static_cast<int16_t>(lroundf(cx - ir * 0.38f)), static_cast<int16_t>(lroundf(cy - ir * 0.4f)),
                static_cast<int16_t>(ir * 0.2f), rgb565(mix(PUPIL, WHITE, 0.92f)));
  g->fillCircle(static_cast<int16_t>(lroundf(cx + ir * 0.32f)), static_cast<int16_t>(lroundf(cy + ir * 0.36f)),
                static_cast<int16_t>(ir * 0.09f), rgb565(mix(col.base, WHITE, 0.6f)));

  applyLidsAndClip(blink);
}

void Eye::drawHeart(int16_t cx, int16_t cy, float r, uint16_t colour) {
  Arduino_GFX *g = canvas_;
  int16_t lobe = static_cast<int16_t>(r * 0.55f);
  int16_t ly = static_cast<int16_t>(lroundf(cy - r * 0.3f));
  g->fillCircle(static_cast<int16_t>(lroundf(cx - r * 0.5f)), ly, lobe, colour);
  g->fillCircle(static_cast<int16_t>(lroundf(cx + r * 0.5f)), ly, lobe, colour);
  int16_t ty = static_cast<int16_t>(lroundf(cy - r * 0.12f));
  g->fillTriangle(static_cast<int16_t>(lroundf(cx - r * 1.02f)), ty, static_cast<int16_t>(lroundf(cx + r * 1.02f)), ty,
                  cx, static_cast<int16_t>(lroundf(cy + r)), colour);
}

// Static background: black outside the goggle, brushed silver rim with bevels, highlight arcs and
// four rivets between SCLERA_R and GOGGLE_R. Rendered once into PSRAM; applyLidsAndClip() copies it.
void Eye::buildBackground() {
  bgTried_ = true;
  bg_ = static_cast<uint16_t *>(heap_caps_malloc(static_cast<size_t>(SIZE) * SIZE * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!bg_) return;  // no PSRAM: applyLidsAndClip() falls back to a flat rim
  constexpr float MID_R = (GOGGLE_R + SCLERA_R) / 2.f, RIVET_R = 9.f;
  constexpr float PI_F = static_cast<float>(M_PI);
  float rivetX[4], rivetY[4];
  for (int i = 0; i < 4; i++) {
    float a = (45.f + 90.f * i) * PI_F / 180.f;
    rivetX[i] = cosf(a) * MID_R;
    rivetY[i] = sinf(a) * MID_R;
  }
  for (int16_t y = 0; y < SIZE; y++) {
    uint16_t *row = bg_ + static_cast<int32_t>(y) * SIZE;
    float dy = y - CENTER;
    for (int16_t x = 0; x < SIZE; x++) {
      float dx = x - CENTER, r = sqrtf(dx * dx + dy * dy);
      uint32_t colour = BLACK;
      if (r <= GOGGLE_R && r > SCLERA_R) {
        float a = atan2f(dy, dx);
        colour = rimMetal(a);
        if (r >= GOGGLE_R - 3.f) colour = RIM_EDGE;                       // outer edge stroke
        else if (r <= SCLERA_R + 4.f) colour = RIM_DARK;                  // inner edge stroke
        else if (r >= GOGGLE_R - 6.5f && r <= GOGGLE_R - 4.5f && a >= -0.95f * PI_F && a <= -0.3f * PI_F)
          colour = mix(colour, WHITE, 0.55f);                            // outer highlight arc (sim 1.05pi..1.7pi)
        else if (r >= SCLERA_R + 6.f && r <= SCLERA_R + 8.f && a >= 0.1f * PI_F && a <= 0.75f * PI_F)
          colour = mix(colour, WHITE, 0.55f);                            // inner highlight arc
        for (int i = 0; i < 4; i++) {
          float ex = dx - rivetX[i], ey = dy - rivetY[i], d = sqrtf(ex * ex + ey * ey);
          if (d > RIVET_R) continue;
          float hx = ex + 3.f, hy = ey + 3.f, t = clampf(sqrtf(hx * hx + hy * hy) / RIVET_R, 0.f, 1.f);
          colour = t < 0.7f ? mix(RIM_LIGHT, RIM_SHADE, t / 0.7f) : mix(RIM_SHADE, RIM_EDGE, (t - 0.7f) / 0.3f);
          if (d > RIVET_R - 1.f) colour = RIM_EDGE;
        }
      }
      row[x] = rgb565(colour);
    }
  }
}

// Eyelids, goggle rim and lens effects, written straight into the framebuffer one scanline at a time.
// Upper lid (sim, 360 px): in a frame rotated by sign*tilt, the region above the parabola
//   y = upperEdge + 15*(1-blink) * (1 - (x/360)^2)   (quadratic curve, control point +30*(1-blink))
// Lower lid: unrotated, region below  y = lowerEdge - 18*lower * (1 - (x/360)^2).
// Here the sags are 20 / 24 px (15 / 18 scaled by 4/3) and the parabola spans SIZE.
// Lids are Minion-yellow skin with a darker crease along the edge. Outside the SCLERA_R disc the
// pixels come from the goggle template (bg_). Then the goggle-lip shadow and the glare ellipse.
void Eye::applyLidsAndClip(float blink) {
  uint16_t *fb = canvas_->getFramebuffer();
  if (!fb) return;
  if (!bgTried_) buildBackground();
  const Params &cur = cur_;
  float upper = fmaxf(cur.upperLid, blink), lower = fmaxf(cur.lowerLid, blink * 0.35f);
  float sign = side_ == 'L' ? -1.f : 1.f;
  float theta = sign * cur.tilt * static_cast<float>(M_PI) / 180.f;
  float c = cosf(theta), s = sinf(theta);
  float upperEdge = -SCLERA_R + upper * SCLERA_R * 2.f, upperSag = 20.f * (1.f - blink);
  float lowerEdge = SCLERA_R - lower * SCLERA_R * 2.f, lowerSag = 24.f * lower;
  constexpr float INV = 1.f / (static_cast<float>(SIZE) * SIZE);
  constexpr float LIP_R = SCLERA_R * LIP_START, LIP_R2 = LIP_R * LIP_R, LIP_SPAN = SCLERA_R - LIP_R;
  const float gc = cosf(GLARE_ROT), gs = sinf(GLARE_ROT);
  constexpr float GLARE_YSPAN = 48.f, GLARE_XSPAN = 82.f;  // bounding box of the rotated ellipse
  const uint16_t lidPx = rgb565(SKIN_LID), creasePx = rgb565(SKIN_CREASE), rimFlat = rgb565(RIM_MID);
  const size_t rowBytes = static_cast<size_t>(SIZE) * 2;

  for (int16_t y = 0; y < SIZE; y++) {
    uint16_t *row = fb + static_cast<int32_t>(y) * SIZE;
    const uint16_t *bgRow = bg_ ? bg_ + static_cast<int32_t>(y) * SIZE : nullptr;
    float dy = y - CENTER;
    if (fabsf(dy) > SCLERA_R) {
      if (bgRow) {
        memcpy(row, bgRow, rowBytes);
      } else {
        memset(row, 0, rowBytes);
        if (fabsf(dy) <= GOGGLE_R) {
          int16_t gh = static_cast<int16_t>(sqrtf(static_cast<float>(GOGGLE_R) * GOGGLE_R - dy * dy));
          for (int16_t x = CENTER - gh; x <= CENTER + gh; x++) row[x] = rimFlat;
        }
      }
      continue;
    }
    int16_t half = static_cast<int16_t>(sqrtf(static_cast<float>(SCLERA_R) * SCLERA_R - dy * dy));
    int16_t x0 = CENTER - half, x1 = CENTER + half;
    if (bgRow) {
      memcpy(row, bgRow, static_cast<size_t>(x0) * 2);
      memcpy(row + x1 + 1, bgRow + x1 + 1, static_cast<size_t>(SIZE - 1 - x1) * 2);
    } else {
      int16_t gh = static_cast<int16_t>(sqrtf(static_cast<float>(GOGGLE_R) * GOGGLE_R - dy * dy));
      for (int16_t x = 0; x < x0; x++) row[x] = x >= CENTER - gh ? rimFlat : 0;
      for (int16_t x = x1 + 1; x < SIZE; x++) row[x] = x <= CENTER + gh ? rimFlat : 0;
    }

    const bool glareRow = fabsf(dy - GLARE_Y) <= GLARE_YSPAN;
    // Rotate the chord's first pixel into the upper-lid frame, then walk it incrementally.
    float px = x0 - CENTER;
    float qx = px * c + dy * s, qy = -px * s + dy * c;
    for (int16_t x = x0; x <= x1; x++, px += 1.f, qx += c, qy -= s) {
      float ue = upperEdge + upperSag * (1.f - qx * qx * INV);
      float le = lowerEdge - lowerSag * (1.f - px * px * INV);
      if (qy < ue - CREASE_HALF || dy > le + CREASE_HALF) {
        row[x] = lidPx;
      } else if (qy <= ue + CREASE_HALF || dy >= le - CREASE_HALF) {
        row[x] = creasePx;
      }
      float r2 = px * px + dy * dy;
      if (r2 > LIP_R2) {  // shadow under the goggle lip: 0 at 0.82 R -> LIP_ALPHA at R
        float t = (sqrtf(r2) - LIP_R) / LIP_SPAN;
        row[x] = mix565(row[x], BLACK, LIP_ALPHA * clampf(t, 0.f, 1.f));
      }
      if (glareRow && fabsf(px - GLARE_X) <= GLARE_XSPAN) {
        float ex = px - GLARE_X, ey = dy - GLARE_Y;
        float u = (ex * gc + ey * gs) / GLARE_A, v = (-ex * gs + ey * gc) / GLARE_B;
        if (u * u + v * v <= 1.f) row[x] = mix565(row[x], WHITE, GLARE_ALPHA);
      }
    }
  }
}
