// ESPidi HUB composer: pick two firmwares, see where they go, write them over USB or download one image.
import * as E from "./espidi.js";
import { GLYPHS } from "./font.js";
import { t, setLang, getLang, detectLang, saveLang } from "./i18n.js";
import { Device, webSerialSupported } from "./flasher.js";

const BUTTON = ["PLAY", "L/R"];
const HUB_VERSION = "0.1.0";

const state = {
  catalog: [],
  data: new Map(),   // catalog index -> Uint8Array | "loading" | Error
  hub: null,         // { bootloader, hub, version } once the HUB build is published
  slots: [
    { mode: "catalog", pick: 0, file: null, name: "", version: "" },
    { mode: "empty", pick: -1, file: null, name: "", version: "" },
  ],
  device: null,
  deviceState: null, // { hub: false } | { hub: true, layout, meta }
  full: false,
  busy: false,
  progress: null,    // 0..1 while writing
  log: [],
  downloaded: false,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Rounded down, as fmt_size() in tools/espidi_hub.py: free space is never overstated.
const fmt = (n) => (n < 1024 * 1024 ? `${Math.floor(n / 1024)} KB` : `${(Math.floor(n / 10485.76) / 100).toFixed(2)} MB`);

// ---------------------------------------------------------------------------
// Slot sources (what espidi.js gets)
// ---------------------------------------------------------------------------
const hubOnDevice = () => !!(state.device && state.deviceState?.hub);
const canKeep = () => hubOnDevice() && !state.full;
const fullInstall = () => state.full || !hubOnDevice();

function sourceOf(i) {
  const s = state.slots[i];
  if (s.mode === "empty") return { kind: "empty" };
  if (s.mode === "keep") return canKeep() ? { kind: "keep" } : { kind: "empty" };
  if (s.mode === "catalog") {
    const data = state.data.get(s.pick);
    if (!(data instanceof Uint8Array)) return null;
    const fw = state.catalog[s.pick];
    return { kind: "image", data, meta: { id: fw.id, name: fw.name, version: fw.version, author: fw.author } };
  }
  if (!s.file || s.file.info.problems.length) return null;
  const d = s.file.info.desc;
  return { kind: "image", data: s.file.data, meta: { id: d?.id || "", name: s.name, version: s.version, author: d?.author || "" } };
}

// Name the HUB will show for slot i, or "" for an empty slot.
function shownName(i) {
  const src = sourceOf(i);
  if (!src || src.kind === "empty") return "";
  if (src.kind === "keep") {
    const m = state.deviceState.meta[i];
    return state.deviceState.layout.slot[i] ? m.name || `SLOT ${i + 1}` : "";
  }
  return src.meta.name || `SLOT ${i + 1}`;
}

function composition() {
  const sources = [0, 1].map(sourceOf);
  const ready = sources.every(Boolean);
  const layout = E.composeLayout(sources.map((s) => s || { kind: "empty" }), canKeep() ? state.deviceState : null);
  return { sources, ready, layout, fits: layout.fits() };
}

function problemText(p) {
  if (p.code === "too_big") return t("err_too_big", { size: fmt(p.size), max: fmt(E.MAX_IMAGE) });
  return t(`err_${p.code}`, p);
}

// ---------------------------------------------------------------------------
// Pixel text in the HUB's own 5x7 font
// ---------------------------------------------------------------------------
function drawText(ctx, x, y, s, scale = 1) {
  for (const ch of s.toUpperCase()) {
    const rows = GLYPHS[ch] || GLYPHS["?"];
    rows.forEach((row, r) => {
      for (let c = 0; c < 5; c++) if (row & (0x10 >> c)) ctx.fillRect(x + c * scale, y + r * scale, scale, scale);
    });
    x += 6 * scale;
  }
}

function renderLogo() {
  const cv = $("#logo");
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--text").trim() || "#fff";
  drawText(ctx, 1, 0, "ESPIDI HUB", 2);
}

// Same lines as draw_menu() in hub/main/main.c.
function renderOled() {
  const ctx = $("#oled").getContext("2d");
  ctx.fillStyle = "#040506";
  ctx.fillRect(0, 0, 128, 32);
  ctx.fillStyle = "#e3f6ff";
  drawText(ctx, 0, 0, `ESPIDI HUB ${state.hub?.version || HUB_VERSION}`);
  for (let i = 0; i < 2; i++) {
    const name = shownName(i) || "---";
    drawText(ctx, 0, 8 + 8 * i, `${i === 0 ? ">" : " "}${i + 1} ${BUTTON[i].padEnd(5)}${name.slice(0, 12)}`);
  }
  const { layout, fits } = composition();
  const tenths = fits ? Math.floor((layout.free() * 10) / (1024 * 1024)) : 0;
  drawText(ctx, 0, 24, `FREE ${Math.floor(tenths / 10)}.${tenths % 10}MB ENC=RUN`);
}

// ---------------------------------------------------------------------------
// Slot cards
// ---------------------------------------------------------------------------
function renderSlot(i) {
  const s = state.slots[i];
  const el = document.querySelector(`.slot[data-slot="${i}"]`);
  const modes = ["catalog", "file", "empty", ...(canKeep() ? ["keep"] : [])];
  const tabs = modes.map((m) =>
    `<button type="button" data-mode="${m}" aria-pressed="${s.mode === m}">${esc(t(`src_${m}`))}</button>`).join("");

  let body = "";
  if (s.mode === "catalog") {
    body = `<div class="catalog">${state.catalog.map((fw, k) => `
      <button type="button" class="fw" data-pick="${k}" aria-pressed="${s.pick === k}">
        <span class="name">${esc(fw.name)}<span class="tag ${esc(fw.channel)}">${esc(t(`channel_${fw.channel}`))}</span></span>
        <span class="meta">${esc(fw.version)} · ${fmt(fw.size)}</span>
        <span class="summary">${esc(fw.summary?.[getLang()] || fw.summary?.en || "")}</span>
      </button>`).join("")}</div>`;
  } else if (s.mode === "file") {
    body = `<label class="drop" data-drop="${i}">${esc(t("drop"))}<input type="file" accept=".bin,application/octet-stream"></label>
      <div class="hint">${esc(t("file_hint"))}</div>`;
    if (s.file && !s.file.info.problems.length) {
      body += `<div class="fields">
        <label>${esc(t("file_name"))}<input data-field="name" maxlength="23" value="${esc(s.name)}"></label>
        <label>${esc(t("file_version"))}<input data-field="version" maxlength="15" value="${esc(s.version)}"></label>
      </div>`;
    }
  } else if (s.mode === "empty" || !canKeep()) {
    body = `<div class="muted">${esc(t("empty_text"))}</div>`;
  } else {
    const m = state.deviceState.meta[i];
    body = `<div class="muted">${esc(state.deviceState.layout.slot[i] ? t("keep_text", { name: m.name || `SLOT ${i + 1}` }) : t("keep_empty"))}</div>`;
  }

  el.innerHTML = `
    <div class="slot-head"><span class="badge">${BUTTON[i]}</span><span class="muted">${esc(t("slot", { n: i + 1 }))}</span></div>
    <div class="tabs" role="group">${tabs}</div>
    ${body}
    <div class="slot-foot" id="foot-${i}"></div>`;
  renderFoot(i);
}

function renderFoot(i) {
  const s = state.slots[i];
  const foot = $(`#foot-${i}`);
  let html = "";
  if (s.mode === "catalog") {
    const fw = state.catalog[s.pick];
    const data = state.data.get(s.pick);
    if (!fw) html = "";
    else if (data === "loading" || data === undefined) html = `<span class="muted">${esc(t("loading"))}</span>`;
    else if (data instanceof Error) html = `<span class="err">${esc(data.message === "sha" ? t("err_sha") : t("err_generic", { detail: data.message }))}</span>`;
    else {
      html = `<div class="what">${esc(fw.name)} ${esc(fw.version)}</div>
        <div class="muted">${esc(t("takes", { size: fmt(data.length), alloc: fmt(E.alignUp(data.length)) }))}</div>`;
      if (fw.screen) html += `<img class="shot" src="catalog/${esc(fw.screen)}" alt="${esc(fw.name)}">`;
    }
  } else if (s.mode === "file") {
    if (s.file) {
      const p = s.file.info.problems;
      if (p.length) html = p.map((x) => `<div class="err">${esc(problemText(x))}</div>`).join("");
      else {
        html = `<div class="what">${esc(s.name || s.file.fileName)} ${esc(s.version)}</div>
          <div class="muted">${esc(t("takes", { size: fmt(s.file.data.length), alloc: fmt(E.alignUp(s.file.data.length)) }))}</div>`;
        if (!s.file.info.desc) html += `<div class="hint">${esc(t("no_desc"))}</div>`;
      }
    }
  } else if (s.mode === "keep" && canKeep() && state.deviceState.layout.slot[i]) {
    const m = state.deviceState.meta[i];
    html = `<div class="what">${esc(m.name || `SLOT ${i + 1}`)} ${esc(m.version)}</div>
      <div class="muted">${fmt(state.deviceState.layout.slot[i])}</div>`;
  } else {
    html = `<span class="muted">—</span>`;
  }
  foot.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Flash map
// ---------------------------------------------------------------------------
function renderMap() {
  const { layout, fits } = composition();
  const segs = [
    ["sys", 0x10000, t("seg_sys")],
    ["hub", E.HUB_SIZE, t("seg_hub")],
    ["s1", layout.slot[0], `PLAY ${fmt(layout.slot[0])}`],
    [fits ? "free" : "over", Math.max(layout.free(), fits ? 0 : 0x10000), fits ? `${t("seg_free")} ${fmt(layout.free())}` : "!"],
    ["s2", layout.slot[1], `L/R ${fmt(layout.slot[1])}`],
    ["files", E.FS_SIZE, t("seg_files")],
  ];
  // Segments under ~4% of the flash get no label, only a tooltip.
  $("#map").innerHTML = segs.filter(([, size]) => size > 0).map(([cls, size, label]) =>
    `<div class="seg ${cls}" style="flex:${size} 0 0" title="${esc(label)}">${size >= E.FLASH_SIZE / 25 ? `<span>${esc(label)}</span>` : ""}</div>`).join("");
  $("#map").setAttribute("aria-label", segs.map(([, size, label]) => (size ? label : "")).filter(Boolean).join(", "));
  const legend = $("#map-legend");
  if (fits) {
    legend.className = "legend";
    legend.textContent = t("legend", { free: fmt(layout.free()), m1: fmt(layout.maxSize(0)), m2: fmt(layout.maxSize(1)) });
  } else {
    legend.className = "legend err";
    legend.textContent = t("overflow", { need: fmt(layout.alloc(0) + layout.alloc(1)), area: fmt(E.AREA) });
  }
}

// ---------------------------------------------------------------------------
// Write panel
// ---------------------------------------------------------------------------
function renderNotices() {
  const n = [];
  if (!webSerialSupported()) n.push(t("no_serial"));
  if (!state.hub) n.push(t("hub_missing"));
  $("#notices").innerHTML = n.map((x) => `<div class="notice">${esc(x)}</div>`).join("");
}

function renderWrite() {
  const dev = $("#device");
  if (!state.device) {
    dev.innerHTML = `<div class="row"><button type="button" id="connect" ${!webSerialSupported() || state.busy ? "disabled" : ""}>${esc(t("connect"))}</button></div>`;
  } else {
    const status = state.deviceState?.hub ? `<span class="ok">${esc(t("dev_hub"))}</span>` : `<span class="muted">${esc(t("dev_nohub"))}</span>`;
    dev.innerHTML = `<div class="row"><span>${esc(t("dev_chip", { chip: state.device.chip }))}</span>
      <button type="button" id="disconnect" ${state.busy ? "disabled" : ""}>${esc(t("disconnect"))}</button></div><div>${status}</div>`;
  }

  const modes = $("#modes");
  if (hubOnDevice()) {
    modes.innerHTML = [["update", false], ["full", true]].map(([m, full]) =>
      `<label><input type="radio" name="mode" value="${m}" ${state.full === full ? "checked" : ""} ${state.busy ? "disabled" : ""}> ${esc(t(`mode_${m}`))}</label>`).join("");
  } else {
    modes.innerHTML = "";
  }

  const { ready, fits, sources } = composition();
  const valid = ready && fits;
  const needsHub = fullInstall();
  $("#write").disabled = state.busy || !state.device || !valid || (needsHub && !state.hub);
  $("#download").disabled = state.busy || !valid || !state.hub || sources.some((s) => s?.kind === "keep");

  const progress = $("#progress");
  progress.hidden = state.progress === null;
  progress.firstElementChild.style.width = `${Math.round((state.progress || 0) * 100)}%`;
  const log = $("#log");
  log.hidden = !state.log.length;
  log.textContent = state.log.join("\n");
  log.scrollTop = log.scrollHeight;

  const hint = $("#download-hint");
  hint.hidden = !state.downloaded;
  hint.innerHTML = `${esc(t("download_hint"))}<br><code>esptool --chip esp32c3 write_flash 0x0 espidi-custom.bin</code>`;
}

function render() {
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll(".lang button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === getLang())));
  renderNotices();
  renderSlot(0);
  renderSlot(1);
  renderDerived();
}

function renderDerived() {
  renderMap();
  renderOled();
  renderWrite();
}

function log(line, append = false) {
  if (append && state.log.length) state.log[state.log.length - 1] += line;
  else state.log.push(line);
  if (state.log.length > 400) state.log.splice(0, state.log.length - 400);
  renderWrite();
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function sha256hex(data) {
  return E.toHex(new Uint8Array(await crypto.subtle.digest("SHA-256", data)));
}

async function ensureData(k) {
  if (k < 0 || state.data.has(k)) return;
  state.data.set(k, "loading");
  try {
    const fw = state.catalog[k];
    const res = await fetch(`catalog/${fw.file}`);
    if (!res.ok) throw new Error(`${res.status} ${fw.file}`);
    const data = new Uint8Array(await res.arrayBuffer());
    if (fw.sha256 && (await sha256hex(data)) !== fw.sha256) throw new Error("sha");
    state.data.set(k, data);
  } catch (e) {
    state.data.set(k, e instanceof Error ? e : new Error(String(e)));
  }
  render();
}

async function loadCatalog() {
  try {
    state.catalog = (await (await fetch("catalog/catalog.json")).json()).firmwares;
  } catch {
    state.catalog = [];
  }
  state.slots.forEach((s) => ensureData(s.pick));
}

async function loadHub() {
  try {
    const res = await fetch("hub/manifest.json", { cache: "no-store" });
    if (!res.ok) throw new Error("no manifest");
    const m = await res.json();
    const get = async (name) => new Uint8Array(await (await fetch(`hub/${name}`)).arrayBuffer());
    state.hub = { bootloader: await get(m.bootloader), hub: await get(m.hub), version: m.version };
  } catch {
    state.hub = null;
  }
}

async function loadFile(i, file) {
  const data = new Uint8Array(await file.arrayBuffer());
  const info = E.inspectImage(data);
  const s = state.slots[i];
  s.file = { fileName: file.name, data, info };
  s.name = (info.desc?.name || file.name.replace(/(\.ino)?\.bin$/i, "")).slice(0, 23);
  s.version = (info.desc?.version || "").slice(0, 15);
  render();
}

// ---------------------------------------------------------------------------
// Device
// ---------------------------------------------------------------------------
// An update must not delete anything by accident: empty slots keep what the device has.
function keepInstalled() {
  if (!canKeep()) return;
  state.slots.forEach((s, i) => {
    if (s.mode === "empty" && state.deviceState.layout.slot[i]) s.mode = "keep";
  });
}

async function connect() {
  const dev = new Device(log);
  state.busy = true;
  render();
  try {
    await dev.connect();
    state.device = dev;
    state.deviceState = await dev.readState();
    state.full = false;
    keepInstalled();
  } catch (e) {
    if (e?.name !== "NotFoundError") log(e?.code === "not_c3" ? t("err_not_c3", { detail: e.detail }) : t("err_generic", { detail: e?.message || e }));
    await dev.disconnect();
  } finally {
    state.busy = false;
    render();
  }
}

async function disconnect() {
  await state.device?.disconnect();
  state.device = null;
  state.deviceState = null;
  state.slots.forEach((s) => { if (s.mode === "keep") s.mode = "empty"; });
  render();
}

async function write() {
  const full = fullInstall();
  const { sources } = composition();
  if (full && !confirm(t("confirm_full"))) {
    log(t("cancelled"));
    return;
  }
  const plan = E.planWrites({ sources, device: state.deviceState, full, bootloader: state.hub?.bootloader, hub: state.hub?.hub });
  const total = plan.files.reduce((a, f) => a + f.data.length, 0);
  const start = new Map();
  plan.files.reduce((acc, f) => (start.set(f, acc), acc + f.data.length), 0);
  state.busy = true;
  state.progress = 0;
  render();
  try {
    await state.device.run(plan, (file, written, size) => {
      state.progress = (start.get(file) + (written / size) * file.data.length) / total;
      const pct = Math.round((written / size) * 100);
      const line = t("writing", { label: file.label, pct });
      if (state.log.length && state.log[state.log.length - 1].startsWith(line.split("…")[0])) state.log[state.log.length - 1] = line;
      else state.log.push(line);
      renderWrite();
    });
    state.progress = 1;
    log(t("done"));
    await state.device.reset();
    await disconnect();
  } catch (e) {
    log(t("err_generic", { detail: e?.message || e }));
  } finally {
    state.busy = false;
    render();
  }
}

function download() {
  const { sources } = composition();
  const { image } = E.buildImage({ bootloader: state.hub.bootloader, hub: state.hub.hub, sources });
  const url = URL.createObjectURL(new Blob([image], { type: "application/octet-stream" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: "espidi-custom.bin" });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  state.downloaded = true;
  renderWrite();
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
document.querySelector(".slots").addEventListener("click", (e) => {
  const card = e.target.closest(".slot");
  if (!card || state.busy) return;
  const i = Number(card.dataset.slot);
  const s = state.slots[i];
  const mode = e.target.closest("[data-mode]")?.dataset.mode;
  const pick = e.target.closest("[data-pick]")?.dataset.pick;
  if (mode) {
    s.mode = mode;
    if (mode === "catalog" && s.pick < 0) s.pick = 0;
    ensureData(s.pick);
    render();
  } else if (pick !== undefined) {
    s.pick = Number(pick);
    ensureData(s.pick);
    render();
  }
});

document.querySelector(".slots").addEventListener("change", (e) => {
  if (e.target.type !== "file" || !e.target.files[0]) return;
  loadFile(Number(e.target.closest(".slot").dataset.slot), e.target.files[0]);
});

document.querySelector(".slots").addEventListener("input", (e) => {
  const field = e.target.dataset.field;
  if (!field) return;
  const i = Number(e.target.closest(".slot").dataset.slot);
  state.slots[i][field] = e.target.value;
  renderFoot(i);
  renderDerived();
});

for (const type of ["dragover", "dragleave", "drop"]) {
  document.querySelector(".slots").addEventListener(type, (e) => {
    const drop = e.target.closest("[data-drop]");
    if (!drop) return;
    e.preventDefault();
    drop.classList.toggle("over", type === "dragover");
    if (type === "drop" && e.dataTransfer.files[0]) loadFile(Number(drop.dataset.drop), e.dataTransfer.files[0]);
  });
}

document.querySelector("#device").addEventListener("click", (e) => {
  if (e.target.id === "connect") connect();
  if (e.target.id === "disconnect") disconnect();
});

document.querySelector("#modes").addEventListener("change", (e) => {
  state.full = e.target.value === "full";
  if (state.full) state.slots.forEach((s) => { if (s.mode === "keep") s.mode = "empty"; });
  else keepInstalled();
  render();
});

$("#write").addEventListener("click", write);
$("#download").addEventListener("click", download);

document.querySelectorAll(".lang button").forEach((b) => b.addEventListener("click", () => {
  setLang(b.dataset.lang);
  saveLang(b.dataset.lang);
  render();
}));

window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", renderLogo);

// ---------------------------------------------------------------------------
setLang(detectLang());
renderLogo();
render();
await Promise.all([loadCatalog(), loadHub()]);
render();
