// SSD1306 128x32 over I2C (ESPidi: SDA GPIO8, SCL GPIO9, address 0x3C).
// Draws into a 512-byte framebuffer; oled_flush() sends it to the display.
// If the display is missing, every call is a no-op and the HUB still works.
#pragma once
#include <stdbool.h>

#define OLED_W 128
#define OLED_H 32

bool oled_init(void);
void oled_clear(void);
void oled_pixel(int x, int y, bool on);
void oled_fill_rect(int x, int y, int w, int h, bool on);
void oled_rect(int x, int y, int w, int h, bool on);
int  oled_text(int x, int y, const char *s, int scale, bool on);  // returns x after the text
int  oled_text_width(const char *s, int scale);
void oled_text_center(int y, const char *s, int scale, bool on);
void oled_flush(void);
