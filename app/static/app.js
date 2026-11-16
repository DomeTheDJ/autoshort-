"use strict";
const $ = (id) => document.getElementById(id);
let pollTimer = null;
let config = null;

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  let data = null;
  try { data = await res.json(); } catch (_) { /* keine JSON-Antwort */ }
  if (!res.ok) {
    const msg = (data && data.detail) || `Fehler ${res.status}`;
    const err = new Error(typeof msg === "string" ? msg : "Unbekannter Fehler");
    err.status = res.status;
    throw err;
  }
  return data;
}

function show(view) {
  $("login-view").classList.toggle("hidden", view !== "login");
  $("app-view").classList.toggle("hidden", view !== "app");
  $("logout").classList.toggle("hidden", view !== "app");
}

async function init() {
  try {
    config = await api("/api/me");
    show("app");
    applyConfig();
    loadJobs();
  } catch (e) {
    show("login");
  }
}

function applyConfig() {
  $("limits").textContent = `Max. ${config.max_upload_mb} MB und ${config.max_video_minutes} Minuten pro Video.`;
  const slider = $("clips");
  slider.min = 1; slider.max = config.max_clips; slider.value = config.max_clips;
  $("clips-value").textContent = slider.value;
  const sel = $("music");
  sel.length = 1;
  for (const name of config.music) {
    const o = document.createElement("option"); o.value = name; o.textContent = name; sel.appendChild(o);
  }
  const f = [];
  f.push(config.transcription ? "Spracherkennung: aktiv" : "Spracherkennung: aus (kein OpenAI-Schlüssel) – keine Untertitel");
  f.push(config.ai ? "KI-Auswahl: Claude" : "KI-Auswahl: kostenlose Heuristik");
  f.push(`Heute verbraucht: ca. ${config.budget.spent.toFixed(2)} von ${config.budget.limit.toFixed(2)} USD`);
  $("features").textContent = f.join(" · ");
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("password").value }) });
    $("password").value = "";
    init();
  } catch (e) { $("login-error").textContent = e.message; }
});

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  clearTimeout(pollTimer);
  show("login");
});

$("clips").addEventListener("input", () => { $("clips-value").textContent = $("clips").value; });
$("file").addEventListener("change", () => {
  const f = $("file").files[0];
  $("file-label").textContent = f ? `${f.name} (${(f.size / 1048576).toFixed(0)} MB)` : "Video auswählen (MP4 / MOV)";
});

$("upload-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const file = $("file").files[0];
  $("upload-error").textContent = "";
  if (!file) return;
  if (file.size > config.max_upload_mb * 1048576) {
    $("upload-error").textContent = `Die Datei ist zu groß (max. ${config.max_upload_mb} MB).`;
    return;
  }
  const params = new URLSearchParams({
    filename: file.name, clips: $("clips").value,
    subtitles: $("opt-subtitles").checked, remove_pauses: $("opt-pauses").checked,
    zooms: $("opt-zooms").checked, sfx: $("opt-sfx").checked, music: $("music").value,
  });
  const xhr = new XMLHttpRequest();
  const box = $("upload-progress");
  box.classList.remove("hidden");
  $("upload-btn").disabled = true;
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    box.querySelector(".bar").style.width = pct + "%";
    box.querySelector(".pct").textContent = pct < 100 ? `Upload ${pct} %` : "Wird geprüft …";
  };
  xhr.onload = () => {
    $("upload-btn").disabled = false;
    box.classList.add("hidden");
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (_) {}
    if (xhr.status >= 200 && xhr.status < 300) {
      $("upload-form").reset();
      $("file-label").textContent = "Video auswählen (MP4 / MOV)";
      applyConfig();
      loadJobs();
    } else if (xhr.status === 401) {
      show("login");
    } else {
      $("upload-error").textContent = data.detail || `Upload fehlgeschlagen (Fehler ${xhr.status}).`;
    }
  };
  xhr.onerror = () => {
    $("upload-btn").disabled = false;
    box.classList.add("hidden");
    $("upload-error").textContent = "Verbindung unterbrochen. Bitte WLAN prüfen und erneut versuchen.";
  };
  xhr.open("POST", "/api/upload?" + params.toString());
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.send(file);
});

const STATUS = { queued: "Wartet", running: "Läuft", done: "Fertig", failed: "Fehler" };

function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  if (text !== undefined) e.textContent = text;
  return e;
}

function renderJob(job) {
  const card = el("div", { class: "card" });
  const head = el("div", { class: "job-head" });
  head.appendChild(el("span", { class: "job-name" }, job.filename));
  head.appendChild(el("span", { class: "status " + job.status }, STATUS[job.status] || job.status));
  card.appendChild(head);
  const base = `/api/jobs/${job.id}/files/`;

  if (job.status === "queued" || job.status === "running") {
    const bar = el("div", { class: "progress" });
    const inner = el("div", { class: "bar" }); inner.style.width = (job.progress || 0) + "%";
    bar.appendChild(inner);
    bar.appendChild(el("span", { class: "pct" }, `${Math.round(job.progress || 0)} % – ${job.message || ""}`));
    card.appendChild(bar);
    card.appendChild(el("p", { class: "hint" }, "Bitte Seite offen und Handy entsperrt lassen – auf dem kostenlosen Server kann das eine Weile dauern."));
  } else if (job.status === "failed") {
    card.appendChild(el("p", { class: "error" }, job.error || "Unbekannter Fehler"));
  } else if (job.status === "done" && job.result) {
    if (job.result.notes && job.result.notes.length) {
      const ul = el("ul", { class: "notes" });
      job.result.notes.forEach((n) => ul.appendChild(el("li", {}, n)));
      card.appendChild(ul);
    }
    const grid = el("div", { class: "clips" });
    for (const c of job.result.clips) {
      const box = el("div", { class: "clip" });
      const v = el("video", { controls: "", playsinline: "", preload: "none", poster: base + c.thumb, src: base + c.file });
      box.appendChild(v);
      box.appendChild(el("div", { class: "title" }, `${c.index}. ${c.title} (${Math.round(c.duration)} s)`));
      box.appendChild(el("a", { class: "secondary", href: base + c.file + "?dl=1", download: c.file }, "MP4 laden"));
      grid.appendChild(box);
    }
    card.appendChild(grid);
    const actions = el("div", { class: "actions" });
    actions.appendChild(el("a", { class: "secondary", href: base + job.result.zip, download: "shorts.zip" }, "Alle als ZIP"));
    card.appendChild(actions);
  }
  if (job.status !== "running") {
    const del = el("button", { class: "link", type: "button" }, "Löschen");
    del.addEventListener("click", async () => {
      if (!confirm("Diesen Auftrag und alle Dateien löschen?")) return;
      await api(`/api/jobs/${job.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
      loadJobs();
    });
    card.appendChild(del);
  }
  return card;
}

let lastSignature = "";
async function loadJobs() {
  clearTimeout(pollTimer);
  try {
    const jobs = await api("/api/jobs");
    const sig = JSON.stringify(jobs.map((j) => [j.id, j.status, j.progress, j.message]));
    if (sig !== lastSignature) {   // nur neu zeichnen, wenn sich etwas geändert hat (Videos laufen weiter)
      lastSignature = sig;
      const box = $("jobs");
      box.replaceChildren(...(jobs.length ? jobs.map(renderJob) : [el("p", { class: "hint" }, "Noch keine Aufträge.")]));
    }
    if (jobs.some((j) => j.status === "queued" || j.status === "running")) {
      pollTimer = setTimeout(loadJobs, 2500);
    }
  } catch (e) {
    if (e.status === 401) show("login");
    else pollTimer = setTimeout(loadJobs, 5000);
  }
}

init();
