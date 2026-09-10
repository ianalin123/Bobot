// Bob eye firmware for the Waveshare ESP32-S3-Touch-LCD-1.85 (ST77916 360x360 QSPI).
// Pin map from the Waveshare wiki; not yet verified on hardware (see README.md).
#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <Preferences.h>
#include <Wire.h>

#include "eye.h"
#include "protocol.h"

namespace {

constexpr int PIN_I2C_SDA = 11, PIN_I2C_SCL = 10, PIN_BACKLIGHT = 5;
constexpr int BACKLIGHT_FREQ_HZ = 20000, BACKLIGHT_BITS = 10, BACKLIGHT_DEFAULT = 800;
// TCA9554 I/O expander: EXIO2 drives the LCD reset line.
constexpr uint8_t TCA9554_ADDR = 0x20, TCA_REG_OUTPUT = 0x01, TCA_REG_CONFIG = 0x03, LCD_RST_BIT = 1 << 2;
constexpr int32_t QSPI_HZ = 80000000;

// QSPI: cs=21 sck=40 d0=46 d1=45 d2=42 d3=41
Arduino_DataBus *bus = new Arduino_ESP32QSPI(21, 40, 46, 45, 42, 41, false);
Arduino_GFX *gfx = new Arduino_ST77916(bus, -1 /*rst via TCA9554*/, 0, true /*ips*/, 360, 360);
PsramCanvas *canvas = new PsramCanvas(360, 360, gfx);
Preferences prefs;
Eye *eye = nullptr;
Protocol *proto = nullptr;
bool displayOk = false;

uint8_t tcaRead(uint8_t reg) {
  Wire.beginTransmission(TCA9554_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return 0xFF;
  if (Wire.requestFrom(static_cast<uint8_t>(TCA9554_ADDR), static_cast<uint8_t>(1)) != 1) return 0xFF;
  return static_cast<uint8_t>(Wire.read());
}

void tcaWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(TCA9554_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

// Pulse LCD reset: EXIO2 as output, low 10 ms, high, then 50 ms for the panel to come up.
void lcdResetPulse() {
  uint8_t config = tcaRead(TCA_REG_CONFIG);
  tcaWrite(TCA_REG_CONFIG, config & ~LCD_RST_BIT);
  uint8_t out = tcaRead(TCA_REG_OUTPUT);
  tcaWrite(TCA_REG_OUTPUT, out & ~LCD_RST_BIT);
  delay(10);
  tcaWrite(TCA_REG_OUTPUT, out | LCD_RST_BIT);
  delay(50);
}

char loadSide() {
  prefs.begin("eye", true);
  String side = prefs.getString("side", "L");
  prefs.end();
  return side == "R" ? 'R' : 'L';
}

void storeSide(char side) {
  prefs.begin("eye", false);
  prefs.putString("side", String(side));
  prefs.end();
  if (eye) eye->setSide(side);
}

void setBacklight(int level) { ledcWrite(PIN_BACKLIGHT, level); }

}  // namespace

void setup() {
  Serial.begin(115200);  // native USB CDC; never wait for a host, the eye must run standalone

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  lcdResetPulse();

  ledcAttach(PIN_BACKLIGHT, BACKLIGHT_FREQ_HZ, BACKLIGHT_BITS);
  ledcWrite(PIN_BACKLIGHT, 0);  // stay dark until the first frame is on the panel

  displayOk = canvas->begin(QSPI_HZ);
  eye = new Eye(canvas, loadSide());
  proto = new Protocol(Serial, *eye, storeSide, setBacklight);

  if (displayOk) {
    eye->step(millis());
    ledcWrite(PIN_BACKLIGHT, BACKLIGHT_DEFAULT);
  } else {
    Serial.println(F("{\"err\":\"display\"}"));
  }
  Serial.printf("{\"boot\":1,\"side\":\"%c\"}\n", eye->side());
}

void loop() {
  proto->poll();
  if (displayOk) {
    eye->step(millis());
  } else {
    delay(10);  // still answer ping/side so the board can be identified
  }
}
