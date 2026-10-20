// The ESPidi board at the bottom of the page, drawn pixel by pixel like the OLED previews: a schematic
// of the real board (github.com/EugeneCarlo/ESPidi) — holes, traces, silkscreen, the OLED with the HUB
// splash, the icon, the encoder and the three keys.
import { GLYPHS } from "./font.js";

export const BOARD_W = 288;
export const BOARD_H = 176;

// 3x5 letters for the small silkscreen; rows top to bottom, bit 2 = left column.
const SMALL = {
  " ": [0, 0, 0, 0, 0], "-": [0, 0, 7, 0, 0], "=": [0, 7, 0, 7, 0], "2": [6, 1, 2, 4, 7], "3": [6, 1, 2, 1, 6],
  A: [2, 5, 7, 5, 5], B: [6, 5, 6, 5, 6], C: [3, 4, 4, 4, 3], D: [6, 5, 5, 5, 6], E: [7, 4, 6, 4, 7],
  G: [3, 4, 5, 5, 3], I: [7, 2, 2, 2, 7], L: [4, 4, 4, 4, 7], M: [5, 7, 7, 5, 5], N: [6, 5, 5, 5, 5],
  P: [6, 5, 6, 4, 4], R: [6, 5, 6, 5, 5], S: [3, 4, 2, 1, 6], T: [7, 2, 2, 2, 2], U: [5, 5, 5, 5, 7],
  V: [5, 5, 5, 5, 2], Y: [5, 5, 2, 2, 2],
};

const OLED = { x: 46, y: 71, w: 92, h: 32 };
const BAR = { x: 54, y: 87, w: 80 };   // the splash progress line, full: the HUB is about to start a slot
const KNOB = [243, 85];
const KEYS = [["PLAY", 32], ["TAP", 125], ["PAGE", 217]];
const KEY_Y = 122, KEY = 39;

// The icon engraved on the board, traced from a photo by tools/trace_board_icon.py: 135x154 bits
// at (102, 7), row by row, MSB first.
const ICON = {
  x: 102, y: 7, w: 135, h: 154,
  bits:
    "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" +
    "AAAAAAAAAA==",
};

function picture() {
  const px = new Uint8Array(BOARD_W * BOARD_H);
  const set = (x, y, v = 1) => {
    x = Math.round(x); y = Math.round(y);
    if (x >= 0 && y >= 0 && x < BOARD_W && y < BOARD_H) px[y * BOARD_W + x] = v;
  };
  const line = (x0, y0, x1, y1, v = 1) => {   // Bresenham
    [x0, y0, x1, y1] = [x0, y0, x1, y1].map(Math.round);
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    for (let err = dx + dy; ;) {
      set(x0, y0, v);
      if (x0 === x1 && y0 === y1) return;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  };
  const path = (pts, v = 1) => { for (let i = 1; i < pts.length; i++) line(...pts[i - 1], ...pts[i], v); };
  const circle = (cx, cy, r, v = 1, dotted = false) => {   // midpoint
    for (let x = r, y = 0, err = 1 - r; x >= y;) {
      for (const [a, b] of [[x, y], [y, x], [-y, x], [-x, y], [-x, -y], [-y, -x], [y, -x], [x, -y]]) {
        if (!dotted || (a + b) % 2 === 0) set(cx + a, cy + b, v);
      }
      y++;
      if (err < 0) err += 2 * y + 1;
      else { x--; err += 2 * (y - x) + 1; }
    }
  };
  const disc = (cx, cy, r, v = 1) => {
    for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++) if (x * x + y * y <= r * r + r) set(cx + x, cy + y, v);
  };
  const arc = (cx, cy, rx, ry, a0, a1, v = 1) => {   // degrees, 0 = right, 90 = down
    const n = Math.ceil((Math.abs(a1 - a0) / 180) * Math.PI * Math.max(rx, ry));
    const at = (i) => { const a = ((a0 + ((a1 - a0) * i) / n) * Math.PI) / 180; return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; };
    for (let i = 1; i <= n; i++) line(...at(i - 1), ...at(i), v);
  };
  const ray = (cx, cy, deg, r0, r1) => {
    const a = (deg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
    line(cx + r0 * c, cy + r0 * s, cx + r1 * c, cy + r1 * s);
  };
  const fill = (x, y, w, h, v = 1) => { for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) set(i, j, v); };
  const box = (x, y, w, h, r = 0) => {
    line(x + r, y, x + w - 1 - r, y); line(x + r, y + h - 1, x + w - 1 - r, y + h - 1);
    line(x, y + r, x, y + h - 1 - r); line(x + w - 1, y + r, x + w - 1, y + h - 1 - r);
    if (!r) return;
    arc(x + r, y + r, r, r, 180, 270); arc(x + w - 1 - r, y + r, r, r, 270, 360);
    arc(x + w - 1 - r, y + h - 1 - r, r, r, 0, 90); arc(x + r, y + h - 1 - r, r, r, 90, 180);
  };
  const text5 = (x, y, s) => {
    for (const ch of s) {
      (GLYPHS[ch] || GLYPHS["?"]).forEach((m, j) => { for (let i = 0; i < 5; i++) if (m & (0x10 >> i)) set(x + i, y + j); });
      x += 6;
    }
  };
  const text3 = (x, y, s) => {
    for (const ch of s) {
      (SMALL[ch] || SMALL[" "]).forEach((m, j) => { for (let i = 0; i < 3; i++) if (m & (4 >> i)) set(x + i, y + j); });
      x += 4;
    }
  };
  const label = (x, y, s, small) => {
    const w = small ? s.length * 4 - 1 : s.length * 6 - 1, h = small ? 5 : 7;
    fill(x - 2, y - 2, w + 4, h + 4, 0);
    (small ? text3 : text5)(x, y, s);
  };

  // Mounting holes, traces from the top edge down the left side, silkscreen.
  for (const [x, y] of [[14, 14], [273, 14], [14, 161], [273, 161]]) { circle(x, y, 6); circle(x, y, 3); }
  const ends = [118, 106, 98, 88, 78], bottoms = [118, 110, 102, 94, 86];
  for (let i = 0; i < 5; i++) {
    const y = 9 + 3 * i, x = 11 + 3 * i;
    path([[ends[i], y], [31 + 3 * i, y], [x, 29 + 3 * i], [x, bottoms[i]]]);
    box(ends[i] + 1, y - 1, 3, 3);
    box(x - 1, bottoms[i] + 1, 3, 3);
  }
  text3(42, 28, "MIDI RITUAL DEVICE");
  text3(42, 35, "ESP32-C3 BASED");

  // The icon, under the OLED, the encoder and the keys as on the board.
  const icon = Uint8Array.from(atob(ICON.bits), (c) => c.charCodeAt(0));
  for (let j = 0; j < ICON.h; j++) {
    for (let i = 0; i < ICON.w; i++) {
      const k = j * ICON.w + i;
      if (icon[k >> 3] & (0x80 >> (k & 7))) set(ICON.x + i, ICON.y + j);
    }
  }

  // OLED glass with the HUB splash, as hub/main/main.c draws it.
  fill(OLED.x, OLED.y, OLED.w, OLED.h, 0);
  box(OLED.x, OLED.y, OLED.w, OLED.h, 2);
  text5(62, 75, "ESPIDI HUB");
  box(BAR.x - 2, BAR.y - 2, BAR.w + 4, 5);
  line(BAR.x, BAR.y, BAR.x + BAR.w - 1, BAR.y);
  text3(52, 93, "PLAY  PAGE  ENC=MENU");

  // Encoder: knurled base, machined top with the pointer.
  const [kx, ky] = KNOB;
  disc(kx, ky, 33, 0);
  circle(kx, ky, 32);
  for (let a = 0; a < 360; a += 12) ray(kx, ky, a, 28, 31);
  circle(kx, ky, 22);
  circle(kx, ky, 15, 1, true);
  circle(kx, ky, 8, 1, true);
  ray(kx, ky, -20, 10, 20);

  // Keys with their labels, REC and STEP between them.
  for (const [name, x] of KEYS) {
    fill(x - 1, KEY_Y - 1, KEY + 2, KEY + 2, 0);
    box(x, KEY_Y, KEY, KEY, 3);
    box(x + 6, KEY_Y + 5, KEY - 12, KEY - 12);
    for (let j = KEY_Y + 7; j < KEY_Y + KEY - 8; j += 2) for (let i = x + 8; i < x + KEY - 7; i += 2) set(i, j);
    label(x + Math.round((KEY - (name.length * 6 - 1)) / 2), 165, name);
  }
  label(81, 166, "---REC---", true);
  label(171, 166, "---STEP---", true);
  box(0, 0, BOARD_W, BOARD_H, 7);   // last: the fills above reach the bottom edge
  return px;
}

// Paints the board; fg and bg are [r, g, b, a].
export function paintBoard(cv, fg, bg) {
  const ctx = cv.getContext("2d");
  const img = ctx.createImageData(BOARD_W, BOARD_H);
  picture().forEach((on, i) => img.data.set(on ? fg : bg, i * 4));
  ctx.putImageData(img, 0, 0);
}
