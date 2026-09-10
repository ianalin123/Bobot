// Bob eye firmware for the Waveshare ESP32-S3-Touch-LCD-2.1 (ST7701 480x480 round IPS, 16-bit RGB
// parallel interface). Pin map, RGB timings and the ST7701 init table come from the Waveshare wiki
// and the ESP32_Display_Panel board file BOARD_WAVESHARE_ESP32_S3_TOUCH_LCD_2_1; see README.md.
#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <Preferences.h>
#include <Wire.h>

#include "eye.h"
#include "protocol.h"

namespace {

// I2C bus shared by the TCA9554 expander, CST820 touch, QMI8658 IMU and PCF85063 RTC.
constexpr int PIN_I2C_SDA = 15, PIN_I2C_SCL = 7;
constexpr int PIN_BACKLIGHT = 6;
constexpr int BACKLIGHT_FREQ_HZ = 20000, BACKLIGHT_BITS = 10, BACKLIGHT_DEFAULT = 800;

// TCA9554 I/O expander at 0x20. Wiki names are 1-based (EXIO1..8), bits here are 0-based.
constexpr uint8_t TCA9554_ADDR = 0x20, TCA_REG_OUTPUT = 0x01, TCA_REG_CONFIG = 0x03;
constexpr uint8_t EXP_LCD_RST = 1 << 0;  // EXIO1
constexpr uint8_t EXP_TP_RST = 1 << 1;   // EXIO2
constexpr uint8_t EXP_LCD_CS = 1 << 2;   // EXIO3: chip select of the 3-wire SPI used only for init
constexpr uint8_t EXP_SD_CS = 1 << 3;    // EXIO4: the TF card shares GPIO1/GPIO2 with LCD SDA/SCL, keep it deselected
constexpr uint8_t EXP_OUTPUTS = EXP_LCD_RST | EXP_TP_RST | EXP_LCD_CS | EXP_SD_CS;

// 3-wire (9-bit) SPI for the ST7701 command interface: SCL=GPIO2, SDA=GPIO1, CS on the expander.
constexpr int PIN_LCD_SCL = 2, PIN_LCD_SDA = 1;

// RGB panel timings: 16 MHz pixel clock, HPW 8 / HBP 10 / HFP 50, VPW 3 / VBP 8 / VFP 8.
constexpr int32_t RGB_PCLK_HZ = 16000000;
constexpr size_t RGB_BOUNCE_PX = 480 * 10;

uint8_t expanderOut = 0xFF;

void tcaWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(TCA9554_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

void expanderSet(uint8_t mask, bool high) {
  expanderOut = high ? (expanderOut | mask) : (expanderOut & ~mask);
  tcaWrite(TCA_REG_OUTPUT, expanderOut);
}

// Software SPI on two GPIOs, chip select driven through the TCA9554 (Arduino_XCA9554SWSPI puts all
// four lines on the expander, which is not how this board is wired).
class ExpanderCsSWSPI : public Arduino_SWSPI {
 public:
  ExpanderCsSWSPI() : Arduino_SWSPI(GFX_NOT_DEFINED /*dc: 9-bit mode*/, GFX_NOT_DEFINED /*cs*/, PIN_LCD_SCL, PIN_LCD_SDA) {}
  void beginWrite() override {
    expanderSet(EXP_LCD_CS, false);
    Arduino_SWSPI::beginWrite();
  }
  void endWrite() override {
    Arduino_SWSPI::endWrite();
    expanderSet(EXP_LCD_CS, true);
  }
};

// ST7701 init sequence copied from ESP32_Display_Panel's Waveshare 2.1" board file
// (ESP_PANEL_BOARD_LCD_VENDOR_INIT_CMD), encoded as Arduino_GFX batch operations.
const uint8_t st7701_waveshare_21_init[] = {
    BEGIN_WRITE,
    WRITE_COMMAND_8, 0xFF, WRITE_BYTES, 5, 0x77, 0x01, 0x00, 0x00, 0x10,
    WRITE_C8_D16, 0xC0, 0x3B, 0x00,
    WRITE_C8_D16, 0xC1, 0x0B, 0x02,
    WRITE_C8_D16, 0xC2, 0x07, 0x02,
    WRITE_C8_D8, 0xCC, 0x10,
    WRITE_C8_D8, 0xCD, 0x08,
    WRITE_COMMAND_8, 0xB0, WRITE_BYTES, 16,
    0x00, 0x11, 0x16, 0x0E, 0x11, 0x06, 0x05, 0x09, 0x08, 0x21, 0x06, 0x13, 0x10, 0x29, 0x31, 0x18,
    WRITE_COMMAND_8, 0xB1, WRITE_BYTES, 16,
    0x00, 0x11, 0x16, 0x0E, 0x11, 0x07, 0x05, 0x09, 0x09, 0x21, 0x05, 0x13, 0x11, 0x2A, 0x31, 0x18,
    WRITE_COMMAND_8, 0xFF, WRITE_BYTES, 5, 0x77, 0x01, 0x00, 0x00, 0x11,
    WRITE_C8_D8, 0xB0, 0x6D,
    WRITE_C8_D8, 0xB1, 0x37,
    WRITE_C8_D8, 0xB2, 0x81,
    WRITE_C8_D8, 0xB3, 0x80,
    WRITE_C8_D8, 0xB5, 0x43,
    WRITE_C8_D8, 0xB7, 0x85,
    WRITE_C8_D8, 0xB8, 0x20,
    WRITE_C8_D8, 0xC1, 0x78,
    WRITE_C8_D8, 0xC2, 0x78,
    WRITE_C8_D8, 0xD0, 0x88,
    WRITE_COMMAND_8, 0xE0, WRITE_BYTES, 3, 0x00, 0x00, 0x02,
    WRITE_COMMAND_8, 0xE1, WRITE_BYTES, 11, 0x03, 0xA0, 0x00, 0x00, 0x04, 0xA0, 0x00, 0x00, 0x00, 0x20, 0x20,
    WRITE_COMMAND_8, 0xE2, WRITE_BYTES, 13,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    WRITE_COMMAND_8, 0xE3, WRITE_BYTES, 4, 0x00, 0x00, 0x11, 0x00,
    WRITE_C8_D16, 0xE4, 0x22, 0x00,
    WRITE_COMMAND_8, 0xE5, WRITE_BYTES, 16,
    0x05, 0xEC, 0xA0, 0xA0, 0x07, 0xEE, 0xA0, 0xA0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    WRITE_COMMAND_8, 0xE6, WRITE_BYTES, 4, 0x00, 0x00, 0x11, 0x00,
    WRITE_C8_D16, 0xE7, 0x22, 0x00,
    WRITE_COMMAND_8, 0xE8, WRITE_BYTES, 16,
    0x06, 0xED, 0xA0, 0xA0, 0x08, 0xEF, 0xA0, 0xA0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    WRITE_COMMAND_8, 0xEB, WRITE_BYTES, 7, 0x00, 0x00, 0x40, 0x40, 0x00, 0x00, 0x00,
    WRITE_COMMAND_8, 0xED, WRITE_BYTES, 16,
    0xFF, 0xFF, 0xFF, 0xBA, 0x0A, 0xBF, 0x45, 0xFF, 0xFF, 0x54, 0xFB, 0xA0, 0xAB, 0xFF, 0xFF, 0xFF,
    WRITE_COMMAND_8, 0xEF, WRITE_BYTES, 6, 0x10, 0x0D, 0x04, 0x08, 0x3F, 0x1F,
    WRITE_COMMAND_8, 0xFF, WRITE_BYTES, 5, 0x77, 0x01, 0x00, 0x00, 0x13,
    WRITE_C8_D8, 0xEF, 0x08,
    WRITE_COMMAND_8, 0xFF, WRITE_BYTES, 5, 0x77, 0x01, 0x00, 0x00, 0x00,
    WRITE_C8_D8, 0x36, 0x00,  // MADCTL
    WRITE_C8_D8, 0x3A, 0x66,  // COLMOD 18-bit (Waveshare's value; the ESP32 still feeds RGB565)
    WRITE_COMMAND_8, 0x11,    // sleep out, 480 ms
    DELAY, 240, DELAY, 240,
    WRITE_COMMAND_8, 0x20,    // inversion off
    DELAY, 120,
    WRITE_COMMAND_8, 0x29,    // display on
    END_WRITE};

Arduino_DataBus *bus = new ExpanderCsSWSPI();
Arduino_ESP32RGBPanel *rgbpanel = new Arduino_ESP32RGBPanel(
    40 /* DE */, 39 /* VSYNC */, 38 /* HSYNC */, 41 /* PCLK */,
    46 /* R0 */, 3 /* R1 */, 8 /* R2 */, 18 /* R3 */, 17 /* R4 */,
    14 /* G0 */, 13 /* G1 */, 12 /* G2 */, 11 /* G3 */, 10 /* G4 */, 9 /* G5 */,
    5 /* B0 */, 45 /* B1 */, 48 /* B2 */, 47 /* B3 */, 21 /* B4 */,
    1 /* hsync_polarity */, 50 /* hsync_front_porch */, 8 /* hsync_pulse_width */, 10 /* hsync_back_porch */,
    1 /* vsync_polarity */, 8 /* vsync_front_porch */, 3 /* vsync_pulse_width */, 8 /* vsync_back_porch */,
    0 /* pclk_active_neg */, RGB_PCLK_HZ /* prefer_speed */, false /* useBigEndian */,
    0 /* de_idle_high */, 0 /* pclk_idle_high */, RGB_BOUNCE_PX /* bounce_buffer_size_px */);
Arduino_RGB_Display *gfx = new Arduino_RGB_Display(
    EYE_SIZE, EYE_SIZE, rgbpanel, 0 /* rotation */, true /* auto_flush */,
    bus, GFX_NOT_DEFINED /* RST via expander */, st7701_waveshare_21_init, sizeof(st7701_waveshare_21_init));
// The RGB display owns the DMA framebuffer the panel scans continuously; the eye is drawn into this
// second PSRAM buffer and copied over per frame so the panel never shows a half-drawn eye.
PsramCanvas *canvas = new PsramCanvas(EYE_SIZE, EYE_SIZE, gfx);
Preferences prefs;
Eye *eye = nullptr;
Protocol *proto = nullptr;
bool displayOk = false;

// TCA9554 bring-up: LCD_RST/TP_RST/LCD_CS/SD_CS as outputs, LCD held in reset for 10 ms, then 100 ms
// for the ST7701 to come up (mirrors ESP_PANEL_BOARD_LCD_PRE_BEGIN_FUNCTION).
void expanderInit() {
  expanderOut = EXP_TP_RST | EXP_LCD_CS | EXP_SD_CS;  // LCD_RST low, everything else deselected/released
  tcaWrite(TCA_REG_OUTPUT, expanderOut);
  tcaWrite(TCA_REG_CONFIG, static_cast<uint8_t>(~EXP_OUTPUTS));
  delay(10);
  expanderSet(EXP_LCD_RST, true);
  delay(100);
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

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  expanderInit();

  ledcAttach(PIN_BACKLIGHT, BACKLIGHT_FREQ_HZ, BACKLIGHT_BITS);
  ledcWrite(PIN_BACKLIGHT, 0);  // stay dark until the first frame is on the panel

  displayOk = canvas->begin(GFX_NOT_DEFINED);  // SPI init table, then RGB peripheral at RGB_PCLK_HZ
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
