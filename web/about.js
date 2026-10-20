// The ESPidi page: the board drawing, what the platform is, the quote.
import { t, setLang, getLang, detectLang, saveLang } from "./i18n.js";
import { cssVar, renderLogo } from "./pixels.js";
import { paintBoard } from "./board.js";

// "#rrggbb" or any CSS colour -> [r, g, b, 255].
function rgba(color) {
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.fillStyle = color;
  const hex = ctx.fillStyle.slice(1);
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)).concat(255);
}

function render() {
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-label]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nLabel)));
  document.querySelectorAll(".lang button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === getLang())));
}

document.querySelectorAll(".lang button").forEach((b) => b.addEventListener("click", () => {
  setLang(b.dataset.lang);
  saveLang(b.dataset.lang);
  render();
}));

setLang(detectLang());
renderLogo(document.querySelector("#logo"));
paintBoard(document.querySelector("#board"), rgba(cssVar("--fg", "#fff")), rgba(cssVar("--bg", "#000")));
render();
