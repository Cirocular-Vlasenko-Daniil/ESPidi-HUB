// UI strings. t(key, params) fills {placeholders}.
const STRINGS = {
  en: {
    title: "Firmware composer",
    subtitle: "Two firmwares on one ESPidi. Hold PLAY or PAGE at power-on to pick one.",
    slot: "Slot {n}",
    src_catalog: "Catalog",
    src_file: "My file",
    src_empty: "Empty",
    src_keep: "On the device",
    drop: "Drop a .bin here or click to choose",
    file_hint: "The app image: <sketch>.ino.bin — not *.merged.bin.",
    file_name: "Name in the HUB menu",
    file_version: "Version",
    empty_text: "This slot stays empty.",
    keep_text: "Keeps {name} that is already on the device.",
    keep_empty: "This slot is empty on the device.",
    loading: "Loading…",
    takes: "{size} · takes {alloc} in flash",
    no_desc: "No ESPidi descriptor in this file — set the name above.",
    channel_stable: "stable",
    channel_beta: "beta",
    channel_example: "example",
    map_title: "Flash memory",
    seg_sys: "SYS",
    seg_hub: "HUB",
    seg_free: "free",
    seg_files: "FILES",
    legend: "Free {free}. Up to {m1} for PLAY, {m2} for PAGE.",
    overflow: "Doesn't fit: the two firmwares need {need}, there is {area}.",
    preview_title: "On the screen",
    preview_hint: "The HUB menu — hold the encoder button at power-on.",
    write_title: "Write",
    connect: "Connect ESPidi",
    disconnect: "Disconnect",
    no_serial: "Writing over USB needs Chrome, Edge or Opera on a computer. You can still download the image.",
    dev_chip: "Connected: {chip}",
    dev_hub: "ESPidi HUB is installed.",
    dev_nohub: "No ESPidi HUB on this board yet — it needs a full install.",
    mode_update: "Update slots — settings and files stay",
    mode_full: "Full install — erases everything",
    write_btn: "Write to ESPidi",
    download_btn: "Download image",
    download_hint: "One file for a fresh install. Flash it at 0x0 (clears settings):",
    hub_missing: "The HUB build isn't published yet. Composing and checks work; full install and the image download switch on with the first HUB release.",
    confirm_full: "A full install erases all settings and files on this ESPidi. Continue?",
    writing: "Writing {label}… {pct}%",
    done: "Done. Unplug the ESPidi and plug it back in to see the HUB.",
    cancelled: "Cancelled.",
    err_not_image: "Not an ESP32 app image.",
    err_wrong_chip: "Built for another chip (id {chipId}); ESPidi is an ESP32-C3.",
    err_no_app_desc: "No app description — is this the merged image? Use <sketch>.ino.bin.",
    err_too_big: "Too big: {size}, a slot holds at most {max}.",
    err_sha: "Download is corrupted (checksum mismatch).",
    err_not_c3: "This is not an ESP32-C3: {detail}",
    err_generic: "Error: {detail}",
    footer_guide: "Writing firmwares",
    footer_catalog: "Add yours to the catalog",
    footer_source: "Source",
    design: "Design:",
  },
  ru: {
    title: "Сборщик прошивки",
    subtitle: "Две прошивки на одной ESPidi. При включении зажмите PLAY или PAGE, чтобы выбрать.",
    slot: "Слот {n}",
    src_catalog: "Каталог",
    src_file: "Свой файл",
    src_empty: "Пусто",
    src_keep: "Как на плате",
    drop: "Перетащите .bin сюда или нажмите, чтобы выбрать",
    file_hint: "Файл приложения: <sketch>.ino.bin — не *.merged.bin.",
    file_name: "Имя в меню HUB",
    file_version: "Версия",
    empty_text: "Слот останется пустым.",
    keep_text: "Оставит {name}, которая уже стоит на плате.",
    keep_empty: "На плате этот слот пуст.",
    loading: "Загрузка…",
    takes: "{size} · во флеше займёт {alloc}",
    no_desc: "В файле нет дескриптора ESPidi — задайте имя выше.",
    channel_stable: "стабильная",
    channel_beta: "бета",
    channel_example: "пример",
    map_title: "Память",
    seg_sys: "SYS",
    seg_hub: "HUB",
    seg_free: "свободно",
    seg_files: "ФАЙЛЫ",
    legend: "Свободно {free}. Для PLAY — до {m1}, для PAGE — до {m2}.",
    overflow: "Не помещается: двум прошивкам нужно {need}, а есть {area}.",
    preview_title: "На экране",
    preview_hint: "Меню HUB — кнопка энкодера при включении.",
    write_title: "Запись",
    connect: "Подключить ESPidi",
    disconnect: "Отключить",
    no_serial: "Запись по USB работает в Chrome, Edge или Opera на компьютере. Образ можно скачать в любом браузере.",
    dev_chip: "Подключено: {chip}",
    dev_hub: "ESPidi HUB установлен.",
    dev_nohub: "ESPidi HUB на этой плате ещё нет — нужна полная установка.",
    mode_update: "Обновить слоты — настройки и файлы сохранятся",
    mode_full: "Полная установка — стирает всё",
    write_btn: "Записать в ESPidi",
    download_btn: "Скачать образ",
    download_hint: "Один файл для чистой установки. Прошивается с адреса 0x0 (стирает настройки):",
    hub_missing: "Сборка HUB ещё не опубликована. Составлять и проверять уже можно; полная установка и скачивание образа включатся с первым релизом HUB.",
    confirm_full: "Полная установка сотрёт все настройки и файлы на этой ESPidi. Продолжить?",
    writing: "Запись: {label}… {pct}%",
    done: "Готово. Отключите ESPidi и подключите снова — запустится HUB.",
    cancelled: "Отменено.",
    err_not_image: "Это не образ приложения ESP32.",
    err_wrong_chip: "Собрано под другой чип (id {chipId}); ESPidi — это ESP32-C3.",
    err_no_app_desc: "Нет описания приложения — это объединённый образ? Нужен <sketch>.ino.bin.",
    err_too_big: "Слишком большая: {size}, в слот помещается до {max}.",
    err_sha: "Файл скачался с ошибкой (не сходится контрольная сумма).",
    err_not_c3: "Это не ESP32-C3: {detail}",
    err_generic: "Ошибка: {detail}",
    footer_guide: "Как писать прошивки",
    footer_catalog: "Добавить свою в каталог",
    footer_source: "Исходники",
    design: "Дизайн:",
  },
};

let lang = "en";

export function setLang(l) {
  lang = STRINGS[l] ? l : "en";
  document.documentElement.lang = lang;
}

export const getLang = () => lang;

export function t(key, params = {}) {
  const s = STRINGS[lang][key] ?? STRINGS.en[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (k in params ? params[k] : `{${k}}`));
}

export function detectLang() {
  try {
    const saved = localStorage.getItem("espidi-lang");
    if (saved) return saved;
  } catch {
    // storage may be blocked
  }
  return (navigator.language || "").toLowerCase().startsWith("ru") ? "ru" : "en";
}

export function saveLang(l) {
  try {
    localStorage.setItem("espidi-lang", l);
  } catch {
    // ignore
  }
}
