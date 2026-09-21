const state = {
  view: "library",
  overview: null,
  matches: null,
  withinDays: 90,
  fast: false,
  fresh: false,
  maxPages: 30,
  site: "",
  banner: "",
};

const panel = document.querySelector("#panel");
const jobEl = document.querySelector("#job");
const bannerEl = document.querySelector("#banner");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

async function api(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
  }
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : "La requête a échoué.");
  }
  return data;
}

function setBanner(message, note) {
  state.banner = message || "";
  bannerEl.hidden = !state.banner;
  bannerEl.textContent = state.banner;
  bannerEl.classList.toggle("note", Boolean(note) && Boolean(state.banner));
}

function jobLabel(kind) {
  return { ingest: "Indexation", crawl: "Exploration", match: "Correspondance" }[kind] || "Tâche";
}

function renderJob() {
  const job = state.overview && state.overview.job;
  if (!job) {
    jobEl.innerHTML = "";
    return;
  }
  const progress = job.progress || {};
  const total = Number(progress.total || 0);
  const done = Number(progress.done || 0);
  let bar = `<div class="bar"><div class="bar-fill is-busy"></div></div>`;
  if (job.status === "running" && total > 0) {
    const width = Math.max(4, Math.round((done / total) * 100));
    bar = `<div class="bar"><div class="bar-fill" style="width:${width}%"></div></div>`;
  }
  if (job.status !== "running") bar = "";
  const cls = job.status === "error" ? "is-error" : "";
  const detail = job.status === "error" ? (job.error || job.message) : job.message;
  jobEl.innerHTML = `
    <div class="job ${cls}">
      <p class="kicker">${esc(jobLabel(job.kind))}</p>
      <p class="job-line">${esc(detail || job.status)}</p>
      ${bar}
    </div>`;
}

function renderStats() {
  const stats = (state.overview && state.overview.stats) || {};
  const library = (state.overview && state.overview.library) || [];
  const waiting = library.filter((item) => !item.indexed).length;
  const libraryMeta = document.querySelector("#meta-library");
  const exploreMeta = document.querySelector("#meta-explore");
  const resultsMeta = document.querySelector("#meta-results");
  if (!libraryMeta) return;
  libraryMeta.textContent = waiting
    ? `${stats.reference_images || 0} indexée(s), ${waiting} en attente`
    : `${stats.reference_images || 0} image(s) à protéger`;
  exploreMeta.textContent = stats.pages_crawled
    ? `${stats.pages_crawled} page(s), ${stats.site_images || 0} image(s)`
    : "Là où elles pourraient être";
  resultsMeta.textContent = stats.matches
    ? `${stats.matches} correspondance(s)`
    : "Ce qui dépasse la date";
}

function running() {
  return state.overview && state.overview.job && state.overview.job.status === "running";
}

function statusTag(status) {
  const map = {
    expire: ["tag-late", "Expiré"],
    "<30j": ["", "Moins de 30 jours"],
    "<90j": ["", "Moins de 90 jours"],
    ok: ["", "Dans les délais"],
    inconnue: ["", "Date inconnue"],
  };
  const [cls, label] = map[status] || ["", status];
  return `<span class="tag ${cls}">${esc(label)}</span>`;
}

function confidenceTag(confidence) {
  const map = {
    haut: "Confirmé",
    moyen: "Probable",
    a_verifier: "À vérifier",
  };
  return `<span class="tag">${esc(map[confidence] || confidence)}</span>`;
}

function libraryCard(item) {
  const tag = item.indexed
    ? `<span class="tag">Indexée</span>`
    : `<span class="tag">Date pas encore enregistrée</span>`;
  return `
    <article class="card" data-filename="${esc(item.filename)}">
      <div class="card-top">
        <img class="thumb" alt="" src="${esc(item.url)}">
        <div>
          <p class="filename">${esc(item.filename)}</p>
          ${tag}
        </div>
      </div>
      <div class="fields">
        <label><span>Expiration</span><input type="date" data-field="expiry_date" value="${esc(item.expiry_date)}"></label>
        <label><span>Crédit</span><input type="text" data-field="credit" value="${esc(item.credit)}" placeholder="Qui a fait cette image"></label>
        <label><span>Notes</span><textarea data-field="notes" placeholder="Usage, territoire, ce qu'il faut retenir">${esc(item.notes)}</textarea></label>
      </div>
      <div class="card-actions">
        <button type="button" class="btn btn-danger" data-remove="${esc(item.filename)}">Retirer</button>
      </div>
    </article>`;
}

function renderLibrary() {
  const library = (state.overview && state.overview.library) || [];
  const indexed = ((state.overview && state.overview.stats) || {}).reference_images || 0;
  const cards = library.length
    ? `<div class="grid">${library.map(libraryCard).join("")}</div>`
    : `<div class="empty"><h3>Rien à protéger pour l'instant</h3><p>Dépose les visuels dont les droits ont une date. Sans eux, le site n'a rien à quoi se comparer.</p></div>`;
  const next = indexed
    ? `<div class="callout"><h3>La liste est prête</h3><p>Ensuite, indique le site où ces images n'ont peut-être plus le droit d'être.</p><div class="toolbar"><button type="button" class="btn" data-view="explore">Continuer vers le site</button></div></div>`
    : "";
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <h2>Les images que tu défends</h2>
        <p>Chaque date est une promesse. Quand elle passe, l'image ne devrait plus être là.</p>
      </div>
    </div>
    <div class="drop" id="drop">
      <h3>Dépose les visuels</h3>
      <p>JPG, PNG, WEBP. La date d'expiration se renseigne juste en dessous.</p>
      <label class="btn file-btn">Choisir des fichiers<input id="files" type="file" accept="image/*" multiple></label>
    </div>
    <div class="toolbar">
      <label class="btn btn-ghost file-btn">Importer un CSV<input id="csv" type="file" accept=".csv,text/csv"></label>
      <label class="check"><input type="checkbox" id="fast" ${state.fast ? "checked" : ""}> Analyse rapide, sans le modèle visuel</label>
      <button type="button" class="btn" id="ingest" ${running() || !library.length ? "disabled" : ""}>Enregistrer les dates</button>
    </div>
    ${cards}
    ${next}`;
  bindLibrary();
}

function renderExplore() {
  const stats = (state.overview && state.overview.stats) || {};
  const needsLibrary = !stats.reference_images;
  const guard = needsLibrary
    ? `<div class="callout"><h3>D'abord, les images</h3><p>Sans bibliothèque, le site n'a rien à quoi se comparer. Reviens à l'étape 01.</p><div class="toolbar"><button type="button" class="btn" data-view="library">Retour à la bibliothèque</button></div></div>`
    : "";
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <h2>Où chercher</h2>
        <p>On lit le sitemap, puis les liens internes. On ramène les images. On ne tranche pas encore.</p>
      </div>
    </div>
    ${guard}
    <form class="form-card" id="crawl-form">
      <label><span>Adresse du site</span><input id="site" type="url" required placeholder="https://www.exemple.com" value="${esc(state.site)}"></label>
      <label><span>Pages à lire au maximum</span><input id="max-pages" type="number" min="1" max="5000" value="${esc(state.maxPages)}"></label>
      <label class="check"><input type="checkbox" id="fast" ${state.fast ? "checked" : ""}> Plus rapide, moins sensible aux recadrages</label>
      <label class="check"><input type="checkbox" id="fresh" ${state.fresh ? "checked" : ""}> Relire les pages déjà vues</label>
      <div class="toolbar">
        <button type="submit" class="btn" ${running() || needsLibrary ? "disabled" : ""}>Lire le site</button>
        <button type="button" class="btn btn-ghost" id="match" ${running() || !stats.site_images ? "disabled" : ""}>Comparer aux références</button>
      </div>
      <p class="hint">${stats.site_images ? `${stats.site_images} image(s) déjà ramenées. La comparaison peut partir de là.` : "La comparaison s'active une fois que des images ont été ramenées."}</p>
    </form>`;
  document.querySelector("#crawl-form").addEventListener("submit", onCrawl);
  document.querySelector("#match").addEventListener("click", onMatch);
  document.querySelector("#fast").addEventListener("change", (event) => { state.fast = event.target.checked; });
  document.querySelector("#fresh").addEventListener("change", (event) => { state.fresh = event.target.checked; });
  document.querySelector("#site").addEventListener("input", (event) => { state.site = event.target.value; });
  document.querySelector("#max-pages").addEventListener("input", (event) => { state.maxPages = Number(event.target.value) || state.maxPages; });
}

function matchCard(row) {
  const late = typeof row.days_left === "number" && row.days_left < 0;
  const days = row.days_left === null || row.days_left === undefined
    ? "Date inconnue"
    : late
      ? `${Math.abs(row.days_left)} j de trop`
      : `${row.days_left} j`;
  const daysNote = row.days_left === null || row.days_left === undefined
    ? "On ne sait pas quand les droits s'arrêtent."
    : late
      ? "Cette image n'a plus le droit d'être là."
      : "jours avant l'échéance.";
  const pages = (row.pages || []).map((url) => `<li><a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a></li>`).join("");
  return `
    <article class="card">
      <div class="pair">
        <figure>
          <img alt="Référence ${esc(row.filename)}" src="${esc(row.ref_image)}">
          <figcaption>La tienne</figcaption>
        </figure>
        <figure>
          <img alt="Image trouvée sur le site" src="${esc(row.site_image)}">
          <figcaption>Sur le site</figcaption>
        </figure>
      </div>
      <p class="days ${late ? "is-late" : ""}">${esc(days)}</p>
      <p class="meta">${esc(daysNote)}</p>
      <p class="filename">${esc(row.filename)}</p>
      <div class="tags">${statusTag(row.status)} ${confidenceTag(row.confidence)}</div>
      <p class="meta">Score ${Math.round((row.score || 0) * 100)} % · ${esc(row.level)}${row.credit ? ` · ${esc(row.credit)}` : ""}</p>
      ${row.notes ? `<p class="meta">${esc(row.notes)}</p>` : ""}
      ${pages ? `<ul class="pages">${pages}</ul>` : ""}
    </article>`;
}

function renderResults() {
  const data = state.matches;
  const confirmed = data ? data.confirmed : [];
  const toVerify = data ? data.to_verify : [];
  const outside = data && data.outside_window
    ? `<div class="callout"><h3>${data.outside_window} autre(s), plus loin</h3><p>Leur échéance dépasse cette fenêtre. Élargis le nombre de jours pour les faire apparaître.</p></div>`
    : "";
  const body = !data
    ? `<div class="empty"><h3>Lecture en cours</h3><p>Les correspondances arrivent.</p></div>`
    : `
      <h3 class="section-title">${confirmed.length} reconnue(s)</h3>
      ${confirmed.length ? `<div class="stack">${confirmed.map(matchCard).join("")}</div>` : `<div class="empty"><h3>Rien de net dans cette fenêtre</h3><p>Soit la comparaison n'a pas encore tourné, soit les échéances sont plus lointaines. Élargis les jours, ou reviens comparer.</p><div class="toolbar"><button type="button" class="btn btn-ghost" data-view="explore">Retour au site</button></div></div>`}
      <h3 class="section-title">${toVerify.length} à regarder de près</h3>
      ${toVerify.length ? `<div class="stack">${toVerify.map(matchCard).join("")}</div>` : `<p class="hint">Rien d'incertain dans cette fenêtre.</p>`}
      ${outside}`;
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <h2>Ce qui reste en ligne</h2>
        <p>Les plus proches de l'échéance d'abord. À gauche, ton image. À droite, celle du site.</p>
      </div>
    </div>
    <div class="toolbar">
      <label><span>Montrer jusqu'à</span><input id="within" type="number" min="0" max="3650" value="${esc(state.withinDays)}"></label>
      <button type="button" class="btn btn-ghost" id="reload-matches">Actualiser</button>
      <a class="btn" href="/api/downloads/report.html?within_days=${encodeURIComponent(state.withinDays)}">Rapport</a>
      <a class="btn btn-ghost" href="/api/downloads/matches.csv?within_days=${encodeURIComponent(state.withinDays)}">CSV</a>
    </div>
    ${body}`;
  document.querySelector("#reload-matches").addEventListener("click", () => loadMatches());
  document.querySelector("#within").addEventListener("change", (event) => {
    state.withinDays = Number(event.target.value);
    loadMatches();
  });
}

let seenStatus = null;
let pollTimer = 0;

function render() {
  document.querySelectorAll(".nav [data-view]").forEach((node) => {
    node.classList.toggle("is-active", node.dataset.view === state.view);
  });
  renderStats();
  renderJob();
  if (state.view === "library") renderLibrary();
  else if (state.view === "explore") renderExplore();
  else renderResults();
}

async function refresh(rebuild) {
  state.overview = await api("/api/overview");
  if (state.overview.defaults && !state._defaultsReady) {
    state.maxPages = state.overview.defaults.max_pages;
    state.withinDays = state.overview.defaults.within_days;
    state._defaultsReady = true;
  }
  const job = state.overview.job;
  const status = job ? job.status : "idle";
  const statusChanged = status !== seenStatus;
  seenStatus = status;
  renderStats();
  renderJob();
  if (rebuild || statusChanged || !panel.childElementCount) render();
  window.clearTimeout(pollTimer);
  if (status === "running") {
    pollTimer = window.setTimeout(() => refresh(false), 1000);
  } else if (statusChanged && status === "done" && state.view === "results") {
    loadMatches();
  }
}

async function loadMatches() {
  state.matches = await api(`/api/matches?within_days=${encodeURIComponent(state.withinDays)}`);
  if (state.view === "results") render();
}

function showView(view) {
  state.view = view;
  setBanner("");
  if (view === "results") loadMatches().catch((error) => setBanner(error.message));
  render();
}

async function onUpload(fileList) {
  const body = new FormData();
  for (const file of fileList) body.append("files", file);
  setBanner("");
  try {
    await api("/api/library/upload", { method: "POST", body });
    await refresh(true);
  } catch (error) {
    setBanner(error.message);
  }
}

async function saveCard(card) {
  const filename = card.dataset.filename;
  const payload = { expiry_date: "", credit: "", notes: "" };
  card.querySelectorAll("[data-field]").forEach((input) => { payload[input.dataset.field] = input.value; });
  try {
    await api(`/api/library/${encodeURIComponent(filename)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    setBanner(error.message);
  }
}

function bindLibrary() {
  const drop = document.querySelector("#drop");
  const files = document.querySelector("#files");
  files.addEventListener("change", () => { if (files.files.length) onUpload(files.files); });
  drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("is-over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("is-over");
    if (event.dataTransfer.files.length) onUpload(event.dataTransfer.files);
  });
  document.querySelector("#csv").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api("/api/library/import-csv", { method: "POST", body });
      await refresh(true);
      setBanner(`${result.applied} ligne(s) de métadonnées importées.`, true);
    } catch (error) {
      setBanner(error.message);
    }
  });
  document.querySelector("#fast").addEventListener("change", (event) => { state.fast = event.target.checked; });
  document.querySelector("#ingest").addEventListener("click", async () => {
    setBanner("");
    try {
      await api("/api/jobs/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fast: state.fast }),
      });
      refresh(true);
    } catch (error) {
      setBanner(error.message);
    }
  });
  panel.querySelectorAll(".card").forEach((card) => {
    card.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("change", () => saveCard(card));
    });
  });
  panel.querySelectorAll("[data-remove]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await api(`/api/library/${encodeURIComponent(button.dataset.remove)}`, { method: "DELETE" });
        await refresh(true);
      } catch (error) {
        setBanner(error.message);
      }
    });
  });
}

async function onCrawl(event) {
  event.preventDefault();
  setBanner("");
  try {
    await api("/api/jobs/crawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site: state.site,
        max_pages: Number(state.maxPages),
        fast: state.fast,
        fresh: state.fresh,
      }),
    });
    refresh(true);
  } catch (error) {
    setBanner(error.message);
  }
}

async function onMatch() {
  setBanner("");
  try {
    await api("/api/jobs/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fast: state.fast }),
    });
    state.view = "results";
    refresh(true);
  } catch (error) {
    setBanner(error.message);
  }
}

document.body.addEventListener("click", (event) => {
  const node = event.target.closest("[data-view]");
  if (!node || node.tagName === "INPUT") return;
  if (node.tagName === "A") event.preventDefault();
  showView(node.dataset.view);
});

refresh(true).catch((error) => setBanner(error.message));
