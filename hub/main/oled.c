#include "oled.h"
#include <string.h>
#include "driver/i2c_master.h"
#include "font.h"

#define OLED_SDA  8
#define OLED_SCL  9
#define OLED_ADDR 0x3C

static i2c_master_dev_handle_t dev;
static bool ok;
static uint8_t frame[1 + OLED_W * OLED_H / 8];  // [0] — I2C control byte "data"
static uint8_t *fb = frame + 1;

static bool send_cmds(const uint8_t *cmds, size_t n)
{
    uint8_t buf[32];
    buf[0] = 0x00;  // control byte: commands
    memcpy(buf + 1, cmds, n);
    return i2c_master_transmit(dev, buf, n + 1, 50) == ESP_OK;
}

bool oled_init(void)
{
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = OLED_SDA,
        .scl_io_num = OLED_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    if (i2c_new_master_bus(&bus_cfg, &bus) != ESP_OK) return false;
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = OLED_ADDR,
        .scl_speed_hz = 400000,
    };
    if (i2c_master_bus_add_device(bus, &dev_cfg, &dev) != ESP_OK) return false;

    // Same init as Adafruit_SSD1306 for a 128x32 panel with internal charge pump.
    static const uint8_t init[] = {
        0xAE,             // display off
        0xD5, 0x80,       // clock divide
        0xA8, 0x1F,       // multiplex: 32 rows
        0xD3, 0x00,       // display offset
        0x40,             // start line 0
        0x8D, 0x14,       // charge pump on
        0x20, 0x00,       // horizontal addressing
        0xA1,             // segment remap
        0xC8,             // COM scan direction
        0xDA, 0x02,       // COM pins for 128x32
        0x81, 0x8F,       // contrast
        0xD9, 0xF1,       // precharge
        0xDB, 0x40,       // VCOM detect
        0xA4,             // resume to RAM content
        0xA6,             // normal (not inverted)
        0xAF,             // display on
    };
    ok = send_cmds(init, sizeof(init));
    frame[0] = 0x40;  // control byte: data
    oled_clear();
    oled_flush();
    return ok;
}

void oled_clear(void)
{
    memset(fb, 0, OLED_W * OLED_H / 8);
}

void oled_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= OLED_W || y < 0 || y >= OLED_H) return;
    uint8_t bit = 1 << (y & 7);
    if (on) fb[x + (y >> 3) * OLED_W] |= bit;
    else fb[x + (y >> 3) * OLED_W] &= ~bit;
}

void oled_fill_rect(int x, int y, int w, int h, bool on)
{
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++) oled_pixel(i, j, on);
}

void oled_rect(int x, int y, int w, int h, bool on)
{
    for (int i = x; i < x + w; i++) { oled_pixel(i, y, on); oled_pixel(i, y + h - 1, on); }
    for (int j = y; j < y + h; j++) { oled_pixel(x, j, on); oled_pixel(x + w - 1, j, on); }
}

int oled_text(int x, int y, const char *s, int scale, bool on)
{
    for (; *s; s++) {
        const uint8_t *rows = font_glyph(*s);
        for (int r = 0; r < FONT_H; r++)
            for (int c = 0; c < FONT_W; c++)
                if (rows[r] & (0x10 >> c))
                    oled_fill_rect(x + c * scale, y + r * scale, scale, scale, on);
        x += (FONT_W + 1) * scale;
    }
    return x;
}

int oled_text_width(const char *s, int scale)
{
    int n = (int)strlen(s);
    return n ? (n * (FONT_W + 1) - 1) * scale : 0;
}

void oled_text_center(int y, const char *s, int scale, bool on)
{
    oled_text((OLED_W - oled_text_width(s, scale)) / 2, y, s, scale, on);
}

void oled_flush(void)
{
    if (!ok) return;
    static const uint8_t window[] = {0x21, 0, OLED_W - 1, 0x22, 0, OLED_H / 8 - 1};
    if (!send_cmds(window, sizeof(window))) return;
    i2c_master_transmit(dev, frame, sizeof(frame), 100);
}
