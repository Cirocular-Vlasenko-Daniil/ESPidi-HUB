// ESPidi HUB composer: pick two firmwares and their covers, see where they go, write them over USB
// or download one image.
import * as E from "./espidi.js";
import { t, setLang, getLang, detectLang, saveLang } from "./i18n.js";
import { Device, webSerialSupported } from "./flasher.js";
import * as G from "./github.js";
import { drawText, cssVar, renderLogo } from "./pixels.js";

const BUTTON = ["PLAY", "PAGE"];
const HUB_VERSION = "0.1.0";

const state = {
  catalog: [],
  data: new Map(),   // catalog index -> Uint8Array | "loading" | Error
  covers: new Map(), // catalog index -> its cover (Uint8Array, 64x32 1 bit)
  hub: null,         // { bootloader, hub, version } once the HUB build is published
  // gh: the GitHub tab — { input, spec, status: "loading" | "ok" | "error", repo, error }
  // cover.mode: "auto" — the firmware's own cover (catalog, repository, inside the image, on the device),
  // "custom" — a picture the user put, "none" — no cover.
  slots: [
    { mode: "catalog", pick: 0, file: null, name: "", version: "", cover: { mode: "auto" }, gh: { input: "" } },
    { mode: "empty", pick: -1, file: null, name: "", version: "", cover: { mode: "auto" }, gh: { input: "" } },
  ],
  screen: "menu",    // OLED preview: "menu" or a slot index (its start screen)
  device: null,
  deviceState: null, // { hub: false } | { hub: true, layout, meta, covers }
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

function baseSource(i) {
  const s = state.slots[i];
  if (s.mode === "empty") return { kind: "empty" };
  if (s.mode === "keep") return canKeep() ? { kind: "keep" } : { kind: "empty" };
  if (s.mode === "catalog") {
    const data = state.data.get(s.pick);
    if (!(data instanceof Uint8Array)) return null;
    const fw = state.catalog[s.pick];
    return { kind: "image", data, meta: { id: fw.id, name: fw.name, version: fw.version, author: fw.author } };
  }
  if (s.mode === "github") {
    const r = s.gh.status === "ok" ? s.gh.repo : null;
    return r && !r.info.problems.length ? { kind: "image", data: r.data, meta: repoMeta(r) } : null;
  }
  if (!s.file || s.file.info.problems.length) return null;
  const d = s.file.info.desc;
  return { kind: "image", data: s.file.data, meta: { id: d?.id || "", name: s.name, version: s.version, author: d?.author || "" } };
}

// What the HUB will show for a repository firmware: its descriptor, else the fallbacks in
// espidi.json (RepoReport.meta() in Python), else the repository name.
function repoMeta(r) {
  const d = r.info.desc, m = r.manifest;
  return { id: d?.id || m.id || "", name: d?.name || m.name || r.spec.repo.toUpperCase().slice(0, 23),
    version: d?.version || m.version || "", author: d?.author || m.author || "" };
}

function sourceOf(i) {
  const src = baseSource(i);
  return src && src.kind !== "empty" ? { ...src, cover: coverOf(i)?.bits || null } : src;
}

// ---------------------------------------------------------------------------
// Covers
// ---------------------------------------------------------------------------
const embedded = new WeakMap();   // image data -> the cover inside it
function embeddedCover(data) {
  if (!embedded.has(data)) embedded.set(data, E.inspectImage(data).cover);
  return embedded.get(data);
}

// The cover that comes with what slot i holds: { bits, from } or null.
function ownCover(i) {
  const s = state.slots[i];
  const src = baseSource(i);
  if (!src || src.kind === "empty") return null;
  if (src.kind === "keep") {
    const c = state.deviceState.layout.slot[i] && state.deviceState.covers?.[i];
    return c ? { bits: c, from: "device" } : null;
  }
  if (s.mode === "catalog" && state.covers.get(s.pick)) return { bits: state.covers.get(s.pick), from: "catalog" };
  if (s.mode === "github" && s.gh.repo.cover) return { bits: s.gh.repo.cover, from: "repo", path: s.gh.repo.manifest.cover };
  const c = s.mode === "file" ? s.file.info.cover : s.mode === "github" ? s.gh.repo.info.cover : embeddedCover(src.data);
  return c ? { bits: c, from: "firmware" } : null;
}

// The cover that will be written for slot i: { bits, from } or null.
function coverOf(i) {
  const c = state.slots[i].cover;
  if (c.mode === "none") return null;
  if (c.mode === "custom") return { bits: c.bits, from: "custom" };
  return ownCover(i);
}

// Picture file -> { w, h, rgba } (canvas pixels) or { raw } for a 256-byte cover dump.
async function decodePicture(blob) {
  if (blob.size === E.COVER_BYTES && /\.(bin|raw)$/i.test(blob.name || "")) return { raw: new Uint8Array(await blob.arrayBuffer()) };
  if (blob.size > 30 * 1024 * 1024) throw new Error("too big");
  const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
  let w = bmp.width, h = bmp.height;
  const exact = w % E.COVER_W === 0 && h % E.COVER_H === 0 && w / E.COVER_W === h / E.COVER_H;
  if (!exact && Math.max(w, h) > 1024) {   // a photo: the browser shrinks it first
    const k = 1024 / Math.max(w, h);
    w = Math.max(1, Math.round(w * k));
    h = Math.max(1, Math.round(h * k));
  }
  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close?.();
  return { w, h, rgba: ctx.getImageData(0, 0, w, h).data };
}

// 64x32 and exact multiples go pixel for pixel (as tools/espidi_hub.py cover), the rest is fitted.
function pictureToCover(px, invert) {
  if (px.raw) return { bits: invert ? px.raw.map((b) => b ^ 0xFF) : px.raw, fitted: false };
  try {
    return { bits: E.pixelsToCover(px.w, px.h, px.rgba, invert), fitted: false };
  } catch {
    return { bits: E.fitCover(px.w, px.h, px.rgba, invert), fitted: true };
  }
}

async function loadCover(i, file) {
  const s = state.slots[i];
  try {
    const px = await decodePicture(file);
    s.cover = { mode: "custom", name: file.name, px, invert: false, ...pictureToCover(px, false) };
    state.screen = i;
  } catch {
    s.cover = { ...s.cover, error: file.name };
  }
  render();
}

// ---------------------------------------------------------------------------
// GitHub tab
// ---------------------------------------------------------------------------
const RECENT_KEY = "espidi-repos";

function recentRepos() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(list) ? list.filter((x) => typeof x === "string").slice(0, 6) : [];
  } catch {
    return [];
  }
}

function rememberRepo(spec) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([spec, ...recentRepos().filter((x) => x !== spec)].slice(0, 6)));
  } catch {
    // storage may be blocked
  }
}

async function loadGithub(i, text) {
  const s = state.slots[i];
  s.mode = "github";
  s.cover = { mode: "auto" };
  let spec;
  try {
    spec = G.parseSpec(text);
  } catch {
    s.gh = { input: text, status: "error", error: { code: "gh_spec", params: {} } };
    render();
    return;
  }
  const gh = { input: G.formatSpec(spec), spec, status: "loading" };
  s.gh = gh;
  render();
  try {
    const r = await G.loadRepo(spec);
    r.info = E.inspectImage(r.data);
    if (r.coverData) {
      try {
        r.cover = pictureToCover(await decodePicture(new Blob([r.coverData], { type: "image/png" })), false).bits;
      } catch {
        r.coverBad = true;
      }
    }
    if (s.gh !== gh) return;   // another load started meanwhile
    s.gh = { ...gh, status: "ok", repo: r };
    rememberRepo(gh.input);
  } catch (e) {
    if (s.gh !== gh) return;
    s.gh = { ...gh, status: "error", error: e instanceof G.GithubError ? e : { code: "gh_network", params: {} } };
  }
  render();
}

// ?play=owner/repo&page=owner/repo — a link that opens the composer with these firmwares.
function syncUrl() {
  const params = new URLSearchParams(location.search);
  ["play", "page"].forEach((key) => params.delete(key));
  ["play", "page"].forEach((key, i) => {
    const s = state.slots[i];
    if (s.mode === "github" && s.gh.spec) params.append(key, G.formatSpec(s.gh.spec));
  });
  // owner/repo@ref reads better than owner%2Frepo%40ref, and is just as valid in a query.
  const query = params.toString().replace(/%2F/gi, "/").replace(/%40/gi, "@");
  const url = `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
  if (url !== `${location.pathname}${location.search}${location.hash}`) history.replaceState(null, "", url);
}

function githubError(e) {
  const p = { ...e.params };
  if (e.code === "gh_bad_manifest") p.detail = (p.codes || []).map((c) => t(`mf_${c}`)).join("; ");
  return t(e.code, p);
}

async function loadCatalogCovers() {
  await Promise.all(state.catalog.map(async (fw, k) => {
    if (!fw.cover) return;
    try {
      const res = await fetch(`catalog/${fw.cover}`);
      if (!res.ok) return;
      state.covers.set(k, pictureToCover(await decodePicture(await res.blob()), false).bits);
    } catch {
      // the firmware simply shows no cover
    }
  }));
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

function shownVersion(i) {
  const src = baseSource(i);
  if (!src || src.kind === "empty") return "";
  return src.kind === "keep" ? state.deviceState.meta[i].version : src.meta.version;
}

function composition() {
  const sources = [0, 1].map(sourceOf);
  const ready = sources.every(Boolean);
  const layout = E.composeLayout(sources.map((s) => s || { kind: "empty" }), canKeep() ? state.deviceState : null);
  return { sources, ready, layout, fits: layout.fits() };
}

const warningsHtml = (info) => info.warnings.map((w) => `<div class="hint">${esc(t(`warn_${w.code}`, w))}</div>`).join("");

function problemText(p) {
  if (p.code === "too_big") return t("err_too_big", { size: fmt(p.size), max: fmt(E.MAX_IMAGE) });
  return t(`err_${p.code}`, p);
}

// ---------------------------------------------------------------------------
// Pixel text in the HUB's own 5x7 font (drawText in pixels.js)
// ---------------------------------------------------------------------------
const textWidth = (s, scale) => (s.length ? (s.length * 6 - 1) * scale : 0);
const drawCenter = (ctx, y, s, scale) => drawText(ctx, Math.trunc((128 - textWidth(s, scale)) / 2), y, s, scale);

function drawBits(ctx, x, y, bits) {
  for (let j = 0; j < E.COVER_H; j++) {
    for (let k = 0; k < E.COVER_W; k++) if (E.coverPixel(bits, k, j)) ctx.fillRect(x + k, y + j, 1, 1);
  }
}

function paintCover(cv, bits) {
  const ctx = cv.getContext("2d");
  ctx.fillStyle = cssVar("--oled-bg", "#000");
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = cssVar("--oled-fg", "#fff");
  if (bits) drawBits(ctx, 0, 0, bits);
}

function renderOled() {
  const screens = ["menu", 0, 1];
  $("#screens").innerHTML = screens.map((k) => `<button type="button" data-screen="${k}" aria-pressed="${state.screen === k}">${
    k === "menu" ? esc(t("screen_menu")) : BUTTON[k]}</button>`).join("");
  $("#preview-hint").textContent = state.screen === "menu" ? t("preview_hint") : t("preview_start", { button: BUTTON[state.screen] });
  const ctx = $("#oled").getContext("2d");
  ctx.fillStyle = cssVar("--oled-bg", "#000");
  ctx.fillRect(0, 0, 128, 32);
  ctx.fillStyle = cssVar("--oled-fg", "#fff");
  if (state.screen === "menu") drawMenu(ctx);
  else drawStart(ctx, state.screen);
}

// Same as draw_start() in hub/main/main.c (try_launch() for an empty slot).
function drawStart(ctx, i) {
  const name = shownName(i);
  if (!name) {
    drawCenter(ctx, 6, `SLOT ${i + 1}`, 1);
    drawCenter(ctx, 18, "EMPTY", 1);
    return;
  }
  const cover = coverOf(i);
  if (!cover) {
    const scale = textWidth(name, 2) <= 128 ? 2 : 1;
    drawCenter(ctx, scale === 2 ? 9 : 12, name, scale);
    return;
  }
  drawBits(ctx, 0, 0, cover.bits);
  const lines = [];
  for (let at = 0; at < name.length && lines.length < 2; at += 10) lines.push(name.slice(at, at + 10));
  const version = shownVersion(i);
  if (version) lines.push(version.slice(0, 10));
  const y = Math.trunc((32 - (lines.length * 9 - 2)) / 2);
  lines.forEach((line, k) => drawText(ctx, 66, y + k * 9, line));
}

// Same lines as draw_menu() in hub/main/main.c.
function drawMenu(ctx) {
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
  const modes = ["catalog", "github", "file", "empty", ...(canKeep() ? ["keep"] : [])];
  const tabs = modes.map((m) =>
    `<button type="button" data-mode="${m}" aria-pressed="${s.mode === m}">${esc(t(`src_${m}`))}</button>`).join("");

  let body = "";
  if (s.mode === "catalog") {
    body = `<div class="catalog">${state.catalog.map((fw, k) => `
      <button type="button" class="fw${state.covers.has(k) ? " has-thumb" : ""}" data-pick="${k}" aria-pressed="${s.pick === k}">
        ${state.covers.has(k) ? `<canvas class="thumb" width="64" height="32" data-thumb="${k}" aria-hidden="true"></canvas>` : ""}
        <span class="name">${esc(fw.name)}<span class="tag ${esc(fw.channel)}">${esc(t(`channel_${fw.channel}`))}</span></span>
        <span class="meta">${esc(fw.version)} · ${fmt(fw.size)}</span>
        <span class="summary">${esc(fw.summary?.[getLang()] || fw.summary?.en || "")}</span>
      </button>`).join("")}</div>`;
  } else if (s.mode === "github") {
    const recent = recentRepos();
    body = `<form class="gh" data-gh-form>
        <input data-gh-input value="${esc(s.gh.input)}" placeholder="${esc(t("gh_placeholder"))}" aria-label="${esc(t("gh_placeholder"))}"
          spellcheck="false" autocomplete="off" autocapitalize="off" enterkeyhint="go">
        <button type="submit" ${s.gh.status === "loading" ? "disabled" : ""}>${esc(t("gh_load"))}</button>
      </form>
      <div class="hint">${esc(t("gh_hint"))}</div>
      ${recent.length ? `<div class="recent"><span class="muted">${esc(t("gh_recent"))}</span>${recent.map((r) =>
        `<button type="button" data-gh-recent="${esc(r)}">${esc(r)}</button>`).join("")}</div>` : ""}`;
    if (s.gh.status === "loading") body += `<div class="muted">${esc(t("gh_loading", { spec: s.gh.input }))}</div>`;
    if (s.gh.status === "error") body += `<div class="err">${esc(githubError(s.gh.error))}</div>`;
    if (s.gh.status === "ok") {
      const r = s.gh.repo, meta = repoMeta(r), m = r.manifest;
      const summary = typeof m.summary === "string" ? m.summary : m.summary?.[getLang()] || m.summary?.en || "";
      body += `<div class="fw repo-card${r.cover ? " has-thumb" : ""}">
          ${r.cover ? `<canvas class="thumb" width="64" height="32" data-repo-thumb aria-hidden="true"></canvas>` : ""}
          <span class="name">${esc(meta.name)}<span class="tag">github</span></span>
          <span class="meta">${esc(meta.version)} · ${fmt(r.data.length)}</span>
          <span class="summary">${summary ? `${esc(summary)}<br>` : ""}<a href="${esc(G.webUrl(r.spec, r.sha))}" target="_blank" rel="noopener">${
            esc(t("gh_from", { repo: `${r.spec.owner}/${r.spec.repo}`, at: r.sha ? r.sha.slice(0, 7) : r.at }))} ↗</a></span>
        </div>`;
    }
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
  el.querySelectorAll("[data-thumb]").forEach((cv) => paintCover(cv, state.covers.get(Number(cv.dataset.thumb))));
  el.querySelectorAll("[data-repo-thumb]").forEach((cv) => paintCover(cv, s.gh.repo?.cover));
  renderFoot(i);
}

function renderCover(i) {
  const c = state.slots[i].cover;
  const cover = coverOf(i);
  const own = ownCover(i);
  let status;
  if (cover?.from === "custom") status = t("cover_from_custom", { name: c.name }) + (c.fitted ? ` ${t("cover_fitted")}` : "");
  else if (cover) status = t(`cover_from_${cover.from}`, { path: cover.path });
  else status = t(c.mode === "none" ? "cover_removed" : "cover_none");
  const acts = [["upload", true], ["invert", c.mode === "custom"], ["remove", c.mode === "auto" && own],
    ["reset", c.mode === "none" || (c.mode === "custom" && own)], ["remove", c.mode === "custom" && !own]];
  return `<div class="cover-box" data-cover-drop="${i}">
      <label class="cover-pic" title="${esc(t("cover_upload"))}">
        ${cover ? `<canvas width="64" height="32" data-cover-slot="${i}"></canvas>` : `<span class="cover-empty pat"><span>64×32</span></span>`}
        <input type="file" accept="image/png,image/gif,image/bmp,image/jpeg,image/webp,.bin" data-cover-input>
      </label>
      <div class="cover-info">
        <div class="cover-title">${esc(t("cover"))}</div>
        <div class="muted">${esc(status)}</div>
        ${c.error ? `<div class="err">${esc(t("cover_bad", { name: c.error }))}</div>` : ""}
        <div class="cover-actions">${acts.filter(([, on]) => on).map(([a]) =>
          `<button type="button" data-cover-act="${a}">${esc(t(`cover_${a}`))}</button>`).join("")}</div>
      </div>
    </div>
    <div class="hint">${esc(t("cover_hint"))}</div>`;
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
      html += warningsHtml(E.inspectImage(data));
      html += renderCover(i);
      if (fw.screen) html += `<img class="shot" src="catalog/${esc(fw.screen)}" alt="${esc(fw.name)}">`;
    }
  } else if (s.mode === "github") {
    const r = s.gh.status === "ok" ? s.gh.repo : null;
    if (!r) html = `<span class="muted">—</span>`;
    else if (r.info.problems.length) html = r.info.problems.map((x) => `<div class="err">${esc(problemText(x))}</div>`).join("");
    else {
      const meta = repoMeta(r);
      html = `<div class="what">${esc(meta.name)} ${esc(meta.version)}</div>
        <div class="muted">${esc(t("takes", { size: fmt(r.data.length), alloc: fmt(E.alignUp(r.data.length)) }))}</div>`;
      if (r.coverMissing) html += `<div class="hint">${esc(t("gh_cover_missing", { path: r.manifest.cover }))}</div>`;
      if (r.coverBad) html += `<div class="hint">${esc(t("gh_cover_bad", { path: r.manifest.cover }))}</div>`;
      html += warningsHtml(r.info);
      html += renderCover(i);
    }
  } else if (s.mode === "file") {
    if (s.file) {
      const p = s.file.info.problems;
      if (p.length) html = p.map((x) => `<div class="err">${esc(problemText(x))}</div>`).join("");
      else {
        html = `<div class="what">${esc(s.name || s.file.fileName)} ${esc(s.version)}</div>
          <div class="muted">${esc(t("takes", { size: fmt(s.file.data.length), alloc: fmt(E.alignUp(s.file.data.length)) }))}</div>`;
        if (!s.file.info.desc) html += `<div class="hint">${esc(t("no_desc"))}</div>`;
        html += warningsHtml(s.file.info);
        html += renderCover(i);
      }
    }
  } else if (s.mode === "keep" && canKeep() && state.deviceState.layout.slot[i]) {
    const m = state.deviceState.meta[i];
    html = `<div class="what">${esc(m.name || `SLOT ${i + 1}`)} ${esc(m.version)}</div>
      <div class="muted">${fmt(state.deviceState.layout.slot[i])}</div>${renderCover(i)}`;
  } else {
    html = `<span class="muted">—</span>`;
  }
  foot.innerHTML = html;
  foot.querySelectorAll("[data-cover-slot]").forEach((cv) => paintCover(cv, coverOf(i)?.bits));
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
    ["s2", layout.slot[1], `PAGE ${fmt(layout.slot[1])}`],
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
  syncUrl();
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
  await loadCatalogCovers();
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
  s.cover = { mode: "auto" };
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
  const recent = e.target.closest("[data-gh-recent]")?.dataset.ghRecent;
  if (recent) {
    loadGithub(i, recent);
    return;
  }
  const act = e.target.closest("[data-cover-act]")?.dataset.coverAct;
  if (act) {
    if (act === "upload") card.querySelector("[data-cover-input]").click();
    if (act === "invert") s.cover = { ...s.cover, invert: !s.cover.invert, ...pictureToCover(s.cover.px, !s.cover.invert) };
    if (act === "remove") s.cover = { mode: "none" };
    if (act === "reset") s.cover = { mode: "auto" };
    if (act !== "upload") {
      state.screen = i;
      render();
    }
  } else if (mode) {
    if (mode !== s.mode) s.cover = { mode: "auto" };
    s.mode = mode;
    if (mode === "catalog" && s.pick < 0) s.pick = 0;
    ensureData(s.pick);
    render();
  } else if (pick !== undefined) {
    if (Number(pick) !== s.pick) s.cover = { mode: "auto" };
    s.pick = Number(pick);
    ensureData(s.pick);
    render();
  }
});

document.querySelector(".slots").addEventListener("change", (e) => {
  if (e.target.type !== "file" || !e.target.files[0]) return;
  const i = Number(e.target.closest(".slot").dataset.slot);
  if ("coverInput" in e.target.dataset) loadCover(i, e.target.files[0]);
  else loadFile(i, e.target.files[0]);
});

document.querySelector(".slots").addEventListener("submit", (e) => {
  if (!e.target.matches("[data-gh-form]")) return;
  e.preventDefault();
  if (state.busy) return;
  const i = Number(e.target.closest(".slot").dataset.slot);
  loadGithub(i, e.target.querySelector("[data-gh-input]").value);
});

document.querySelector(".slots").addEventListener("input", (e) => {
  if ("ghInput" in e.target.dataset) {
    state.slots[Number(e.target.closest(".slot").dataset.slot)].gh.input = e.target.value;
    return;
  }
  const field = e.target.dataset.field;
  if (!field) return;
  const i = Number(e.target.closest(".slot").dataset.slot);
  state.slots[i][field] = e.target.value;
  renderFoot(i);
  renderDerived();
});

for (const type of ["dragover", "dragleave", "drop"]) {
  document.querySelector(".slots").addEventListener(type, (e) => {
    const drop = e.target.closest("[data-drop], [data-cover-drop]");
    if (!drop || state.busy) return;
    e.preventDefault();
    drop.classList.toggle("over", type === "dragover");
    const file = type === "drop" && e.dataTransfer.files[0];
    if (!file) return;
    if (drop.dataset.drop !== undefined) loadFile(Number(drop.dataset.drop), file);
    else loadCover(Number(drop.dataset.coverDrop), file);
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

$("#screens").addEventListener("click", (e) => {
  const k = e.target.closest("[data-screen]")?.dataset.screen;
  if (k === undefined) return;
  state.screen = k === "menu" ? "menu" : Number(k);
  renderOled();
});

$("#write").addEventListener("click", write);
$("#download").addEventListener("click", download);

document.querySelectorAll(".lang button").forEach((b) => b.addEventListener("click", () => {
  setLang(b.dataset.lang);
  saveLang(b.dataset.lang);
  render();
}));

// ---------------------------------------------------------------------------
setLang(detectLang());
renderLogo($("#logo"));
const params = new URLSearchParams(location.search);
["play", "page"].forEach((key, i) => { if (params.get(key)) loadGithub(i, params.get(key)); });
render();
await Promise.all([loadCatalog(), loadHub()]);
render();
