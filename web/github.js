// Firmwares from ESPidi-ready GitHub repositories: espidi.json, the firmware it names and its
// cover, all from one commit. GitHub release downloads can't be read by a web page (no CORS),
// so the files come from the repository itself — raw.githubusercontent.com, or jsDelivr when
// that is unreachable. Same rules as open_repo() / check_repo() in tools/espidi_hub.py.
import { MANIFEST, validateManifest } from "./espidi.js";

const API = "https://api.github.com";
const RAW = "https://raw.githubusercontent.com";
const JSDELIVR = "https://cdn.jsdelivr.net/gh";

// code: gh_spec, gh_repo, gh_ref, gh_no_manifest, gh_bad_manifest, gh_no_firmware, gh_network
export class GithubError extends Error {
  constructor(code, params = {}) {
    super(code);
    this.code = code;
    this.params = params;
  }
}

const GITHUB_LINK = /^(?:https?:\/\/)?(?:www\.)?github\.com\/([^/]+)\/([^/]+)(?:\/(?:tree|blob)\/([^/]+)(?:\/(.*))?)?$/i;
const RAW_LINK = /^(?:https?:\/\/)?raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\/(.*)$/i;

// owner/repo[/folder][@ref], a github.com link (…/tree/<ref>/<folder>, …/blob/<ref>/…/espidi.json)
// or a raw.githubusercontent.com link -> { owner, repo, ref, path }. parse_spec() in Python.
export function parseSpec(text) {
  const s = String(text).trim().replace(/[?#].*$/, "").replace(/\/+$/, "");
  let owner, repo, ref, path;
  const m = s.match(GITHUB_LINK) || s.match(RAW_LINK);
  if (m) {
    [owner, repo, ref, path] = [m[1], m[2], m[3] || "", m[4] || ""];
  } else {
    const at = s.indexOf("@");
    const parts = (at < 0 ? s : s.slice(0, at)).split("/");
    ref = at < 0 ? "" : s.slice(at + 1);
    if (parts.length < 2) throw new GithubError("gh_spec");
    [owner, repo] = parts;
    path = parts.slice(2).join("/");
  }
  if (repo.toLowerCase().endsWith(".git")) repo = repo.slice(0, -4);
  path = path.replace(/^\/+|\/+$/g, "");
  if (path === MANIFEST || path.endsWith(`/${MANIFEST}`)) path = path.slice(0, -MANIFEST.length).replace(/\/+$/, "");
  const ok = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(owner) && /^[A-Za-z0-9._-]{1,100}$/.test(repo) &&
    (!path || path.split("/").every((seg) => !["", ".", ".."].includes(seg))) &&
    /^[^\s~^:?*[\\]*$/.test(ref) && !ref.includes("..") && !ref.startsWith("/") && !ref.endsWith("/");
  if (!ok) throw new GithubError("gh_spec");
  return { owner, repo, ref, path };
}

export const formatSpec = (s) => `${s.owner}/${s.repo}${s.path ? `/${s.path}` : ""}${s.ref ? `@${s.ref}` : ""}`;

// Where to look at the files on github.com (for a link).
export const webUrl = (s, at) =>
  `https://github.com/${s.owner}/${s.repo}${at || s.path ? `/tree/${at || "HEAD"}${s.path ? `/${s.path}` : ""}` : ""}`;

const quotePath = (p) => p.split("/").map(encodeURIComponent).join("/");
const timeout = (ms) => (typeof AbortSignal !== "undefined" && AbortSignal.timeout ? AbortSignal.timeout(ms) : undefined);

// The commit the ref points to, so all files come from one commit (and fresh, not cached by
// ref). null when the API is unreachable or rate-limited (60 requests an hour per address).
async function resolveCommit(spec) {
  let res;
  try {
    res = await fetch(`${API}/repos/${spec.owner}/${spec.repo}/commits/${quotePath(spec.ref || "HEAD")}`,
      { headers: { Accept: "application/vnd.github.sha" }, signal: timeout(20000) });
  } catch {
    return null;
  }
  if (res.status === 404) throw new GithubError("gh_repo", { repo: `${spec.owner}/${spec.repo}` });
  if (res.status === 422) throw new GithubError("gh_ref", { repo: `${spec.owner}/${spec.repo}`, ref: spec.ref });
  if (!res.ok) return null;
  const sha = (await res.text()).trim();
  return /^[0-9a-f]{40}$/.test(sha) ? sha : null;
}

// A file of the repository: Uint8Array, or null if it isn't there.
async function getFile(spec, at, rel) {
  const path = quotePath([spec.path, rel].filter(Boolean).join("/"));
  const urls = [`${RAW}/${spec.owner}/${spec.repo}/${quotePath(at)}/${path}`,
    `${JSDELIVR}/${spec.owner}/${spec.repo}${at === "HEAD" ? "" : `@${quotePath(at)}`}/${path}`];
  for (const url of urls) {
    let res;
    try {
      res = await fetch(url, { signal: timeout(60000) });
    } catch {
      continue;   // blocked or offline: try the next mirror
    }
    if (res.status === 404) return null;
    if (res.ok) return new Uint8Array(await res.arrayBuffer());
  }
  throw new GithubError("gh_network", { file: rel });
}

// -> { spec, sha, at, manifest, data, coverData (Uint8Array | null), coverMissing }
export async function loadRepo(spec) {
  const sha = await resolveCommit(spec);
  const at = sha || spec.ref || "HEAD";
  const where = formatSpec(spec);
  const raw = await getFile(spec, at, MANIFEST);
  if (!raw) throw new GithubError("gh_no_manifest", { where });
  let manifest;
  try {
    manifest = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    throw new GithubError("gh_bad_manifest", { codes: ["json"] });
  }
  const codes = validateManifest(manifest);
  if (codes.length) throw new GithubError("gh_bad_manifest", { codes });
  const data = await getFile(spec, at, manifest.firmware);
  if (!data) throw new GithubError("gh_no_firmware", { path: manifest.firmware, where });
  const coverData = manifest.cover ? await getFile(spec, at, manifest.cover) : null;
  return { spec, sha, at, manifest, data, coverData, coverMissing: !!manifest.cover && !coverData };
}
