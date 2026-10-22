// ESPidi HUB core for the web: layout, partition table, metadata sector, image checks.
// A port of tools/espidi_hub.py — keep both in sync (web/test.html compares them byte for byte).

export const FLASH_SIZE = 0x400000;
export const BOOTLOADER_OFFSET = 0x0;
export const PT_OFFSET = 0x8000, PT_MAX = 0xC00;
export const NVS_OFFSET = 0x9000, NVS_SIZE = 0x4000;
export const META_OFFSET = 0xD000, META_SIZE = 0x1000;
export const OTADATA_OFFSET = 0xE000, OTADATA_SIZE = 0x2000;
export const HUB_OFFSET = 0x10000, HUB_SIZE = 0x40000;
export const APP_START = HUB_OFFSET + HUB_SIZE;   // 0x50000
export const APP_END = 0x300000;
export const FS_OFFSET = APP_END, FS_SIZE = FLASH_SIZE - APP_END;
export const APP_ALIGN = 0x10000;
export const PLACEHOLDER = 0x10000;
export const AREA = APP_END - APP_START;
export const MAX_IMAGE = AREA - PLACEHOLDER;
export const SLOTS = 2;

const TYPE_APP = 0x00, TYPE_DATA = 0x01, TYPE_ESPIDI = 0x40;
const SUB_FACTORY = 0x00, SUB_OTA0 = 0x10, SUB_OTA1 = 0x11, SUB_OTA = 0x00, SUB_NVS = 0x02, SUB_SPIFFS = 0x82;

export const alignUp = (n, a = APP_ALIGN) => Math.ceil(n / a) * a;

// ---------------------------------------------------------------------------
// Layout: slot 1 grows up from APP_START, slot 2 grows down to APP_END.
// ---------------------------------------------------------------------------
export class Layout {
  constructor(slot = [0, 0]) {
    this.slot = [...slot];          // bytes given to each slot, 0 = empty
  }
  alloc(i) { return this.slot[i] || PLACEHOLDER; }
  offset(i) { return i === 0 ? APP_START : APP_END - this.alloc(1); }
  maxSize(i) { return AREA - this.alloc(1 - i); }
  free() { return AREA - this.slot[0] - this.slot[1]; }
  fits() { return this.alloc(0) + this.alloc(1) <= AREA; }
  partitions() {
    return [
      { name: "nvs", type: TYPE_DATA, subtype: SUB_NVS, offset: NVS_OFFSET, size: NVS_SIZE },
      { name: "espidi", type: TYPE_ESPIDI, subtype: 0x00, offset: META_OFFSET, size: META_SIZE },
      { name: "otadata", type: TYPE_DATA, subtype: SUB_OTA, offset: OTADATA_OFFSET, size: OTADATA_SIZE },
      { name: "hub", type: TYPE_APP, subtype: SUB_FACTORY, offset: HUB_OFFSET, size: HUB_SIZE },
      { name: "slot1", type: TYPE_APP, subtype: SUB_OTA0, offset: this.offset(0), size: this.alloc(0) },
      { name: "slot2", type: TYPE_APP, subtype: SUB_OTA1, offset: this.offset(1), size: this.alloc(1) },
      { name: "spiffs", type: TYPE_DATA, subtype: SUB_SPIFFS, offset: FS_OFFSET, size: FS_SIZE },
    ];
  }
}

// ---------------------------------------------------------------------------
// MD5 (partition table checksum, esptool verify) and CRC-32 (metadata)
// ---------------------------------------------------------------------------
const MD5_K = Int32Array.from({ length: 64 }, (_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) | 0);
const MD5_S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];

export function md5(bytes) {
  const len = bytes.length;
  const blocks = ((len + 8) >>> 6) + 1;
  const w = new Int32Array(blocks * 16);
  for (let i = 0; i < len; i++) w[i >> 2] |= bytes[i] << ((i % 4) * 8);
  w[len >> 2] |= 0x80 << ((len % 4) * 8);
  w[blocks * 16 - 2] = (len * 8) | 0;
  w[blocks * 16 - 1] = Math.floor(len / 0x20000000);
  let a0 = 0x67452301, b0 = 0xefcdab89 | 0, c0 = 0x98badcfe | 0, d0 = 0x10325476;
  for (let blk = 0; blk < w.length; blk += 16) {
    let a = a0, b = b0, c = c0, d = d0;
    for (let i = 0; i < 64; i++) {
      let f, g;
      if (i < 16) { f = (b & c) | (~b & d); g = i; }
      else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
      else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
      else { f = c ^ (b | ~d); g = (7 * i) % 16; }
      const x = (a + f + MD5_K[i] + w[blk + g]) | 0;
      const s = MD5_S[(i >> 4) * 4 + (i % 4)];
      a = d; d = c; c = b;
      b = (b + ((x << s) | (x >>> (32 - s)))) | 0;
    }
    a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
  }
  const out = new Uint8Array(16);
  const dv = new DataView(out.buffer);
  [a0, b0, c0, d0].forEach((v, i) => dv.setInt32(i * 4, v, true));
  return out;
}

export const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
export const md5hex = (bytes) => toHex(md5(bytes));

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

// ---------------------------------------------------------------------------
// Partition table (same bytes as ESP-IDF gen_esp32part.py)
// ---------------------------------------------------------------------------
function putStr(buf, at, str, n) {
  buf.fill(0, at, at + n);
  const bytes = new TextEncoder().encode(str);
  buf.set(bytes.subarray(0, n - 1), at);
}

function getStr(buf, at, n) {
  const slice = buf.subarray(at, at + n);
  const end = slice.indexOf(0);
  return new TextDecoder("ascii").decode(end >= 0 ? slice.subarray(0, end) : slice);
}

export function buildTable(parts) {
  const out = new Uint8Array(PT_MAX).fill(0xFF);
  const dv = new DataView(out.buffer);
  parts.forEach((p, k) => {
    const o = k * 32;
    out[o] = 0xAA; out[o + 1] = 0x50; out[o + 2] = p.type; out[o + 3] = p.subtype;
    dv.setUint32(o + 4, p.offset, true);
    dv.setUint32(o + 8, p.size, true);
    out.fill(0, o + 12, o + 28);
    out.set(new TextEncoder().encode(p.name).subarray(0, 16), o + 12);
    dv.setUint32(o + 28, p.flags || 0, true);
  });
  const n = parts.length * 32;
  out[n] = 0xEB; out[n + 1] = 0xEB;           // bytes n+2 .. n+15 stay 0xFF
  out.set(md5(out.subarray(0, n)), n + 16);
  return out;
}

export function parseTable(data) {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const parts = [];
  for (let o = 0; o + 32 <= data.length; o += 32) {
    if (data[o] === 0xAA && data[o + 1] === 0x50) {
      parts.push({
        type: data[o + 2], subtype: data[o + 3],
        offset: dv.getUint32(o + 4, true), size: dv.getUint32(o + 8, true),
        name: getStr(data, o + 12, 16), flags: dv.getUint32(o + 28, true),
      });
    } else {
      if (data[o] === 0xEB && data[o + 1] === 0xEB && toHex(md5(data.subarray(0, o))) !== toHex(data.subarray(o + 16, o + 32))) {
        throw new Error("partition table MD5 mismatch");
      }
      break;
    }
  }
  return parts;
}

// The layout of a device, or null if it has no ESPidi HUB.
export function layoutFromTable(parts) {
  const by = Object.fromEntries(parts.map((p) => [p.name, p]));
  const s1 = by.slot1, s2 = by.slot2;
  if (!(s1 && s2 && by.hub && s1.offset === APP_START && s2.offset + s2.size === APP_END)) return null;
  return new Layout([s1.size, s2.size]);
}

// ---------------------------------------------------------------------------
// "espidi" metadata sector (C side: hub/main/slots.c)
// ---------------------------------------------------------------------------
const META_MAGIC = "ESPIDIHB";
const META_HEAD = 12, META_SLOT = 88, META_BODY = META_HEAD + SLOTS * META_SLOT;   // 188

export const emptyMeta = () => ({ id: "", name: "", version: "", author: "", imageSize: 0, imageCrc32: 0 });

export function packMeta(slots, covers = []) {
  const out = new Uint8Array(META_SIZE).fill(0xFF);
  const dv = new DataView(out.buffer);
  out.set(new TextEncoder().encode(META_MAGIC), 0);
  dv.setUint16(8, 1, true);
  dv.setUint16(10, 0, true);
  slots.forEach((m, i) => {
    const o = META_HEAD + i * META_SLOT;
    putStr(out, o, m.id, 16);
    putStr(out, o + 16, m.name, 24);
    putStr(out, o + 40, m.version, 16);
    putStr(out, o + 56, m.author, 24);
    dv.setUint32(o + 80, m.imageSize, true);
    dv.setUint32(o + 84, m.imageCrc32 >>> 0, true);
  });
  dv.setUint32(META_BODY, crc32(out.subarray(0, META_BODY)), true);
  covers.forEach((c, i) => { if (c) out.set(packCover(c), COVER_OFFSETS[i]); });
  return out;
}

export function unpackMeta(data) {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const ok = getStr(data, 0, 8) === META_MAGIC && dv.getUint16(8, true) === 1 &&
    crc32(data.subarray(0, META_BODY)) === dv.getUint32(META_BODY, true);
  return Array.from({ length: SLOTS }, (_, i) => {
    if (!ok) return emptyMeta();
    const o = META_HEAD + i * META_SLOT;
    return {
      id: getStr(data, o, 16), name: getStr(data, o + 16, 24), version: getStr(data, o + 40, 16),
      author: getStr(data, o + 56, 24), imageSize: dv.getUint32(o + 80, true), imageCrc32: dv.getUint32(o + 84, true),
    };
  });
}

// ---------------------------------------------------------------------------
// Covers: 64x32, 1 bit per pixel, rows top to bottom, 8 bytes per row, MSB = leftmost
// pixel, 1 = lit. One block per slot in the "espidi" sector; optionally inside a firmware.
// ---------------------------------------------------------------------------
export const COVER_W = 64, COVER_H = 32, COVER_BYTES = 256;
const COVER_MAGIC = "ESPIDICV";
const COVER_HEAD = 16, COVER_BLOCK = COVER_HEAD + COVER_BYTES + 4;   // 276
export const COVER_OFFSETS = [0x100, 0x300];
const FW_COVER_MAGIC = new TextEncoder().encode("ESPIDI-CV-1\0");

export function packCover(bits) {
  const out = new Uint8Array(COVER_BLOCK);
  const dv = new DataView(out.buffer);
  out.set(new TextEncoder().encode(COVER_MAGIC), 0);
  dv.setUint16(8, COVER_W, true);
  dv.setUint16(10, COVER_H, true);
  dv.setUint32(12, 0, true);
  out.set(bits, COVER_HEAD);
  dv.setUint32(COVER_BLOCK - 4, crc32(out.subarray(0, COVER_BLOCK - 4)), true);
  return out;
}

export function unpackCover(data, offset) {
  const b = data.subarray(offset, offset + COVER_BLOCK);
  if (b.length < COVER_BLOCK) return null;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  if (getStr(b, 0, 8) !== COVER_MAGIC || dv.getUint16(8, true) !== COVER_W || dv.getUint16(10, true) !== COVER_H) return null;
  if (crc32(b.subarray(0, COVER_BLOCK - 4)) !== dv.getUint32(COVER_BLOCK - 4, true)) return null;
  return b.slice(COVER_HEAD, COVER_HEAD + COVER_BYTES);
}

export const unpackCovers = (data) => COVER_OFFSETS.map((o) => unpackCover(data, o));

export const coverPixel = (bits, x, y) => (bits[y * 8 + (x >> 3)] & (0x80 >> (x & 7))) !== 0;

// RGBA pixels (canvas ImageData layout) of a 64x32 picture or an exact multiple of it:
// each k*k block becomes one pixel, lit if at least half of it is light and opaque.
// Same as pixels_to_cover() in tools/espidi_hub.py.
export function pixelsToCover(w, h, rgba, invert = false) {
  if (w % COVER_W || h % COVER_H || w / COVER_W !== h / COVER_H) throw new Error("cover_size");
  const k = w / COVER_W;
  const out = new Uint8Array(COVER_BYTES);
  for (let y = 0; y < COVER_H; y++) {
    for (let x = 0; x < COVER_W; x++) {
      let lit = 0;
      for (let dy = 0; dy < k; dy++) {
        for (let dx = 0; dx < k; dx++) {
          const o = ((y * k + dy) * w + x * k + dx) * 4;
          const lum = Math.floor((rgba[o] * 299 + rgba[o + 1] * 587 + rgba[o + 2] * 114) / 1000);
          if (rgba[o + 3] >= 128 && lum >= 128) lit++;
        }
      }
      if ((lit * 2 >= k * k) !== invert) out[y * 8 + (x >> 3)] |= 0x80 >> (x & 7);
    }
  }
  return out;
}

// Any other picture: scaled to fit 64x32 (centred, black around it) and dithered (Atkinson).
// Light = lit; transparent = dark. Not part of the Python tool — the CLI wants exact sizes.
export function fitCover(w, h, rgba, invert = false) {
  const scale = Math.min(COVER_W / w, COVER_H / h);
  const dw = Math.max(1, Math.round(w * scale)), dh = Math.max(1, Math.round(h * scale));
  const ox = Math.floor((COVER_W - dw) / 2), oy = Math.floor((COVER_H - dh) / 2);
  const g = new Float32Array(COVER_W * COVER_H);   // 0..1, area average of the source
  for (let y = 0; y < dh; y++) {
    const y0 = (y * h) / dh, y1 = ((y + 1) * h) / dh;
    for (let x = 0; x < dw; x++) {
      const x0 = (x * w) / dw, x1 = ((x + 1) * w) / dw;
      let sum = 0, area = 0;
      for (let sy = Math.floor(y0); sy < Math.ceil(y1); sy++) {
        const fy = Math.min(y1, sy + 1) - Math.max(y0, sy);
        for (let sx = Math.floor(x0); sx < Math.ceil(x1); sx++) {
          const f = fy * (Math.min(x1, sx + 1) - Math.max(x0, sx));
          const o = (sy * w + sx) * 4;
          sum += f * ((rgba[o] * 0.299 + rgba[o + 1] * 0.587 + rgba[o + 2] * 0.114) / 255) * (rgba[o + 3] / 255);
          area += f;
        }
      }
      const v = sum / area;
      g[(oy + y) * COVER_W + ox + x] = invert ? 1 - v : v;
    }
  }
  if (invert) {   // the margin is part of the picture's background too
    for (let y = 0; y < COVER_H; y++) {
      for (let x = 0; x < COVER_W; x++) {
        if (x < ox || x >= ox + dw || y < oy || y >= oy + dh) g[y * COVER_W + x] = 1;
      }
    }
  }
  const out = new Uint8Array(COVER_BYTES);
  for (let y = 0; y < COVER_H; y++) {
    for (let x = 0; x < COVER_W; x++) {
      const i = y * COVER_W + x;
      const on = g[i] >= 0.5;
      if (on) out[y * 8 + (x >> 3)] |= 0x80 >> (x & 7);
      const err = (g[i] - (on ? 1 : 0)) / 8;
      for (const [dx, dy] of [[1, 0], [2, 0], [-1, 1], [0, 1], [1, 1], [0, 2]]) {
        const nx = x + dx, ny = y + dy;
        if (nx >= 0 && nx < COVER_W && ny < COVER_H) g[ny * COVER_W + nx] += err;
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Firmware images
// ---------------------------------------------------------------------------
const CHIP_ID_ESP32C3 = 5;
const APP_DESC_MAGIC = 0xABCD5432;
const DESC_MAGIC = new TextEncoder().encode("ESPIDI-FW-1\0");

function findBytes(hay, needle) {
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

// The mode the firmware reads its flash in (image_flash_mode in espidi_hub.py): the Arduino
// chip report's line, else the header's — which says DIO even for a QIO build.
const FLASH_MODES = ["QIO", "QOUT", "DIO", "DOUT"];
export function imageFlashMode(data) {
  for (const mode of FLASH_MODES) {
    if (findBytes(data, new Uint8Array([0, ...[...mode].map((c) => c.charCodeAt(0)), 10, 0])) >= 0) return mode;
  }
  return data.length > 2 && data[2] < FLASH_MODES.length ? FLASH_MODES[data[2]] : "";
}

// Problems and warnings are codes; the UI translates them (see i18n.js).
export function inspectImage(data) {
  const info = { size: data.length, chipId: -1, project: "", appVersion: "", idfVersion: "", desc: null, cover: null,
    flashMode: "", problems: [], warnings: [] };
  if (data.length < 0x20 + 144 || data[0] !== 0xE9) {
    info.problems.push({ code: "not_image" });
    return info;
  }
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  info.chipId = dv.getUint16(12, true);
  if (info.chipId !== CHIP_ID_ESP32C3) info.problems.push({ code: "wrong_chip", chipId: info.chipId });
  if (dv.getUint32(0x20, true) === APP_DESC_MAGIC) {
    info.appVersion = getStr(data, 0x30, 32);
    info.project = getStr(data, 0x50, 32);
    info.idfVersion = getStr(data, 0x90, 32);
  } else {
    info.problems.push({ code: "no_app_desc" });
  }
  const pos = findBytes(data, DESC_MAGIC);
  if (pos >= 0 && pos + 92 <= data.length) {
    info.desc = {
      id: getStr(data, pos + 12, 16), name: getStr(data, pos + 28, 24),
      version: getStr(data, pos + 52, 16), author: getStr(data, pos + 68, 24),
    };
  }
  const cv = findBytes(data, FW_COVER_MAGIC);
  if (cv >= 0 && cv + FW_COVER_MAGIC.length + COVER_BYTES <= data.length) {
    info.cover = data.slice(cv + FW_COVER_MAGIC.length, cv + FW_COVER_MAGIC.length + COVER_BYTES);
  }
  info.flashMode = imageFlashMode(data);
  if (info.flashMode === "QIO" || info.flashMode === "QOUT") info.warnings.push({ code: "quad", mode: info.flashMode });
  if (alignUp(data.length) > MAX_IMAGE) info.problems.push({ code: "too_big", size: data.length });
  return info;
}

// ---------------------------------------------------------------------------
// espidi.json of an ESPidi-ready repository — check_manifest() in tools/espidi_hub.py.
// Problem codes: object, format, firmware, cover, summary, sketch, text.
// ---------------------------------------------------------------------------
export const MANIFEST = "espidi.json";
const MANIFEST_TEXT = { id: 15, name: 23, version: 15, author: 23 };

const repoPath = (p, ext) => typeof p === "string" && p.toLowerCase().endsWith(ext) && !p.startsWith("/") &&
  !p.includes("\\") && p.split("/").every((seg) => !["", ".", ".."].includes(seg));

export function validateManifest(m) {
  if (m === null || typeof m !== "object" || Array.isArray(m)) return ["object"];
  const out = [];
  if (m.espidi !== 1) out.push("format");
  if (!repoPath(m.firmware, ".bin")) out.push("firmware");
  if ("cover" in m && !repoPath(m.cover, ".png")) out.push("cover");
  const summary = "summary" in m ? m.summary : "";
  if (!(typeof summary === "string" || (summary && typeof summary === "object" && !Array.isArray(summary) &&
    Object.values(summary).every((v) => typeof v === "string")))) out.push("summary");
  if ("sketch" in m && !(typeof m.sketch === "string" && /^[A-Za-z0-9_.-]+$/.test(m.sketch))) out.push("sketch");
  if (Object.entries(MANIFEST_TEXT).some(([k, n]) => k in m && !(typeof m[k] === "string" && m[k].length <= n))) out.push("text");
  return out;
}

// ---------------------------------------------------------------------------
// What to write. A slot source is one of:
//   { kind: "empty" }
//   { kind: "keep" }                                (device update only)
//   { kind: "image", data: Uint8Array, meta: { id, name, version, author } }
// Each may carry cover: Uint8Array(256) | null. For "keep", the cover to write — the UI
// passes the device's own cover unless the user changed it.
// ---------------------------------------------------------------------------
export function slotMetaFor(src) {
  return { id: src.meta.id || "", name: src.meta.name || "", version: src.meta.version || "",
    author: src.meta.author || "", imageSize: src.data.length, imageCrc32: crc32(src.data) };
}

export function composeLayout(sources, device = null) {
  return new Layout(sources.map((s, i) => {
    if (s.kind === "image") return alignUp(s.data.length);
    if (s.kind === "keep" && device) return device.layout.slot[i];
    return 0;
  }));
}

// Fresh install: everything from the bootloader up to the end of the firmware area.
// Flashed at 0x0 it also clears NVS (settings); the file partition is not part of it.
export function buildImage({ bootloader, hub, sources }) {
  const layout = composeLayout(sources);
  const out = new Uint8Array(APP_END).fill(0xFF);
  out.set(bootloader, BOOTLOADER_OFFSET);
  out.set(buildTable(layout.partitions()), PT_OFFSET);
  out.set(packMeta(sources.map((s) => (s.kind === "image" ? slotMetaFor(s) : emptyMeta())),
    sources.map((s) => (s.kind === "image" ? s.cover || null : null))), META_OFFSET);
  out.set(hub, HUB_OFFSET);
  sources.forEach((s, i) => { if (s.kind === "image") out.set(s.data, layout.offset(i)); });
  return { image: out, layout };
}

// Device writes. Order matters: images first, then empty-slot placeholders, then the
// partition table and metadata — an interrupted write leaves the old table in charge and a
// half-written slot fails validation, so the HUB starts.
export function planWrites({ sources, device, full, bootloader, hub }) {
  const layout = composeLayout(sources, full ? null : device);
  const meta = sources.map((s, i) => {
    if (s.kind === "image") return slotMetaFor(s);
    if (s.kind === "keep" && device && !full) return device.meta[i];
    return emptyMeta();
  });
  const covers = sources.map((s) => (s.kind === "image" || (s.kind === "keep" && device && !full) ? s.cover || null : null));
  const files = [];
  if (full) files.push({ address: BOOTLOADER_OFFSET, data: bootloader, label: "bootloader" },
    { address: HUB_OFFSET, data: hub, label: "HUB" });
  sources.forEach((s, i) => {
    if (s.kind === "image") files.push({ address: layout.offset(i), data: s.data, label: `slot ${i + 1}` });
  });
  if (!full) {
    sources.forEach((s, i) => {
      if (s.kind === "empty" && device.layout.slot[i]) {
        files.push({ address: layout.offset(i), data: new Uint8Array(PLACEHOLDER).fill(0xFF), label: `slot ${i + 1} (empty)` });
      }
    });
  }
  files.push({ address: PT_OFFSET, data: buildTable(layout.partitions()), label: "partition table" },
    { address: META_OFFSET, data: packMeta(meta, covers), label: "slot info" });
  return { layout, meta, covers, files, erase: full };
}
