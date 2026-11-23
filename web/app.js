"use strict";
// Страница приложения: связь с сервером, очередь, готовые лекции, настройки.
// Карточки лекций и перетаскивание файлов — в cards.js.

const TOKEN = document.querySelector('meta[name="app-token"]').content;
const MODE = document.querySelector('meta[name="app-mode"]').content;
const STATE_LABELS = {
  waiting: "ждёт очереди", running: "идёт", done: "готово",
  warning: "проверьте расшифровку", error: "ошибка", cancelled: "отменено",
};
const App = { state: null, activeCard: null, uploads: {}, keys: {}, settingsDirty: false };

// Иконки — свои SVG одной толщины из шаблона #icons в index.html; здесь только копия.
function icon(name) {
  return document.getElementById("icons").content.querySelector(`[data-icon="${name}"]`).cloneNode(true);
}

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, body) {
  const options = body === undefined ? { method: "GET" } : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-App-Token": TOKEN },
    body: JSON.stringify(body),
  };
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    const err = new Error("Нет связи с приложением.");
    err.hint = "Проверьте, что окно Терминала с приложением открыто, и обновите страницу.";
    throw err;
  }
  let data = {};
  try { data = await response.json(); } catch (e) { /* пустой ответ */ }
  if (!response.ok) {
    const err = new Error(data.error || `Ошибка ${response.status}`);
    err.hint = data.hint || "";
    throw err;
  }
  return data;
}

function post(path, body = {}) { return api(path, body); }

let messageTimer = null;
function showMessage(text, hint = "", kind = "info") {
  const box = document.getElementById("message");
  box.className = kind === "error" ? "message error" : "message";
  box.replaceChildren(...[el("span", { text }), hint ? el("span", { class: "hint", text: hint }) : null]
    .filter(Boolean));  // null внутри replaceChildren превратился бы в текст «null»
  box.hidden = false;
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => { box.hidden = true; }, kind === "error" ? 15000 : 6000);
}

async function act(promise) {
  try { return await promise; } catch (e) { showMessage(e.message, e.hint, "error"); return null; }
}

function fmtSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
  return `${(bytes / 1024 ** 3).toFixed(2).replace(".", ",")} ГБ`;
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h} ч ${String(m).padStart(2, "0")} мин`;
  if (m) return `${m} мин ${String(sec).padStart(2, "0")} с`;
  return `${sec} с`;
}

function changed(name, value) {
  const key = JSON.stringify(value);
  if (App.keys[name] === key) return false;
  App.keys[name] = key;
  return true;
}

// --- вверху: чего не хватает ---------------------------------------------------

function renderProblems(state) {
  const items = state.problems.slice();
  if (state.settings_error) items.push({ text: state.settings_error, hint: "" });
  if (!changed("problems", items)) return;
  document.getElementById("problems").replaceChildren(...items.map(p =>
    el("div", { class: "problem", role: "alert" }, [icon("alert"), el("div", {}, [el("span", { text: p.text }),
      p.hint ? el("span", { class: "hint", text: p.hint }) : null])])));
}

// --- очередь и готовые ------------------------------------------------------------

function jobTitle(job) { return `${job.subject}, ${job.date}`; }

function renderActive(job) {
  const head = el("div", { class: "job-head" }, [el("h3", { text: jobTitle(job) }),
    el("span", { class: "state", text: STATE_LABELS[job.state] })]);
  const cancel = el("button", { type: "button", class: "danger small", text: "Отменить",
    onclick: async () => {
      const res = await act(post(`/api/jobs/${job.id}/cancel`));
      if (res && !res.ok) showMessage(res.message);
      refreshSoon();
    } });
  if (job.state === "waiting") {
    return el("div", { class: "job waiting" }, [head, el("p", { class: "hint", text: "Начнётся, когда закончится текущая." }), cancel]);
  }
  const eta = job.eta === null ? "оцениваю, сколько осталось…" : `осталось примерно ${fmtTime(job.eta)}`;
  return el("div", { class: "job running" }, [head,
    el("div", { class: "step", text: job.step }),
    el("div", { class: "progress-row" }, [
      el("progress", { max: 100, value: job.percent, "aria-label": "Готовность расшифровки" }),
      el("span", { class: "percent", text: `${job.percent}%` })]),
    el("div", { class: "times" }, [el("span", { class: "eta", text: eta }),
      el("span", { class: "elapsed", text: `прошло ${fmtTime(job.elapsed)}` })]),
    el("p", { class: "lid" }, [icon("moon"),
      el("span", { text: "Не закрывайте крышку ноутбука — иначе мак уснёт и расшифровка встанет на паузу." })]),
    el("div", { class: "actions" }, [cancel])]);
}

function renderFinished(job, state) {
  // Оттиск печати «опускается» только у лекции, которая закончилась при открытой странице.
  const stamp = App.freshStamps && App.freshStamps.has(job.id) ? "state fresh" : "state";
  const parts = [el("div", { class: "job-head" }, [el("h3", { text: jobTitle(job) }),
    el("span", { class: stamp, text: STATE_LABELS[job.state] })])];
  for (const warning of job.warnings) parts.push(el("p", { class: "warn", text: warning }));
  if (job.state === "done" || job.state === "warning") {
    parts.push(el("p", { class: "path", text: job.lecture_dir }));
    parts.push(el("p", { text: `В проект Claude пойдёт файлов: ${job.project_files} (.srt, конспекты, слайды).` }));
    if (job.two_uploads) {
      parts.push(el("p", { class: "hint", text: `Это больше ${state.upload_limit} — в чат Claude придётся грузить в два захода.` }));
    }
  } else if (job.state === "error") {
    parts.push(el("p", { class: "error-text", text: job.message }));
    if (job.hint) parts.push(el("p", { class: "hint", text: job.hint }));
  } else {
    parts.push(el("p", { class: "hint", text: job.message }));
  }
  const buttons = [];
  if (job.lecture_dir && (job.state === "done" || job.state === "warning")) {
    buttons.push(el("button", { type: "button", onclick: async () => {
      const res = await act(post(`/api/jobs/${job.id}/open`));
      if (res && !res.ok) showMessage(res.message);
    } }, [icon("folder"), "Открыть папку"]));
  }
  buttons.push(el("button", { type: "button", class: "secondary", text: "Убрать из списка",
    onclick: async () => { await act(post(`/api/jobs/${job.id}/dismiss`)); refreshSoon(); } }));
  parts.push(el("div", { class: "actions" }, buttons));
  return el("div", { class: `job ${job.state}` }, parts);
}

function renderJobs(state) {
  if (!changed("jobs", state.jobs)) return;
  const active = state.jobs.filter(j => j.state === "running" || j.state === "waiting")
    .sort((a, b) => (a.state === "running" ? -1 : 0) - (b.state === "running" ? -1 : 0) || a.id - b.id);
  const finished = state.jobs.filter(j => !(j.state === "running" || j.state === "waiting"))
    .sort((a, b) => b.id - a.id);
  if (!App.seenFinished) App.seenFinished = new Set(finished.map(j => j.id));  // при открытии без анимации
  App.freshStamps = new Set(finished.filter(j => !App.seenFinished.has(j.id)).map(j => j.id));
  App.freshStamps.forEach(id => App.seenFinished.add(id));
  const queue = document.getElementById("queue");
  const done = document.getElementById("done");
  queue.replaceChildren(...(active.length ? active.map(renderActive)
    : [el("p", { class: "empty", text: "Сейчас ничего не идёт и не ждёт." })]));
  done.replaceChildren(...(finished.length ? finished.map(j => renderFinished(j, state))
    : [el("p", { class: "empty", text: "Пока ничего." })]));
}

// --- настройки ---------------------------------------------------------------------

function renderSettings(state, force = false) {
  const box = document.getElementById("settings");
  if (!force && (App.settingsDirty || !changed("settings", state.settings))) return;
  const s = state.settings;
  const subjects = el("div", {}, s.subjects.map(name => subjectRow(name)));
  const root = el("input", { type: "text", value: s.study_root, oninput: () => { App.settingsDirty = true; } });
  const threshold = el("input", { type: "number", min: 2, max: 1000, value: s.loop_threshold,
    oninput: () => { App.settingsDirty = true; } });
  const status = el("span", { class: "saved" });
  const save = el("button", { type: "button", text: "Сохранить настройки", onclick: async () => {
    const names = [...subjects.querySelectorAll("input")].map(i => i.value.trim()).filter(Boolean);
    const res = await act(post("/api/settings", { study_root: root.value, subjects: names,
      loop_threshold: Number(threshold.value) }));
    if (res) {
      App.settingsDirty = false;
      state.settings = res;
      App.keys.settings = JSON.stringify(res);
      renderSettings(state, true);
      document.querySelector("#settings .saved").textContent = "Сохранено.";
      refreshSoon();
    }
  } });
  box.replaceChildren(
    el("label", {}, ["Папка учёбы", root]),
    el("p", { class: "hint", text: `Сейчас это ${s.study_root_full}. Знак ~ — ваша домашняя папка.` }),
    el("p", { class: "label", text: "Предметы" }), subjects,
    el("button", { type: "button", class: "secondary small add", text: "Добавить предмет", onclick: () => {
      subjects.append(subjectRow(""));
      App.settingsDirty = true;
    } }),
    el("label", { class: "threshold" }, ["Порог проверки на петлю", threshold]),
    el("p", { class: "hint", text: "Сколько одинаковых фраз подряд считать петлёй. По умолчанию 5." }),
    el("div", { class: "actions" }, [save, status]));
}

function subjectRow(name) {
  const row = el("div", { class: "subject-row" });
  row.append(el("input", { type: "text", value: name, "aria-label": "Название предмета",
    oninput: () => { App.settingsDirty = true; } }),
    el("button", { type: "button", class: "iconbtn danger", title: "Убрать предмет", "aria-label": "Убрать предмет",
      onclick: () => { row.remove(); App.settingsDirty = true; } }, [icon("x")]));
  return row;
}

// --- опрос ------------------------------------------------------------------------------

let pollTimer = null;
function refreshSoon() { clearTimeout(pollTimer); pollTimer = setTimeout(refresh, 150); }

async function refresh() {
  clearTimeout(pollTimer);
  let busy = false;
  try {
    const state = await api("/api/state");
    App.state = state;
    busy = state.jobs.some(j => j.state === "running" || j.state === "waiting");
    renderProblems(state);
    renderCards(state);
    renderJobs(state);
    renderSettings(state);
  } catch (e) {
    showMessage(e.message, e.hint, "error");
  }
  pollTimer = setTimeout(refresh, busy ? 1000 : 2500);
}

document.getElementById("demo-badge").hidden = MODE !== "demo";
window.addEventListener("DOMContentLoaded", () => { setupDrop(); refresh(); });
