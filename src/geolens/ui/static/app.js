// ---------- city coordinates (for the map) ----------
const CITY_COORDS = {
  "Singapore": [1.3521, 103.8198],
  "Tengah Plantation Crescent": [1.3608, 103.7382],
  "Tampines": [1.3496, 103.9568],
  "Jurong East": [1.3329, 103.7436],
  "Punggol": [1.4041, 103.9025],
  "Bedok": [1.3236, 103.9273],
  "Woodlands": [1.4382, 103.7891],
  "Kuala Lumpur": [3.139, 101.6869],
  "Petaling Jaya": [3.1073, 101.6067],
  "Jakarta": [-6.2088, 106.8456],
  "Pekanbaru": [0.5071, 101.4478],
  "Bangkok": [13.7563, 100.5018],
  "Manila": [14.5995, 120.9842],
  "Ho Chi Minh City": [10.8231, 106.6297],
  "Hong Kong": [22.3193, 114.1694],
  "Tokyo": [35.6762, 139.6503],
  "Seoul": [37.5665, 126.978],
  "Sydney": [-33.8688, 151.2093],
  "London": [51.5074, -0.1278],
  "New York": [40.7128, -74.006],
  "San Francisco": [37.7749, -122.4194],
  "Toronto": [43.6532, -79.3832],
};

// engine name -> { label, family, granularity, tag (short pill) }
const ENGINE_META = {
  contrastgeo:        { label: "ContrastGeo",          family: "contrastgeo",   granularity: "post", tag: "few-shot" },
  fewuser:            { label: "FewUser",              family: "fewuser",       granularity: "user", tag: "few-shot" },
  retrievezero:       { label: "RetrieveZero",         family: "retrievezero",  granularity: "user", tag: "zero-shot" },
  gazetteer_post:     { label: "Gazetteer (post)",     family: "gazetteer",         granularity: "post", tag: "string-match" },
  gazetteer_user:     { label: "Gazetteer (user)",     family: "gazetteer",         granularity: "user", tag: "string-match" },
  gpt4o_mini_post:    { label: "GPT-4o-mini (post)",   family: "gpt",               granularity: "post", tag: "LLM" },
  gpt4o_mini_user:    { label: "GPT-4o-mini (user)",   family: "gpt",               granularity: "user", tag: "LLM" },
  claude_haiku_post:  { label: "Claude Haiku (post)",  family: "claude",            granularity: "post", tag: "LLM" },
  claude_haiku_user:  { label: "Claude Haiku (user)",  family: "claude",            granularity: "user", tag: "LLM" },
};

// ---------- map ----------
const map = L.map("map").setView([1.35, 103.82], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap",
  maxZoom: 18,
}).addTo(map);
const markerLayer = L.layerGroup().addTo(map);

function bucketMarker(bucket, coords, popup) {
  // Glyph (P/U) so the two markers are distinguishable without relying on
  // colour or shape alone (accessibility / colour-blind users).
  const glyph = bucket === "post" ? "P" : "U";
  const html = `<div class="engine-marker ${bucket}" title="${bucket}-level">${glyph}</div>`;
  const icon = L.divIcon({ html, className: "", iconSize: [18, 18], iconAnchor: [9, 9] });
  return L.marker(coords, { icon, title: `${bucket}-level consensus` }).bindPopup(popup);
}

// ---------- scenario tiles ----------
let currentScenario = null;
let replayTimers = [];

async function loadScenarios() {
  const tilesEl = document.getElementById("scenario-tiles");
  tilesEl.innerHTML = "";
  let index;
  try {
    const r = await fetch("/static/scenarios/index.json");
    index = await r.json();
  } catch (e) { console.warn("Scenario index missing", e); return; }
  for (const id of index.scenarios) {
    try {
      const r = await fetch(`/static/scenarios/${id}.json`);
      const sc = await r.json();
      const btn = document.createElement("button");
      btn.className = "scenario-tile";
      btn.dataset.scenarioId = id;
      btn.innerHTML =
        `<span class="tile-text">` +
          `<span class="tile-title">${sc.title}</span>` +
          `<span class="tile-audience">${sc.audience}</span>` +
        `</span>`;
      btn.addEventListener("click", () => runScenario(sc));
      tilesEl.appendChild(btn);
    } catch (e) { console.warn(`Failed to load scenario ${id}`, e); }
  }
}

function setActiveTile(id) {
  document.querySelectorAll(".scenario-tile").forEach(t => {
    t.classList.toggle("active", t.dataset.scenarioId === id);
  });
}

function showScenarioBanner(sc) {
  const el = document.getElementById("active-scenario-banner");
  if (!sc) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.querySelector(".banner-audience").textContent = sc.audience;
  el.querySelector(".banner-headline").textContent = sc.headline;
}

function clearReplay() { replayTimers.forEach(t => clearTimeout(t)); replayTimers = []; }
function fillInput(post, userPosts) {
  document.getElementById("post").value = post || "";
  document.getElementById("user_posts").value = (userPosts || []).join("\n");
}

async function runScenario(sc) {
  currentScenario = sc;
  setActiveTile(sc.id);
  showScenarioBanner(sc);
  document.getElementById("comparison-toggle").checked = !!sc.comparison_default;
  clearReplay();

  if (sc.onboard_city) {
    document.getElementById("onboard_city").value = sc.onboard_city;
    await runOnboard(sc.onboard_city, false);
  }

  for (const item of sc.posts) {
    const t = setTimeout(() => {
      fillInput(item.post, item.user_posts);
      runGeolocate();
    }, item.delay_ms || 0);
    replayTimers.push(t);
  }

  document.getElementById("replay-btn").classList.remove("hidden");
}

document.getElementById("replay-btn").addEventListener("click", () => {
  if (currentScenario) runScenario(currentScenario);
});

// ---------- geolocate ----------
function showDemoError(msg) {
  const el = document.getElementById("demo-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function clearDemoError() {
  document.getElementById("demo-error").classList.add("hidden");
}
function httpErrorMessage(status, txt) {
  if (status === 429) {
    return "Rate limit reached: the public demo allows a limited number of queries per IP per hour. " +
      "Wait a little, or run GeoLens locally (see the GitHub repo) to remove the cap.";
  }
  return `Request failed (${status}). ${txt || ""}`.trim();
}

async function runGeolocate() {
  const btn = document.getElementById("geolocate-btn");
  clearDemoError();
  const post = document.getElementById("post").value.trim() || null;
  const userHandle = document.getElementById("user_handle").value.trim() || null;
  const rawPosts = document.getElementById("user_posts").value.trim();
  const userPosts = rawPosts ? rawPosts.split(/\r?\n/).filter(Boolean) : null;
  if (!post && !userPosts && !userHandle) {
    showDemoError("Enter a post (or pick a scenario above) before running.");
    return;
  }
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = "Geolocating…";
  try {
    const ensembleMethod = document.getElementById("ensemble-method").value;
    const resp = await fetch("/geolocate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ post, user_handle: userHandle, user_posts: userPosts, k: 5, ensemble_method: ensembleMethod }),
    });
    if (!resp.ok) {
      showDemoError(httpErrorMessage(resp.status, await resp.text().catch(() => "")));
      return;
    }
    renderResults(await resp.json());
  } catch (e) {
    showDemoError(`Could not reach the server: ${e.message || e}. Check your connection and retry.`);
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.textContent = "Geolocate";
  }
}

document.getElementById("geolocate-btn").addEventListener("click", runGeolocate);
document.getElementById("comparison-toggle").addEventListener("change", () => {
  const last = window._lastResult;
  if (last) renderResults(last);
});
// Changing the fusion method re-runs (the consensus is recomputed server-side).
document.getElementById("ensemble-method").addEventListener("change", () => {
  if (window._lastResult) runGeolocate();
});

// ---------- rendering ----------
function renderResults(data) {
  window._lastResult = data;
  renderStubBanner(data.per_engine);
  renderEnsembles(data.ensembles || {});
  renderDisagreement(data.triangulation);
  renderEngines(data.per_engine);
  renderMap(data.ensembles || {}, data.triangulation);
  renderComparison(data.ensembles || {}, data.per_engine);
  document.getElementById("export-verdict-btn").classList.remove("hidden");
}

function renderStubBanner(perEngine) {
  const el = document.getElementById("stub-banner");
  const engines = Object.values(perEngine || {});
  const stubbed = engines.filter(p => p.mode === "stub").length;
  if (stubbed > 0) {
    el.classList.remove("hidden");
    el.textContent =
      `⚠ Demo / degraded mode: ${stubbed} of ${engines.length} engines are returning ` +
      `placeholder (stub) predictions, not live model output. Set API keys for real inference; ` +
      `do not treat this verdict as operational.`;
  } else {
    el.classList.add("hidden");
  }
}

// Export the full live verdict (per-engine votes, ensembles, disagreement
// score, and run manifest) as a JSON audit record.
document.getElementById("export-verdict-btn").addEventListener("click", () => {
  const data = window._lastResult;
  if (!data) return;
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "geolens-verdict.json";
  a.click();
  URL.revokeObjectURL(a.href);
});

function renderEnsembles(ensembles) {
  const wrap = document.getElementById("ensembles");
  const cards = document.getElementById("ensemble-cards");
  cards.innerHTML = "";
  const buckets = Object.keys(ensembles);
  if (!buckets.length) { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");

  for (const bucket of ["post", "user"]) {
    const e = ensembles[bucket];
    if (!e) continue;
    const card = document.createElement("div");
    card.className = `ensemble-card ${bucket}-bucket`;
    const conf = (e.consensus_confidence * 100).toFixed(1);
    const delta = e.delta_vs_best_single;
    const deltaPct = (delta * 100).toFixed(1);
    const deltaClass = delta > 0.005 ? "delta-pos" : (delta < -0.005 ? "delta-neg" : "delta-zero");
    const deltaSign = delta > 0 ? "+" : "";
    const deltaLine = `Δ vs best single (<i>${escapeHtml(ENGINE_META[e.best_single_engine]?.label || e.best_single_engine)}</i> picked <b>${escapeHtml(e.best_single_city)}</b>): <span class="${deltaClass}">${deltaSign}${deltaPct} pts</span>`;
    const altLine = e.differs_from_best_single
      ? `<br><i>Note: ensemble disagrees with best single engine.</i>`
      : "";
    const methodLabel = e.method === "rrf" ? "reciprocal rank fusion" : "weighted sum";
    card.innerHTML =
      `<div class="ensemble-bucket">${bucket}-level ensemble · ${e.contributing_engines.length} engines · ${methodLabel}</div>` +
      `<div class="ensemble-city">${escapeHtml(e.consensus_city)}</div>` +
      `<div class="ensemble-conf">${conf}% fused score</div>` +
      `<div class="ensemble-detail">${deltaLine}${altLine}</div>`;
    cards.appendChild(card);
  }
}

function renderDisagreement(tri) {
  const el = document.getElementById("disagreement-banner");
  if (!tri || !tri.disagreement_flag) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  const reason = tri.notes && tri.notes[0]
    ? tri.notes[0]
    : "Engines disagree on where this came from.";
  el.querySelector(".disagreement-text").textContent =
    "Geolocation inconsistency detected: " + reason;
}

function renderEngines(perEngine) {
  const section = document.getElementById("per-engine-section");
  section.classList.remove("hidden");
  document.getElementById("engine-count").textContent = Object.keys(perEngine).length;

  const buckets = { post: [], user: [] };
  for (const [name, p] of Object.entries(perEngine)) {
    const meta = ENGINE_META[name];
    if (!meta) continue;
    buckets[meta.granularity].push([name, p, meta]);
  }

  for (const bucket of ["post", "user"]) {
    const container = section.querySelector(`.engine-cards[data-bucket="${bucket}"]`);
    container.innerHTML = "";
    section.querySelector(`.bucket-count[data-bucket="${bucket}"]`).textContent = buckets[bucket].length;
    for (const [name, p, meta] of buckets[bucket]) {
      const card = document.createElement("div");
      card.className = "engine-card";
      card.dataset.family = meta.family;
      const conf = (p.confidence * 100).toFixed(1);
      const fillStyle = `width: ${Math.max(2, p.confidence * 100)}%`;
      const topkRows = p.top_k.slice(1, 4).map(([c, prob]) =>
        `<div class="topk-row"><span>${escapeHtml(c)}</span><span>${(prob*100).toFixed(0)}%</span></div>`
      ).join("");
      const modeBadge = p.mode === "stub"
        ? `<span class="mode-badge stub" title="placeholder output, not a live model">stub</span>`
        : `<span class="mode-badge real" title="live model output">real</span>`;
      const cityLine = p.abstain
        ? `<span class="abstain">no location signal</span>`
        : `${escapeHtml(p.city)} <span style="color:#667085;font-weight:400">(${conf}%)</span>`;
      const evidenceLine = p.evidence
        ? `<div class="evidence-line">${escapeHtml(p.evidence)}</div>` : "";
      card.innerHTML =
        `<div class="engine-head">` +
          `<span class="engine-name">${meta.label}</span>` +
          `<span class="engine-tag">${meta.tag}</span>` +
          modeBadge +
        `</div>` +
        `<div class="city-line">${cityLine}</div>` +
        `<div class="confidence-bar"><div class="fill" style="${fillStyle}"></div></div>` +
        (topkRows ? `<div class="topk">${topkRows}</div>` : "") +
        evidenceLine +
        `<div class="meta-line">${p.latency_ms.toFixed(0)} ms · ${escapeHtml(p.note)}</div>`;
      container.appendChild(card);
    }
  }
}

function renderMap(ensembles, tri) {
  markerLayer.clearLayers();
  const points = [];
  const missing = [];
  for (const bucket of ["post", "user"]) {
    const e = ensembles[bucket];
    if (!e) continue;
    const coords = CITY_COORDS[e.consensus_city];
    if (!coords) {
      // Don't drop the city silently; tell the operator it has no coordinate.
      missing.push(`${bucket}-level: ${e.consensus_city}`);
      continue;
    }
    points.push({ bucket, coords, city: e.consensus_city });
    const popup =
      `<b>${bucket}-level ensemble</b><br>` +
      `${escapeHtml(e.consensus_city)} (${(e.consensus_confidence*100).toFixed(1)}%, ${e.contributing_engines.length} engines)`;
    bucketMarker(bucket, coords, popup).addTo(markerLayer);
  }

  if (tri && tri.disagreement_flag && points.length >= 2) {
    L.polyline([points[0].coords, points[1].coords], {
      color: "#f97316", weight: 2, dashArray: "6 6",
    }).addTo(markerLayer);
  } else if (points.length >= 2 && points[0].city === points[1].city) {
    // both buckets converged on same city, halo it
    L.circle(points[0].coords, { radius: 30000, color: "#1d4ed8", fillOpacity: 0.08, weight: 1 })
      .addTo(markerLayer);
  }

  if (points.length) {
    const sameCity = points.length === 1 || points[0].city === points[1].city;
    if (sameCity) {
      map.setView(points[0].coords, 9);
    } else {
      map.fitBounds(points.map(p => p.coords), { padding: [40, 40] });
    }
  }

  const notice = document.getElementById("map-notice");
  if (missing.length) {
    notice.classList.remove("hidden");
    notice.textContent =
      "No map coordinate for " + missing.join(", ") +
      ". Onboard the city with a latitude/longitude to pin it.";
  } else {
    notice.classList.add("hidden");
  }

  // Text equivalent of the map for screen readers.
  const summaryEl = document.getElementById("map-summary");
  if (summaryEl) {
    const parts = points.map(p => `${p.bucket}-level: ${p.city}`);
    let txt = parts.length ? parts.join("; ") : "No mapped prediction.";
    if (tri && tri.disagreement_flag && tri.disagreement_km != null) {
      txt += `. Post and user consensus differ by about ${Math.round(tri.disagreement_km)} km (flagged).`;
    } else if (points.length >= 2 && points[0].city === points[1].city) {
      txt += ". Both buckets agree.";
    }
    summaryEl.textContent = txt;
  }
}

function renderComparison(ensembles, perEngine) {
  const wrap = document.getElementById("comparison");
  const rowsEl = document.getElementById("comparison-rows");
  if (!document.getElementById("comparison-toggle").checked) {
    wrap.classList.add("hidden"); return;
  }
  if (!Object.keys(ensembles).length) { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");
  rowsEl.innerHTML = "";

  for (const bucket of ["post", "user"]) {
    const e = ensembles[bucket];
    if (!e) continue;
    const bestSingle = perEngine[e.best_single_engine];
    const labelLine = document.createElement("div");
    labelLine.className = "comparison-bucket-label";
    labelLine.textContent = `${bucket}-level`;
    rowsEl.appendChild(labelLine);

    const pair = document.createElement("div");
    pair.className = "comparison-pair";
    const ensConf = (e.consensus_confidence * 100).toFixed(1);
    const bsConf = bestSingle ? (bestSingle.confidence * 100).toFixed(1) : "—";
    const bsLabel = ENGINE_META[e.best_single_engine]?.label || e.best_single_engine;
    pair.innerHTML =
      `<div class="col">` +
        `<h4>Best single engine: ${escapeHtml(bsLabel)}</h4>` +
        `<div class="city">${escapeHtml(e.best_single_city)} <span style="color:#667085;font-weight:400">(${bsConf}%)</span></div>` +
        `<div class="note">Someone using only this engine would see this answer.</div>` +
      `</div>` +
      `<div class="col unified">` +
        `<h4>Ensemble (${e.contributing_engines.length} engines)</h4>` +
        `<div class="city">${escapeHtml(e.consensus_city)} <span style="color:#667085;font-weight:400">(${ensConf}%)</span></div>` +
        `<div class="note">${e.differs_from_best_single ? "Ensemble disagrees with best single: different answer surfaced." : "Ensemble agrees with best single, with confidence " + (e.delta_vs_best_single >= 0 ? "raised" : "softened") + " by " + Math.abs(e.delta_vs_best_single * 100).toFixed(1) + " pts."}</div>` +
      `</div>`;
    rowsEl.appendChild(pair);
  }
}

// ---------- onboarding ----------
async function runOnboard(city, forceRefresh) {
  const btn = document.getElementById("onboard-btn");
  btn.disabled = true;
  btn.textContent = "Onboarding…";
  try {
    const resp = await fetch("/onboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city, force_refresh: !!forceRefresh }),
    });
    if (!resp.ok) { alert(`Onboard error ${resp.status}: ${await resp.text()}`); return; }
    populateOnboardCards(await resp.json());
  } finally {
    btn.disabled = false;
    btn.textContent = "Onboard";
  }
}

document.getElementById("onboard-btn").addEventListener("click", () => {
  const city = document.getElementById("onboard_city").value.trim();
  if (!city) { alert("Enter a city name first."); return; }
  runOnboard(city, false);
});
document.getElementById("onboard-regen-btn").addEventListener("click", () => {
  const city = document.getElementById("onboard_city").value.trim();
  if (!city) return;
  runOnboard(city, true);
});
document.getElementById("onboard-save-btn").addEventListener("click", async () => {
  clearDemoError();
  let profile;
  try {
    profile = readOnboardCards();
  } catch (e) {
    showDemoError(e.message || String(e));  // coordinate validation failed
    return;
  }
  try {
    const resp = await fetch("/onboard", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    });
    if (!resp.ok) { showDemoError(httpErrorMessage(resp.status, await resp.text().catch(() => ""))); return; }
    populateOnboardCards(await resp.json());
  } catch (e) {
    showDemoError(`Could not save the profile: ${e.message || e}.`);
  }
});

function populateOnboardCards(profile) {
  const cards = document.getElementById("onboard-cards");
  cards.classList.remove("hidden");
  const coordNote = (profile.lat == null || profile.lon == null)
    ? " · ⚠ no map coordinate; add one below so it pins on the map"
    : "";
  cards.querySelector(".onboard-source").textContent =
    `source: ${profile.source} · city: ${profile.name}${coordNote}`;
  const warnEl = document.getElementById("onboard-warnings");
  const warnings = profile.warnings || [];
  warnEl.innerHTML = warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
  warnEl.classList.toggle("hidden", warnings.length === 0);
  for (const field of ["aliases", "landmarks", "foods", "slang"]) {
    cards.querySelector(`textarea[data-field="${field}"]`).value = (profile[field] || []).join(", ");
  }
  cards.querySelector('textarea[data-field="notes"]').value = profile.notes || "";
  document.getElementById("onboard_lat").value = profile.lat == null ? "" : profile.lat;
  document.getElementById("onboard_lon").value = profile.lon == null ? "" : profile.lon;
  // Register the coordinate so the map can pin this city immediately.
  if (profile.lat != null && profile.lon != null) {
    CITY_COORDS[profile.name] = [profile.lat, profile.lon];
  }
}

function readOnboardCards() {
  const cards = document.getElementById("onboard-cards");
  // Accept ASCII and CJK fullwidth/ideographic separators.
  const split = sel =>
    cards.querySelector(`textarea[data-field="${sel}"]`).value
      .split(/[,，、]/).map(s => s.trim()).filter(Boolean);
  const num = id => {
    const v = document.getElementById(id).value.trim();
    return v === "" ? null : Number(v);
  };
  const lat = num("onboard_lat");
  const lon = num("onboard_lon");
  // Validate coordinate ranges and catch a likely lat/lon swap.
  if (lat != null && (Number.isNaN(lat) || lat < -90 || lat > 90)) {
    throw new Error(`Latitude ${lat} is out of range (-90 to 90).`);
  }
  if (lon != null && (Number.isNaN(lon) || lon < -180 || lon > 180)) {
    throw new Error(`Longitude ${lon} is out of range (-180 to 180).`);
  }
  if (lat != null && lon != null && Math.abs(lat) > 90) {
    throw new Error("Latitude looks like a longitude. Did you swap the fields?");
  }
  return {
    name: document.getElementById("onboard_city").value.trim(),
    aliases: split("aliases"),
    landmarks: split("landmarks"),
    foods: split("foods"),
    slang: split("slang"),
    notes: cards.querySelector('textarea[data-field="notes"]').value,
    lat: lat,
    lon: lon,
  };
}

// ---------- helpers ----------
function fmtKm(km) {
  if (km == null) return "—";
  if (km >= 1000) return `${(km / 1000).toFixed(1)}k km`;
  return `${km.toFixed(0)} km`;
}

function ciSpan(ci) {
  if (!ci || ci.length < 2) return "";
  return `<span class="ci">[${(ci[0]*100).toFixed(0)}, ${(ci[1]*100).toFixed(0)}]</span>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ---------- mode tabs (Live demo / Batch eval) ----------

const MODE_KEY = "geolens.mode";

function applyMode(mode) {
  document.querySelectorAll(".mode-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.mode === mode);
  });
  document.querySelectorAll("[data-mode-target]").forEach(el => {
    el.classList.toggle("hidden", el.dataset.modeTarget !== mode);
  });
  try { localStorage.setItem(MODE_KEY, mode); } catch (e) { /* ignore */ }
}

document.querySelectorAll(".mode-tab").forEach(t => {
  t.addEventListener("click", () => applyMode(t.dataset.mode));
});

// Restore last mode on load (default: demo)
let initialMode = "demo";
try { initialMode = localStorage.getItem(MODE_KEY) || "demo"; } catch (e) { /* ignore */ }
applyMode(initialMode);

// ---------- batch eval ----------

let _lastBatchResponse = null;
let _lastFileName = "results.csv";

const fileInput = document.getElementById("batch-file");
const runBtn = document.getElementById("batch-run-btn");
const rowCountEl = document.getElementById("batch-rowcount");
const statusEl = document.getElementById("batch-status");
function showBatchStatus(msg, isError) {
  statusEl.classList.remove("hidden", "error");
  if (isError) statusEl.classList.add("error");
  statusEl.textContent = msg;
}
const summaryWrap = document.getElementById("batch-summary");
const summaryMetaEl = summaryWrap.querySelector(".summary-meta");
const engineTbody = summaryWrap.querySelector(".summary-table:not(.ensemble-table) tbody");
const ensembleTbody = summaryWrap.querySelector(".summary-table.ensemble-table tbody");
const rowsWrap = document.getElementById("batch-rows");
const rowsTbody = rowsWrap.querySelector(".rows-table tbody");
const rowsCountEl = document.getElementById("rows-count");

async function previewCsv(file) {
  const text = await file.text();
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  const dataRows = Math.max(0, lines.length - 1);
  const hasGT = lines[0] && lines[0].toLowerCase().includes("ground_truth_city");
  rowCountEl.textContent = `${dataRows} rows · ${hasGT ? "with ground truth (will run /eval_csv)" : "no ground truth (will run /batch_predict_csv)"}`;
  runBtn.disabled = dataRows === 0;
  return { dataRows, hasGT };
}

fileInput.addEventListener("change", async () => {
  const f = fileInput.files[0];
  if (!f) { rowCountEl.textContent = ""; runBtn.disabled = true; return; }
  _lastFileName = f.name.replace(/\.csv$/i, "_results.csv");
  await previewCsv(f);
});

runBtn.addEventListener("click", async () => {
  const f = fileInput.files[0];
  if (!f) return;
  const { hasGT } = await previewCsv(f);
  const endpoint = hasGT ? "/eval_csv" : "/batch_predict_csv";
  runBtn.disabled = true;
  statusEl.classList.remove("hidden", "error");
  statusEl.textContent = `Running ${endpoint}…`;
  summaryWrap.classList.add("hidden");
  rowsWrap.classList.add("hidden");

  const fd = new FormData();
  fd.append("file", f);

  try {
    const resp = await fetch(endpoint, { method: "POST", body: fd });
    if (!resp.ok) {
      const text = await resp.text();
      statusEl.textContent = httpErrorMessage(resp.status, text);
      statusEl.classList.add("error");
      return;
    }
    const data = await resp.json();
    _lastBatchResponse = data;
    statusEl.textContent = `Done. ${data.rows.length} rows processed.`;
    renderBatchResults(data, hasGT);
  } catch (e) {
    statusEl.textContent = `Network error: ${e}`;
    statusEl.classList.add("error");
  } finally {
    runBtn.disabled = false;
  }
});

function renderRollup(rollup) {
  const wrap = document.getElementById("batch-rollup");
  const tbody = wrap.querySelector("tbody");
  if (!rollup || !rollup.length) { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");
  tbody.innerHTML = "";
  for (const c of rollup) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${escapeHtml(c.city)}</td>` +
      `<td>${c.post_count}</td>` +
      `<td>${c.user_count}</td>` +
      `<td><b>${c.post_count + c.user_count}</b></td>`;
    tbody.appendChild(tr);
  }
}

function renderBatchResults(data, withSummary) {
  renderRollup(data.rollup);
  if (withSummary && data.summary) {
    summaryWrap.classList.remove("hidden");
    const s = data.summary;
    const meta1 =
      `Total ${s.total_rows} rows · ${s.evaluated_rows} evaluated · ${s.ooc_rows} out-of-catalogue · ${s.error_rows} errors` +
      (s.catalogue_size ? ` · closed-set of N=${s.catalogue_size} candidate cities · Acc@1 shown with 95% Wilson CI` : "");
    const m = data.manifest;
    const meta2 = m && m.version
      ? `<div class="run-manifest">run: GeoLens v${escapeHtml(m.version)} · ${escapeHtml(m.ensemble_method)} fusion · k=${m.k} · catalogue ${m.catalogue_size} (sha ${escapeHtml(m.catalogue_sha)}) · ${escapeHtml(m.generated_at)}</div>`
      : "";
    let bannerLine = "";
    if (s.banner && s.banner.n_labelled > 0) {
      const b = s.banner;
      bannerLine =
        `<div class="banner-metrics">disagreement banner on ${b.n_labelled} labelled rows ` +
        `(${b.n_positive} should fire): precision ${(b.precision*100).toFixed(0)}%, ` +
        `recall ${(b.recall*100).toFixed(0)}% (TP ${b.true_positive} · FP ${b.false_positive} · FN ${b.false_negative})</div>`;
    }
    summaryMetaEl.innerHTML = escapeHtml(meta1) + meta2 + bannerLine;

    // Per-engine table, sorted by acc@1 desc
    engineTbody.innerHTML = "";
    const engines = Object.values(s.per_engine).sort((a, b) => b.acc_at_1 - a.acc_at_1);
    const bestAcc1 = engines[0]?.acc_at_1 ?? 0;
    const worstAcc1 = engines[engines.length - 1]?.acc_at_1 ?? 0;
    for (const m of engines) {
      const tr = document.createElement("tr");
      const cls = m.acc_at_1 === bestAcc1 ? "acc-best" : (m.acc_at_1 === worstAcc1 ? "acc-worst" : "");
      tr.innerHTML =
        `<td>${escapeHtml(m.name)}</td>` +
        `<td class="${cls}">${(m.acc_at_1*100).toFixed(1)}%${ciSpan(m.acc_at_1_ci)}</td>` +
        `<td>${(m.acc_at_5*100).toFixed(1)}%</td>` +
        `<td>${m.mean_rank.toFixed(1)}</td>` +
        `<td>${fmtKm(m.median_error_km)}</td>` +
        `<td>${(m.acc_at_161km*100).toFixed(1)}%</td>` +
        `<td>${m.median_latency_ms.toFixed(0)} ms</td>` +
        `<td>$${m.total_cost_usd.toFixed(4)}</td>` +
        `<td>${m.n_evaluated}</td>`;
      engineTbody.appendChild(tr);
    }

    ensembleTbody.innerHTML = "";
    for (const [g, m] of Object.entries(s.ensembles)) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${escapeHtml(g)}-level ensemble</td>` +
        `<td><b>${(m.acc_at_1*100).toFixed(1)}%</b>${ciSpan(m.acc_at_1_ci)}</td>` +
        `<td>${(m.acc_at_5*100).toFixed(1)}%</td>` +
        `<td>${m.mean_rank.toFixed(1)}</td>` +
        `<td>${fmtKm(m.median_error_km)}</td>` +
        `<td>${(m.acc_at_161km*100).toFixed(1)}%</td>` +
        `<td>${(m.differs_from_best_single_rate*100).toFixed(0)}%</td>` +
        `<td>${m.n_evaluated}</td>`;
      ensembleTbody.appendChild(tr);
    }

    renderPerBucket(s);
  } else {
    summaryWrap.classList.add("hidden");
  }

  rowsWrap.classList.remove("hidden");
  rowsCountEl.textContent = `(${data.rows.length} rows)`;
  rowsTbody.innerHTML = "";
  for (const r of data.rows) {
    const tr = document.createElement("tr");
    const postEns = r.ensembles?.post;
    const userEns = r.ensembles?.user;
    const postCity = postEns ? postEns.consensus_city : "—";
    const userCity = userEns ? userEns.consensus_city : "—";
    const gtMatch = (city) => r.ground_truth_city &&
      city.toLowerCase() === r.ground_truth_city.toLowerCase()
      ? `<span class="gt-match">${escapeHtml(city)}</span>` : escapeHtml(city);
    const tri = r.triangulation;
    const disagreement = tri && tri.disagreement_flag ? "⚠ post≠user" : "";
    tr.innerHTML =
      `<td>${escapeHtml(r.id)}</td>` +
      `<td>${escapeHtml(r.ground_truth_city || "—")}</td>` +
      `<td><span class="status-${r.status}">${escapeHtml(r.status)}</span></td>` +
      `<td>${gtMatch(postCity)}</td>` +
      `<td>${gtMatch(userCity)}</td>` +
      `<td>${disagreement}</td>`;
    rowsTbody.appendChild(tr);
  }
}

function renderPerBucket(s) {
  const wrap = document.querySelector(".per-bucket-wrap");
  const table = document.querySelector(".per-bucket-table");
  if (!wrap || !table) return;
  const buckets = Object.values(s.per_bucket || {});
  if (!buckets.length) { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");

  // Engine columns: union of engines seen, ordered like the per-engine table.
  const engineNames = Object.keys(s.per_engine || {});
  const head = table.querySelector("thead tr");
  head.innerHTML = `<th>Bucket</th><th>n</th>` +
    engineNames.map(n => `<th title="${escapeHtml(n)}">${escapeHtml(ENGINE_META[n]?.label || n)}</th>`).join("");

  const tbody = table.querySelector("tbody");
  tbody.innerHTML = "";
  buckets.sort((a, b) => a.bucket.localeCompare(b.bucket));
  for (const b of buckets) {
    const cells = engineNames.map(n => {
      const v = b.acc_at_1[n];
      if (v == null) return `<td>—</td>`;
      // colour-grade the cell so wins/losses pop in the matrix
      const hue = Math.round(v * 120); // 0=red .. 120=green
      return `<td style="background:hsl(${hue},70%,92%)">${(v*100).toFixed(0)}</td>`;
    }).join("");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><code>${escapeHtml(b.bucket)}</code></td><td>${b.n_rows}</td>${cells}`;
    tbody.appendChild(tr);
  }
}

document.getElementById("dl-results-csv").addEventListener("click", () => {
  if (!_lastBatchResponse) return;
  const csv = batchResultsToCsv(_lastBatchResponse);
  downloadFile(_lastFileName, csv, "text/csv");
});

document.getElementById("dl-summary-tex").addEventListener("click", async () => {
  if (!_lastBatchResponse?.summary) { showBatchStatus("Run a batch with a ground-truth column first.", true); return; }
  const tex = batchSummaryToLatex(_lastBatchResponse.summary, _lastBatchResponse.manifest);
  try {
    await navigator.clipboard.writeText(tex);
    showBatchStatus("LaTeX table copied to clipboard.", false);
  } catch (e) {
    downloadFile("summary.tex", tex, "text/plain");
  }
});

document.getElementById("dl-geojson").addEventListener("click", () => {
  if (!_lastBatchResponse) return;
  downloadFile("geolens-predictions.geojson", batchResultsToGeoJSON(_lastBatchResponse), "application/geo+json");
});

// Reproducibility: the run manifest (model versions, catalogue hash, k, fusion,
// timestamp) as a standalone JSON, so a reported number is attributable.
document.getElementById("dl-manifest").addEventListener("click", () => {
  if (!_lastBatchResponse?.manifest) return;
  downloadFile("geolens-run-manifest.json", JSON.stringify(_lastBatchResponse.manifest, null, 2), "application/json");
});

// Great-circle distance (km) between two [lat, lon] points, for error analysis.
function haversineKm(a, b) {
  const R = 6371.0088, toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]), dLon = toRad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// GeoJSON FeatureCollection (WGS84 / CRS84) of the per-row ensemble
// predictions, so a verdict drops straight into QGIS/PostGIS.
function batchResultsToGeoJSON(data) {
  const features = [];
  for (const r of data.rows) {
    for (const bucket of ["post", "user"]) {
      const e = r.ensembles?.[bucket];
      if (!e) continue;
      const coords = CITY_COORDS[e.consensus_city];
      if (!coords) continue;
      // Distance error vs. ground truth (when known), so a GIS user can
      // symbolise or filter predictions by how far off they are.
      const gt = r.ground_truth_city ? CITY_COORDS[r.ground_truth_city] : null;
      const errorKm = gt ? Math.round(haversineKm(coords, gt)) : null;
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [coords[1], coords[0]] },  // GeoJSON is [lon, lat]
        properties: {
          id: r.id, bucket, city: e.consensus_city,
          confidence: e.consensus_confidence,
          ground_truth_city: r.ground_truth_city || null,
          error_km: errorKm,
          within_161km: errorKm === null ? null : errorKm <= 161,
          disagreement_flag: r.triangulation?.disagreement_flag ?? null,
          disagreement_km: r.triangulation?.disagreement_km ?? null,
        },
      });
    }
  }
  return JSON.stringify({
    type: "FeatureCollection",
    crs: { type: "name", properties: { name: "urn:ogc:def:crs:OGC:1.3:CRS84" } },
    features,
  }, null, 2);
}

function batchResultsToCsv(data) {
  // Per-engine columns let someone do error analysis, not just read the
  // ensemble verdict. Engine set is taken from the first row that ran.
  const engineNames = [];
  for (const r of data.rows) {
    if (r.per_engine && Object.keys(r.per_engine).length) {
      engineNames.push(...Object.keys(r.per_engine));
      break;
    }
  }
  const eq = (a, b) => a && b && a.toLowerCase() === b.toLowerCase();
  const cols = ["id", "status", "ground_truth_city",
    "post_ensemble_city", "post_ensemble_conf",
    "user_ensemble_city", "user_ensemble_conf",
    "disagreement_flag", "disagreement_km", "disagreement_score"];
  for (const n of engineNames) { cols.push(`${n}_top1`, `${n}_correct`); }
  const lines = [cols.join(",")];
  for (const r of data.rows) {
    const pe = r.ensembles?.post;
    const ue = r.ensembles?.user;
    const tri = r.triangulation;
    const row = [
      csvCell(r.id),
      csvCell(r.status),
      csvCell(r.ground_truth_city || ""),
      csvCell(pe?.consensus_city || ""),
      pe ? pe.consensus_confidence.toFixed(4) : "",
      csvCell(ue?.consensus_city || ""),
      ue ? ue.consensus_confidence.toFixed(4) : "",
      tri ? (tri.disagreement_flag ? "1" : "0") : "",
      tri && tri.disagreement_km != null ? tri.disagreement_km.toFixed(0) : "",
      tri ? (tri.disagreement_score ?? 0).toFixed(3) : "",
    ];
    for (const n of engineNames) {
      const p = r.per_engine?.[n];
      row.push(csvCell(p?.city || ""));
      row.push(p && r.ground_truth_city ? (eq(p.city, r.ground_truth_city) ? "1" : "0") : "");
    }
    lines.push(row.join(","));
  }
  return lines.join("\n");
}

function csvCell(s) {
  if (s == null) return "";
  const t = String(s);
  return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
}

function batchSummaryToLatex(s, manifest) {
  const lines = [];
  if (manifest && manifest.version) {
    lines.push(`% GeoLens v${manifest.version} · ${manifest.ensemble_method} fusion · k=${manifest.k} · catalogue ${manifest.catalogue_size} (sha ${manifest.catalogue_sha}) · ${manifest.generated_at}`);
  }
  lines.push("\\begin{tabular}{lrrrrrr}");
  lines.push("\\toprule");
  lines.push("Engine & Acc@1 (95\\% CI) & Acc@5 & Mean rank & Median km err. & Acc@161km & N \\\\");
  lines.push("\\midrule");
  const ci = m => `[${(m.acc_at_1_ci[0]*100).toFixed(0)}, ${(m.acc_at_1_ci[1]*100).toFixed(0)}]`;
  const engines = Object.values(s.per_engine).sort((a, b) => b.acc_at_1 - a.acc_at_1);
  for (const m of engines) {
    lines.push(`${escapeLatex(m.name)} & ${(m.acc_at_1*100).toFixed(1)} {\\scriptsize ${ci(m)}} & ${(m.acc_at_5*100).toFixed(1)} & ${m.mean_rank.toFixed(2)} & ${m.median_error_km.toFixed(0)} & ${(m.acc_at_161km*100).toFixed(1)} & ${m.n_evaluated} \\\\`);
  }
  lines.push("\\midrule");
  for (const [g, m] of Object.entries(s.ensembles)) {
    lines.push(`Ensemble (${g}) & \\textbf{${(m.acc_at_1*100).toFixed(1)}} & ${(m.acc_at_5*100).toFixed(1)} & ${m.mean_rank.toFixed(2)} & ${m.median_error_km.toFixed(0)} & ${(m.acc_at_161km*100).toFixed(1)} & ${m.n_evaluated} \\\\`);
  }
  lines.push("\\bottomrule");
  lines.push("\\end{tabular}");
  return lines.join("\n");
}

function escapeLatex(s) {
  return String(s).replace(/[_$%&#{}~^\\]/g, c => "\\" + c);
}

function downloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------- boot ----------
loadScenarios();
