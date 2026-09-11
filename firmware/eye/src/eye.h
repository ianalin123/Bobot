// Procedural eye renderer for the 480x480 round LCD. Geometry, colours and the expression
// table mirror web/eyes-sim/eyes.mjs + expressions.mjs (which are 360x360; every pixel value
// here is the sim's value scaled by 480/360) so the LCD matches the browser twin.
#pragma once

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <esp_heap_caps.h>

constexpr int16_t EYE_SIZE = 480;

enum class PupilShape : uint8_t { Round, Heart };

struct Expression {
  const char *name;
  float upperLid;      // fraction of the eye height covered by the upper lid (0 open .. 1 closed)
  float lowerLid;      // same for the lower lid
  float irisScale;     // multiplier on IRIS_R (69 px; 52 in the sim)
  float pupilScale;    // multiplier on PUPIL_R (31 px; 23 in the sim)
  PupilShape pupilShape;
  float tilt;          // upper-lid rotation in degrees, mirrored per eye (+ = inner corner up = sad)
  float lidAsym;       // extra upper-lid opening on the RIGHT eye only ("curious")
  uint16_t blinkRateMs;  // mean auto-blink interval, jittered +/-30 %
};

// Returns nullptr for an unknown name.
const Expression *findExpression(const char *name);

// Arduino_Canvas whose 16-bit framebuffer (480*480*2 = 460 800 bytes) lives in PSRAM.
class PsramCanvas : public Arduino_Canvas {
 public:
  using Arduino_Canvas::Arduino_Canvas;
  bool begin(int32_t speed = GFX_NOT_DEFINED) override {
    if (!_framebuffer) {
      _framebuffer = static_cast<uint16_t *>(heap_caps_aligned_alloc(
          16, static_cast<size_t>(_width) * _height * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
      // If PSRAM is missing Arduino_Canvas::begin falls back to its own allocation.
    }
    return Arduino_Canvas::begin(speed);
  }
};

class Eye {
 public:
  Eye(Arduino_Canvas *canvas, char side);

  void setSide(char side) { side_ = (side == 'R') ? 'R' : 'L'; }
  char side() const { return side_; }

  // New target state from the Jetson. blinkFlag is treated as an edge (true after false = one blink),
  // exactly like the sim. Returns false when the expression name is unknown (neutral is used).
  bool setState(const char *expression, float gx, float gy, float pupil, bool blinkFlag);
  void blink(uint32_t now);

  // Advance animation (smoothing, auto-blink, saccades), draw, flush to the panel.
  void step(uint32_t now);
  float fps() const { return fps_; }
  const char *expressionName() const { return expression_->name; }

 private:
  struct Params {
    float upperLid, lowerLid, irisScale, pupilScale, heart, tilt, gx, gy, sx, sy;
  };

  float blinkAmount(uint32_t now);
  void draw(float blink);
  void drawHeart(int16_t cx, int16_t cy, float r, uint16_t colour);
  void applyLidsAndClip(float blink);
  void buildBackground();

  Arduino_Canvas *canvas_;
  uint16_t *bg_ = nullptr;  // static goggle rim + black surround, rendered once (PSRAM)
  bool bgTried_ = false;
  char side_;
  const Expression *expression_;
  Params cur_, target_;
  int32_t blinkStart_ = -1;
  uint32_t nextBlink_ = 0, nextSaccade_ = 0;
  uint16_t blinkRateMs_ = 4500;
  bool lastBlinkFlag_ = false;
  uint32_t fpsWindowStart_ = 0;
  uint16_t fpsFrames_ = 0;
  float fps_ = 0;
};
