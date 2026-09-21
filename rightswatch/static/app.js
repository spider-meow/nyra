const state = {
  view: "library",
  overview: null,
  matches: null,
  withinDays: 90,
  ingestFast: false,
  crawlFast: false,
  thenMatch: true,
  fresh: false,
  hideRejected: true,
  maxPages: 30,
  site: "",
  banner: "",
  openKey: "",
  openHit: "",
  libraryShown: 60,
  resultsShown: 40,
  libraryQuery: "",
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
  const stop = job.status === "running" ? `<button type="button" class="btn btn-ghost" data-stop>Arrêter</button>` : "";
  const cls = job.status === "error" ? "is-error" : "";
  const detail = job.status === "error" ? (job.error || job.message) : job.message;
  jobEl.innerHTML = `
    <div class="job ${cls}">
      <p class="kicker">${esc(jobLabel(job.kind))}</p>
      <p class="job-line">${esc(detail || job.status)}</p>
      ${bar}
      ${stop}
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
  const job = state.overview && state.overview.job;
  if (job && job.kind === "crawl" && job.status === "running") {
    const progress = job.progress || {};
    const done = progress.done || 0;
    const total = progress.total || 0;
    exploreMeta.textContent = total ? `Lecture en cours · ${done}/${total}` : "Lecture en cours";
  } else if (job && job.kind === "crawl" && job.status === "done" && String(job.message || "").startsWith("Arrêté")) {
    exploreMeta.textContent = `Lecture arrêtée · ${stats.pages_crawled || 0} pages en stock`;
  } else if (job && job.kind === "crawl" && job.status === "done") {
    exploreMeta.textContent = `Lecture terminée · ${stats.pages_crawled || 0} pages, ${stats.site_images || 0} images`;
  } else {
    exploreMeta.textContent = stats.pages_crawled
      ? `${stats.pages_crawled} pages en stock, ${stats.site_images || 0} images`
      : "Là où elles pourraient être";
  }
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

function thumb(url, size) {
  if (!url) return "";
  const join = url.includes("?") ? "&" : "?";
  return `${url}${join}w=${size || 80}`;
}

function libraryCard(item) {
  const tag = item.indexed ? "Indexée" : "Date pas encore enregistrée";
  return `
    <article class="card" data-filename="${esc(item.filename)}">
      <div class="line">
        <img class="thumb" alt="" loading="lazy" src="${esc(thumb(item.url, 80))}">
        <div>
          <p class="filename" title="${esc(item.filename)}">${esc(item.filename)}</p>
          <p class="meta">${esc(tag)}</p>
        </div>
        <button type="button" class="btn btn-danger" data-remove="${esc(item.filename)}">Retirer</button>
      </div>
      <div class="line-fields">
        <label><span>Expiration</span><input type="date" data-field="expiry_date" value="${esc(item.expiry_date)}"></label>
        <label><span>Crédit</span><input type="text" data-field="credit" value="${esc(item.credit)}" placeholder="Qui a fait cette image"></label>
        <label><span>Notes</span><input type="text" data-field="notes" value="${esc(item.notes)}" placeholder="Usage, territoire"></label>
      </div>
    </article>`;
}

function renderLibrary() {
  const library = (state.overview && state.overview.library) || [];
  const indexed = ((state.overview && state.overview.stats) || {}).reference_images || 0;
  const query = state.libraryQuery.trim().toLowerCase();
  const filtered = query ? library.filter((item) => item.filename.toLowerCase().includes(query)) : library;
  const shown = filtered.slice(0, state.libraryShown);
  const more = filtered.length > shown.length
    ? `<button type="button" class="btn btn-ghost" data-more="libraryShown">Afficher la suite · ${shown.length}/${filtered.length}</button>`
    : "";
  const cards = library.length
    ? `<div class="rows">${shown.map(libraryCard).join("")}</div>${more}`
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
      <label><span>Filtrer</span><input id="library-filter" type="text" value="${esc(state.libraryQuery)}" placeholder="Nom de fichier"></label>
      <label class="check"><input type="checkbox" id="ingest-fast" ${state.ingestFast ? "checked" : ""}> Indexation rapide, sans le modèle visuel</label>
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
      <label class="check"><input type="checkbox" id="crawl-fast" ${state.crawlFast ? "checked" : ""}> Sans le modèle visuel : plus rapide, les recadrages passent à côté</label>
      <label class="check"><input type="checkbox" id="fresh" ${state.fresh ? "checked" : ""}> Relire les pages déjà vues</label>
      <label class="check"><input type="checkbox" id="then-match" ${state.thenMatch ? "checked" : ""}> Comparer dès que la lecture est finie</label>
      <div class="toolbar">
        <button type="submit" class="btn" ${running() || needsLibrary ? "disabled" : ""}>Lire le site</button>
        <button type="button" class="btn btn-ghost" id="match" ${running() || !stats.site_images ? "disabled" : ""}>Comparer aux références</button>
      </div>
      <p class="hint">${stats.site_images ? `${stats.site_images} image(s) déjà ramenées. La comparaison peut partir de là.` : "La comparaison s'active une fois que des images ont été ramenées."}</p>
    </form>`;
  document.querySelector("#crawl-form").addEventListener("submit", onCrawl);
  document.querySelector("#match").addEventListener("click", onMatch);
  document.querySelector("#crawl-fast").addEventListener("change", (event) => { state.crawlFast = event.target.checked; });
  document.querySelector("#then-match").addEventListener("change", (event) => { state.thenMatch = event.target.checked; });
  document.querySelector("#fresh").addEventListener("change", (event) => { state.fresh = event.target.checked; });
  document.querySelector("#site").addEventListener("input", (event) => { state.site = event.target.value; });
  document.querySelector("#max-pages").addEventListener("input", (event) => { state.maxPages = Number(event.target.value) || state.maxPages; });
}

function dayText(days) {
  if (days === null || days === undefined) return "Date inconnue";
  if (days < 0) return `${Math.abs(days)} j de trop`;
  return `${days} j`;
}

function visibleHits(group) {
  const hits = group.hits || [];
  if (!state.hideRejected) return hits;
  return hits.filter((hit) => hit.decision !== "ecarte");
}

function matchGroup(group) {
  const hits = visibleHits(group);
  if (!hits.length) return "";
  const late = typeof group.days_left === "number" && group.days_left < 0;
  const key = `ref-${group.reference_id}`;
  const open = state.openKey === key;
  const detail = open ? hits.map((hit, index) => {
    const hitKey = `${key}-${index}`;
    const shown = state.openHit === hitKey;
    const ids = (hit.site_image_ids || [hit.site_image_id]).join(",");
    const decision = hit.decision ? ` · ${hit.decision === "ecarte" ? "écarté" : hit.decision === "retenu" ? "retenu" : "traité"}` : "";
    const links = shown
      ? (hit.pages || []).map((url) => `<li><a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a></li>`).join("")
      : "";
    return `
      <div class="hit">
        <button type="button" class="btn btn-ghost" data-hit="${hitKey}">${shown ? "Fermer" : "Voir"}</button>
        <p class="meta">Score ${Math.round((hit.score || 0) * 100)} % · ${esc(hit.level)}${esc(decision)} · ${hit.page_count || (hit.pages || []).length} page(s)</p>
        ${confidenceTag(hit.confidence)}
        <button type="button" class="btn btn-ghost" data-decision="retenu" data-ref="${group.reference_id}" data-sites="${esc(ids)}">Retenir</button>
        <button type="button" class="btn btn-ghost" data-decision="ecarte" data-ref="${group.reference_id}" data-sites="${esc(ids)}">Écarter</button>
        <button type="button" class="btn btn-ghost" data-decision="traite" data-ref="${group.reference_id}" data-sites="${esc(ids)}">Traité</button>
      </div>
      ${shown ? `<div class="detail"><img alt="" loading="lazy" src="${esc(thumb(group.ref_image, 160))}"><img alt="" loading="lazy" src="${esc(thumb(hit.site_image, 160))}"></div>` : ""}
      ${links ? `<ul class="pages">${links}</ul>` : ""}`;
  }).join("") : "";
  return `
    <article class="card">
      <button type="button" class="line" data-open="${key}">
        <img class="thumb" alt="" loading="lazy" src="${esc(thumb(group.ref_image, 80))}">
        <div>
          <p class="filename" title="${esc(group.filename)}">${esc(group.filename)}</p>
          <p class="days ${late ? "is-late" : ""}">${esc(dayText(group.days_left))} · ${hits.length} occurrence(s)</p>
        </div>
        ${statusTag(group.status)}
      </button>
      ${detail}
    </article>`;
}

function renderGroupList(title, groups) {
  const visible = groups.slice(0, state.resultsShown);
  const cards = visible.map(matchGroup).filter(Boolean).join("");
  const more = groups.length > state.resultsShown
    ? `<button type="button" class="btn btn-ghost" data-more="resultsShown">Afficher la suite · ${state.resultsShown}/${groups.length}</button>`
    : "";
  if (!groups.length) return "";
  return `<h3 class="section-title">${esc(title)}</h3><div class="rows">${cards}</div>${more}`;
}

function renderResults() {
  const data = state.matches;
  const confirmed = data ? data.confirmed : [];
  const toVerify = data ? data.to_verify : [];
  const later = data ? (data.later || []) : [];
  const body = !data
    ? `<div class="empty"><h3>Lecture en cours</h3><p>Les correspondances arrivent.</p></div>`
    : `
      ${renderGroupList(`${confirmed.length} dans la fenêtre`, confirmed)}
      ${confirmed.length ? "" : `<p class="hint">${later.length ? `Rien dans les ${esc(state.withinDays)} jours. Le reste est listé plus bas.` : "Aucune correspondance pour l'instant."}</p>`}
      ${renderGroupList(`${toVerify.length} à regarder de près`, toVerify)}
      ${renderGroupList(`${later.length} plus loin`, later)}`;
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <h2>Ce qui reste en ligne</h2>
        <p>Une ligne par visuel. Ouvre la ligne pour voir les deux images, en petit.</p>
      </div>
    </div>
    <div class="toolbar">
      <label><span>Montrer jusqu'à</span><input id="within" type="number" min="0" max="3650" value="${esc(state.withinDays)}"></label>
      <label class="check"><input type="checkbox" id="hide-rejected" ${state.hideRejected ? "checked" : ""}> Masquer les écartés</label>
      <button type="button" class="btn btn-ghost" id="reload-matches">Actualiser</button>
      <a class="btn" href="/api/downloads/report.html?within_days=${encodeURIComponent(state.withinDays)}">Rapport</a>
      <a class="btn btn-ghost" href="/api/downloads/matches.csv?within_days=${encodeURIComponent(state.withinDays)}">CSV</a>
    </div>
    ${body}`;
  document.querySelector("#reload-matches").addEventListener("click", () => loadMatches());
  document.querySelector("#hide-rejected").addEventListener("change", (event) => {
    state.hideRejected = event.target.checked;
    render();
  });
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
  } else if (statusChanged && status === "done") {
    const finished = job && job.result && Object.prototype.hasOwnProperty.call(job.result, "matches");
    if (finished) state.view = "results";
    if (finished || state.view === "results") loadMatches();
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
  const filter = document.querySelector("#library-filter");
  if (filter) {
    filter.addEventListener("input", (event) => {
      state.libraryQuery = event.target.value;
      state.libraryShown = 60;
      const caret = event.target.selectionStart;
      render();
      const again = document.querySelector("#library-filter");
      if (again) {
        again.focus();
        again.setSelectionRange(caret, caret);
      }
    });
  }
  document.querySelector("#ingest-fast").addEventListener("change", (event) => { state.ingestFast = event.target.checked; });
  document.querySelector("#ingest").addEventListener("click", async () => {
    setBanner("");
    try {
      await api("/api/jobs/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fast: state.ingestFast }),
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
        fast: state.crawlFast,
        fresh: state.fresh,
        then_match: state.thenMatch,
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
      body: JSON.stringify({ fast: state.crawlFast }),
    });
    state.view = "results";
    refresh(true);
  } catch (error) {
    setBanner(error.message);
  }
}

document.body.addEventListener("click", async (event) => {
  const stop = event.target.closest("[data-stop]");
  if (stop) {
    try { await api("/api/jobs/cancel", { method: "POST" }); } catch (error) { setBanner(error.message); }
    return;
  }
  const decision = event.target.closest("[data-decision]");
  if (decision) {
    const ids = String(decision.dataset.sites || "").split(",").map((item) => Number(item)).filter(Boolean);
    try {
      await api("/api/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reference_id: Number(decision.dataset.ref),
          site_image_ids: ids,
          decision: decision.dataset.decision,
        }),
      });
      await loadMatches();
    } catch (error) {
      setBanner(error.message);
    }
    return;
  }
  const hitToggle = event.target.closest("[data-hit]");
  if (hitToggle) {
    state.openHit = state.openHit === hitToggle.dataset.hit ? "" : hitToggle.dataset.hit;
    render();
    return;
  }
  const opener = event.target.closest("[data-open]");
  if (opener) {
    state.openKey = state.openKey === opener.dataset.open ? "" : opener.dataset.open;
    render();
    return;
  }
  const more = event.target.closest("[data-more]");
  if (more) {
    state[more.dataset.more] += more.dataset.more === "libraryShown" ? 60 : 40;
    render();
    return;
  }
  const node = event.target.closest("[data-view]");
  if (!node || node.tagName === "INPUT") return;
  if (node.tagName === "A") event.preventDefault();
  showView(node.dataset.view);
});

refresh(true).catch((error) => setBanner(error.message));
