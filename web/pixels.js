// Pixel text in the HUB's own 5x7 font and the page colours, for the composer and the ESPidi page.
import { GLYPHS } from "./font.js";

export function drawText(ctx, x, y, s, scale = 1) {
  for (const ch of s.toUpperCase()) {
    const rows = GLYPHS[ch] || GLYPHS["?"];
    rows.forEach((row, r) => {
      for (let c = 0; c < 5; c++) if (row & (0x10 >> c)) ctx.fillRect(x + c * scale, y + r * scale, scale, scale);
    });
    x += 6 * scale;
  }
}

export const cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

export function renderLogo(cv) {
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = cssVar("--fg", "#fff");
  drawText(ctx, 1, 0, "ESPIDI HUB", 2);
}
