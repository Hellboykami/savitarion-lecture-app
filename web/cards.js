"use strict";
// Карточки лекций до запуска и перетаскивание файлов (в том числе целых папок).

const KIND_LABELS = { record: "Запись", notes: "Конспекты", slides: "Слайды", other: "Прочее" };
const KIND_ORDER = ["record", "notes", "slides", "other"];

function isJunk(name) {
  const low = name.toLowerCase();
  return low === ".ds_store" || low === "thumbs.db" || low === "desktop.ini" || name.startsWith("._");
}

function naturalCompare(a, b) {
  return a.localeCompare(b, "ru", { numeric: true, sensitivity: "base" });
}

function mergeCard(card) {
  const cards = App.state.cards;
  const index = cards.findIndex(c => c.id === card.id);
  if (index >= 0) cards[index] = card; else cards.push(card);
  renderCards(App.state, true, true);  // изменение сделал пользователь — показываем сразу
}

async function updateCard(cardId, patch) {
  const card = await act(post(`/api/cards/${cardId}`, patch));
  if (card) mergeCard(card);
}

function fileRow(card, file, isRecord, recordIndex, recordCount) {
  const kind = el("select", { title: "Вид файла", "aria-label": `Вид файла «${file.name}»`,
    onchange: () => updateCard(card.id, { kinds: { [file.id]: kind.value } }) },
    KIND_ORDER.map(k => el("option", { value: k, text: KIND_LABELS[k], selected: k === file.kind })));
  const order = el("span", { class: "order" });
  if (isRecord && recordCount > 1) {
    order.append(
      el("button", { type: "button", class: "iconbtn", title: "Выше", "aria-label": `Поднять «${file.name}» выше`,
        disabled: recordIndex === 0, onclick: () => updateCard(card.id, { move: { file: file.id, dir: -1 } }) }, [icon("up")]),
      el("button", { type: "button", class: "iconbtn", title: "Ниже", "aria-label": `Опустить «${file.name}» ниже`,
        disabled: recordIndex === recordCount - 1, onclick: () => updateCard(card.id, { move: { file: file.id, dir: 1 } }) }, [icon("down")]));
  }
  const remove = el("button", { type: "button", class: "iconbtn danger", title: "Убрать файл из карточки",
    "aria-label": `Убрать «${file.name}» из карточки`, onclick: () => updateCard(card.id, { remove: file.id }) }, [icon("x")]);
  return el("div", { class: "file" }, [el("span", { class: "name", text: file.name }),
    el("span", { class: "size", text: fmtSize(file.size) }), kind, order, remove]);
}

function renderCard(card, state) {
  const uploads = Object.values(App.uploads).filter(u => u.card === card.id);
  const subject = el("select", { onchange: () => updateCard(card.id, { subject: subject.value }) },
    state.settings.subjects.map(name => el("option", { value: name, text: name, selected: name === card.subject })));
  const date = el("input", { type: "date", value: card.date, onchange: () => updateCard(card.id, { date: date.value }) });
  const dateLine = el("div", { class: "field" }, [el("span", { text: "Дата" }), date,
    el("span", { class: "source", text: `(${card.date_source})` }),
    card.date_manual ? el("button", { type: "button", class: "link", text: "взять из файлов",
      onclick: () => updateCard(card.id, { date: "" }) }) : null]);
  const body = [
    el("div", { class: "card-head" }, [el("h3", { text: "Лекция" }),
      card.id === App.activeCard ? el("span", { class: "hint", text: "сюда добавятся перетащенные файлы" }) : null]),
    el("div", { class: "field" }, [el("span", { text: "Предмет" }), subject]),
    dateLine,
  ];
  const records = card.files.filter(f => f.kind === "record");
  for (const kind of KIND_ORDER) {
    const list = card.files.filter(f => f.kind === kind);
    if (!list.length) continue;
    body.push(el("h4", { text: KIND_LABELS[kind] }));
    body.push(el("div", { class: "files" }, list.map(f => fileRow(card, f, kind === "record", records.indexOf(f), records.length))));
  }
  if (uploads.length) {
    body.push(el("h4", { text: "Загружается" }));
    body.push(el("div", { class: "files" }, uploads.map(u => el("div", { class: "file uploading" }, [
      el("span", { class: "name", text: u.name }), el("span", { class: "size", text: `${u.percent}%` })]))));
  }
  if (!card.files.length && !uploads.length) {
    body.push(el("p", { class: "hint", text: "Пока пусто: перетащите файлы лекции." }));
  }
  if (card.record_count >= 2) {
    const join = el("input", { type: "checkbox", checked: card.join_parts,
      onchange: () => updateCard(card.id, { join_parts: join.checked }) });
    body.push(el("div", { class: "join" }, [el("label", {}, [join, " части одной записи"]),
      el("div", { class: "hint", text: card.join_parts
        ? "Записи склеятся в показанном порядке и расшифруются как одна; таймкоды пойдут сплошные. Порядок меняется стрелками."
        : "Без галочки каждая запись расшифруется отдельно." })]));
  } else if (card.files.length && !card.record_count) {
    body.push(el("p", { class: "hint", text: "Записи нет — материалы просто разложатся по папке лекции." }));
  }
  const busy = uploads.length > 0 || card.pending > 0;
  body.push(el("div", { class: "actions" }, [
    el("button", { type: "button", text: "Запустить", disabled: busy || !card.files.length, onclick: async () => {
      const job = await act(post(`/api/cards/${card.id}/run`));
      if (job) refreshSoon();
    } }),
    el("button", { type: "button", class: "secondary", text: "Удалить карточку", onclick: async () => {
      if (card.files.length && !window.confirm("Удалить карточку? Файлы уберутся из временной папки; в папку учёбы ничего не попадало.")) return;
      if (await act(post(`/api/cards/${card.id}/delete`))) refreshSoon();
    } }),
  ]));
  return el("div", { class: card.id === App.activeCard ? "card active" : "card", "data-card-id": card.id,
    onclick: () => { if (App.activeCard !== card.id) { App.activeCard = card.id; renderCards(App.state, true); } } }, body);
}

function renderCards(state, force = false, byUser = false) {
  const box = document.getElementById("cards");
  if (!state.cards.some(c => c.id === App.activeCard)) {
    App.activeCard = state.cards.length ? state.cards[state.cards.length - 1].id : null;
  }
  // Фоновое обновление не перерисовывает карточку, пока в ней выбирают или вводят
  // (иначе сбросится открытый список). Изменения самого пользователя видны сразу.
  const focused = document.activeElement;
  const editing = box.contains(focused) && (focused.tagName === "SELECT"
    || (focused.tagName === "INPUT" && focused.type !== "checkbox"));
  if (editing && !byUser) return;
  const key = [state.cards, state.settings.subjects, App.uploads, App.activeCard];
  if (!changed("cards", key) && !force) return;
  App.keys.cards = JSON.stringify(key);
  box.replaceChildren(...state.cards.map(card => renderCard(card, state)));
  document.getElementById("new-card").hidden = state.cards.length === 0;
}

// --- загрузка ---------------------------------------------------------------------------

function uploadFile(cardId, entry) {
  return new Promise((resolve, reject) => {
    const key = `${cardId}:${entry.rel}:${Date.now()}:${Math.random()}`;
    App.uploads[key] = { card: cardId, name: entry.file.name, percent: 0 };
    renderCards(App.state, true);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/cards/${cardId}/files?name=${encodeURIComponent(entry.file.name)}`);
    xhr.setRequestHeader("X-App-Token", TOKEN);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = ev => {
      if (ev.lengthComputable && App.uploads[key]) {
        App.uploads[key].percent = Math.round(100 * ev.loaded / ev.total);
        renderCards(App.state, true);
      }
    };
    xhr.onload = () => {
      delete App.uploads[key];
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* пустой ответ */ }
      if (xhr.status === 200) { resolve(data); return; }
      const err = new Error(data.error || `ошибка ${xhr.status}`);
      err.hint = data.hint || "";
      reject(err);
    };
    xhr.onerror = () => {
      delete App.uploads[key];
      const err = new Error("нет связи с приложением");
      err.hint = "Проверьте, что окно Терминала открыто, и обновите страницу.";
      reject(err);
    };
    xhr.send(entry.file);
  });
}

async function addFiles(entries, targetId) {
  const list = entries.filter(e => !isJunk(e.file.name)).sort((a, b) => naturalCompare(a.rel, b.rel));
  if (!list.length || !App.state) return;
  let cardId = targetId || App.activeCard;
  if (!cardId || !App.state.cards.some(c => c.id === cardId)) {
    const card = await act(post("/api/cards"));
    if (!card) return;
    cardId = card.id;
    App.activeCard = card.id;
    mergeCard(card);
  }
  for (const entry of list) {
    try {
      mergeCard(await uploadFile(cardId, entry));
    } catch (e) {
      renderCards(App.state, true);
      showMessage(`Не загрузился «${entry.file.name}»: ${e.message}`, e.hint, "error");
    }
  }
}

// --- перетаскивание: файлы и целые папки ------------------------------------------------

function walk(entry, prefix, out) {
  if (entry.isFile) {
    return new Promise(resolve => entry.file(file => { out.push({ file, rel: prefix + file.name }); resolve(); }, () => resolve()));
  }
  if (entry.isDirectory) {
    const reader = entry.createReader();
    return new Promise(resolve => {
      const next = () => reader.readEntries(async batch => {
        if (!batch.length) { resolve(); return; }
        for (const child of batch) await walk(child, `${prefix}${entry.name}/`, out);
        next();
      }, () => resolve());
      next();
    });
  }
  return Promise.resolve();
}

async function collectDropped(dt) {
  // webkitGetAsEntry нужно вызвать сразу, пока событие drop не закончилось.
  const entries = dt.items ? [...dt.items].filter(i => i.kind === "file")
    .map(i => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)) : [];
  if (entries.length && entries.every(Boolean)) {
    const out = [];
    for (const entry of entries) await walk(entry, "", out);
    return out;
  }
  return [...dt.files].map(file => ({ file, rel: file.name }));
}

function setupDrop() {
  window.addEventListener("dragover", e => { e.preventDefault(); document.body.classList.add("dragging"); });
  window.addEventListener("dragleave", e => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
  window.addEventListener("drop", async e => {
    e.preventDefault();
    document.body.classList.remove("dragging");
    const cardNode = e.target instanceof Element ? e.target.closest(".card") : null;
    const targetId = cardNode ? Number(cardNode.dataset.cardId) : null;
    await addFiles(await collectDropped(e.dataTransfer), targetId);
  });
  const picker = document.getElementById("picker");
  picker.addEventListener("change", async () => {
    const chosen = [...picker.files].map(file => ({ file, rel: file.name }));
    picker.value = "";
    await addFiles(chosen, null);
  });
  document.getElementById("new-card").addEventListener("click", async () => {
    const card = await act(post("/api/cards"));
    if (card) { App.activeCard = card.id; mergeCard(card); }
  });
}
