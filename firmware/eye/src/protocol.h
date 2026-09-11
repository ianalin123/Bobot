// JSON-lines protocol over USB CDC, matching bob/hardware/eyes.py and web/eyes-sim.
//   state:  {"e":"happy","gx":0.3,"gy":-0.1,"blink":false,"p":1.0}   (<= 20 Hz, no reply)
//   ping:   {"cmd":"ping"}              -> {"ok":1,"side":"L","fps":58,"fw":"0.3.0"}
//   side:   {"cmd":"side","value":"L"}  -> {"ok":1,"side":"L"}   (persisted in NVS by main.cpp)
//   bl:     {"cmd":"bl","value":800}    -> {"ok":1,"bl":800}     (backlight 0..1023)
//   anything else                       -> {"err":"unknown"}; unparsable line -> {"err":"json"}
// If the link goes quiet the last state is kept; nothing resets.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include <string.h>

#include "eye.h"

#define EYE_FW_VERSION "0.3.0"

class Protocol {
 public:
  using SideFn = void (*)(char side);
  using BacklightFn = void (*)(int level);

  Protocol(Stream &io, Eye &eye, SideFn onSide, BacklightFn onBacklight)
      : io_(io), eye_(eye), onSide_(onSide), onBacklight_(onBacklight) {}

  // Non-blocking: drain whatever bytes are waiting, handle each complete line.
  void poll() {
    while (io_.available() > 0) {
      int ch = io_.read();
      if (ch < 0) break;
      if (ch == '\n' || ch == '\r') {
        if (len_ > 0 && !overflow_) {
          buf_[len_] = '\0';
          handle(buf_);
        }
        len_ = 0;
        overflow_ = false;
      } else if (!overflow_) {
        if (len_ < sizeof(buf_) - 1) {
          buf_[len_++] = static_cast<char>(ch);
        } else {
          overflow_ = true;  // discard the rest of this line
          len_ = 0;
        }
      }
    }
  }

  uint32_t lastRxMs() const { return lastRx_; }

 private:
  void handle(const char *line) {
    JsonDocument doc;
    if (deserializeJson(doc, line) != DeserializationError::Ok || !doc.is<JsonObject>()) {
      io_.println(F("{\"err\":\"json\"}"));
      return;
    }
    lastRx_ = millis();
    const char *cmd = doc["cmd"];
    if (cmd) {
      handleCommand(cmd, doc);
      return;
    }
    if (doc["e"].is<const char *>() || doc["gx"].is<float>() || doc["gy"].is<float>() ||
        doc["p"].is<float>() || doc["blink"].is<bool>()) {
      // Missing keys keep their previous value so partial updates are harmless.
      const char *e = doc["e"] | expression_;
      strlcpy(expressionBuf_, e, sizeof(expressionBuf_));
      expression_ = expressionBuf_;
      gx_ = doc["gx"] | gx_;
      gy_ = doc["gy"] | gy_;
      pupil_ = doc["p"] | pupil_;
      bool blink = doc["blink"] | false;
      eye_.setState(expression_, gx_, gy_, pupil_, blink);  // unknown names fall back to neutral
      return;
    }
    io_.println(F("{\"err\":\"unknown\"}"));
  }

  void handleCommand(const char *cmd, JsonDocument &doc) {
    if (strcmp(cmd, "ping") == 0) {
      io_.printf("{\"ok\":1,\"side\":\"%c\",\"fps\":%d,\"fw\":\"%s\"}\n", eye_.side(),
                 static_cast<int>(lroundf(eye_.fps())), EYE_FW_VERSION);
    } else if (strcmp(cmd, "side") == 0) {
      const char *v = doc["value"];
      if (v && (v[0] == 'L' || v[0] == 'R') && v[1] == '\0') {
        if (onSide_) onSide_(v[0]);
        io_.printf("{\"ok\":1,\"side\":\"%c\"}\n", v[0]);
      } else {
        io_.println(F("{\"err\":\"side\"}"));
      }
    } else if (strcmp(cmd, "bl") == 0) {
      int v = doc["value"] | -1;
      if (v < 0 || v > 1023) {
        io_.println(F("{\"err\":\"bl\"}"));
      } else {
        if (onBacklight_) onBacklight_(v);
        io_.printf("{\"ok\":1,\"bl\":%d}\n", v);
      }
    } else {
      io_.println(F("{\"err\":\"unknown\"}"));
    }
  }

  Stream &io_;
  Eye &eye_;
  SideFn onSide_;
  BacklightFn onBacklight_;
  char buf_[256];
  size_t len_ = 0;
  bool overflow_ = false;
  uint32_t lastRx_ = 0;
  char expressionBuf_[24] = "neutral";
  const char *expression_ = expressionBuf_;
  float gx_ = 0.f, gy_ = 0.f, pupil_ = 1.f;
};
