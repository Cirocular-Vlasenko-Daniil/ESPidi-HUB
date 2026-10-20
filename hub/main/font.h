// 5x7 font: digits, upper case and a few symbols. Lower case is drawn as upper case,
// anything unknown as '?'.
#pragma once
#include <stdint.h>

#define FONT_W 5
#define FONT_H 7

// 7 row masks, bit 4 = leftmost column.
const uint8_t *font_glyph(char c);
