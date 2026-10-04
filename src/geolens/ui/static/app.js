// ---------- city coordinates (for the map) ----------
// Mirrors geolens/engines/_coords.py. A catalogue city missing here cannot
// be pinned, so tests/test_coords_parity.py keeps the two tables equal.
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
  "Los Angeles": [34.0522, -118.2437],
  "Bandung": [-6.9039, 107.6186],
  "Istanbul": [41.0138, 28.9497],
  "Chicago": [41.85, -87.65],
  "Sao Paulo": [-23.5475, -46.6361],
  "Rio de Janeiro": [-22.9028, -43.2075],
  "Denpasar": [-8.65, 115.2167],
  "Surabaya": [-7.2492, 112.7508],
  "Atlanta": [33.749, -84.388],
  "Medan": [3.5833, 98.6667],
  "Dallas": [32.7831, -96.8067],
  "Miami": [25.7743, -80.1937],
  "Izmir": [38.4127, 27.1384],
  "Makassar": [-5.14, 119.4221],
  "Las Vegas": [36.175, -115.1372],
  "Lagos": [6.4531, 3.3958],
  "Yogyakarta": [-7.7828, 110.3608],
  "Austin": [30.2672, -97.7431],
  "Malang": [-7.9797, 112.6304],
  "San Diego": [32.7153, -117.1573],
  "Dublin": [53.3331, -6.2489],
  "San Antonio": [29.4241, -98.4936],
  "Houston": [29.7633, -95.3633],
  "Buenos Aires": [-34.6131, -58.3772],
  "Cleveland": [41.4995, -81.6954],
  "Philadelphia": [39.9523, -75.1638],
  "Curitiba": [-25.4278, -49.2731],
  "Charlotte": [35.2271, -80.8431],
};

// ---------- the engine roster ----------
// Labels, families, granularities and chips come from GET /instance, which
// serves geolens/engines/registry.py, so registering an engine needs no edit
// here. An engine the roster does not describe is still rendered, from its
// name and a granularity read off its suffix.
let ENGINE_ROSTER = {};
let LOCAL_ENGINE_NAMES = [];

function engineMeta(name) {
  const known = ENGINE_ROSTER[name];
  if (known) return known;
  return {
    label: name,
    family: name,
    granularity: /_user$/.test(name) ? "user" : "post",
    tag: "",
    tagTitle: "",
    callsAThirdParty: false,
  };
}

function rosterNames() { return Object.keys(ENGINE_ROSTER); }

// How many registered engines answer at one level.
function rosterCount(granularity) {
  return rosterNames().filter(n => engineMeta(n).granularity === granularity).length;
}

function setRoster(engines, localNames) {
  ENGINE_ROSTER = {};
  for (const [name, info] of Object.entries(engines || {})) {
    ENGINE_ROSTER[name] = {
      label: info.label || name,
      family: info.family || name,
      granularity: info.granularity === "user" ? "user" : "post",
      tag: info.tag || "",
      tagTitle: info.tag_title || "",
      callsAThirdParty: Boolean(info.calls_a_third_party),
    };
  }
  LOCAL_ENGINE_NAMES = Array.isArray(localNames) && localNames.length
    ? localNames.slice()
    : rosterNames().filter(n => !ENGINE_ROSTER[n].callsAThirdParty);
}

// ---------- place coordinates and identifiers ----------
// A marker's coordinate comes from the server: every response carries
// `places` and `place_coordinates` for the places it names, and /catalogue
// carries one for every candidate. CITY_COORDS above is only the fallback.
const SERVER_COORDS = {};
// place_id -> the server's entry, and name -> place_id. An export joins on
// the identifier rather than on a free-text place name.
const PLACE_BY_ID = {};
const PLACE_ID_BY_NAME = {};

function rememberCoords(table) {
  for (const [name, coords] of Object.entries(table || {})) {
    if (Array.isArray(coords) && coords.length === 2) SERVER_COORDS[name] = coords;
  }
}

function rememberPlaces(places) {
  for (const p of places || []) {
    if (!p || !p.place_id) continue;
    PLACE_BY_ID[p.place_id] = p;
    if (p.name) {
      PLACE_ID_BY_NAME[p.name] = p.place_id;
      if (p.lat != null && p.lon != null) SERVER_COORDS[p.name] = [p.lat, p.lon];
    }
  }
}

function coordFor(name) {
  if (!name) return null;
  return SERVER_COORDS[name] || CITY_COORDS[name] || null;
}

function placeIdFor(name) {
  if (!name) return null;
  return PLACE_ID_BY_NAME[name] || null;
}

// One decimal, everywhere a distance is written out.
function round1(km) {
  if (km == null || Number.isNaN(km)) return null;
  return Math.round(km * 10) / 10;
}

// ---------- a recorded run ----------
// `?replay=<scenario id>` renders a committed scenario check instead of
// querying. replay.js does the rendering; this is what the rest of the page
// checks to keep off the network.
function recordedRunId() {
  try { return new URLSearchParams(window.location.search).get("replay") || ""; }
  catch (e) { return ""; }
}

const RECORDED_RUN = recordedRunId();

// ---------- info notes ----------
// A circled "i" after a label or a figure, opening one short explanation
// under the line it belongs to. The listeners are delegated to the document,
// so a note this file writes into a card behaves like one written into
// index.html. Several notes may be open at once.

let _infoSeq = 0;

// The button and the body for one note, as markup, so generated text carries
// the same structure as the static text.
function infoNote(id, label, bodyHtml) {
  const noteId = id || `i-note-${++_infoSeq}`;
  return {
    id: noteId,
    button:
      `<button type="button" class="info-btn" aria-expanded="false" ` +
      `aria-controls="${escapeHtml(noteId)}">` +
        `<span aria-hidden="true">i</span>` +
        `<span class="visually-hidden">About: ${escapeHtml(label)}</span>` +
      `</button>`,
    body: `<span class="info-body hidden" id="${escapeHtml(noteId)}">${bodyHtml}</span>`,
  };
}

function infoBodyOf(button) {
  const id = button.getAttribute("aria-controls");
  return id ? document.getElementById(id) : null;
}

function setInfoOpen(button, open) {
  const body = infoBodyOf(button);
  if (!body) return;
  button.setAttribute("aria-expanded", open ? "true" : "false");
  body.classList.toggle("hidden", !open);
}

// A button answers Enter and Space with a click of its own, so one click
// listener covers the pointer and the keyboard.
document.addEventListener("click", (event) => {
  const button = event.target.closest && event.target.closest(".info-btn");
  if (!button) return;
  event.preventDefault();
  setInfoOpen(button, button.getAttribute("aria-expanded") !== "true");
});

// Escape closes the note the reader is in, and focus returns to its button.
// A body carries no tabindex of its own, so the second branch is the one a
// reader reaches by tabbing to a link inside an open note.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const from = event.target;
  if (!from || !from.closest) return;
  let button = from.closest(".info-btn");
  if (!button) {
    const body = from.closest(".info-body");
    if (!body || !body.id) return;
    button = document.querySelector(`.info-btn[aria-controls="${body.id}"]`);
  }
  if (!button || button.getAttribute("aria-expanded") !== "true") return;
  setInfoOpen(button, false);
  button.focus();
});

// ---------- instance settings ----------
// What this deployment enforces, which engines it registers, and what the
// verification flag measured. Read once at boot; every call that needs the
// roster waits for it.
let INSTANCE = null;
let _instancePromise = null;

async function loadInstance() {
  try {
    const r = await fetch("/instance");
    if (!r.ok) return;
    INSTANCE = await r.json();
  } catch (e) { console.warn("Instance settings unavailable", e); return; }
  setRoster(INSTANCE.engines, INSTANCE.local_engines);
  applyInstanceSettings();
}

function ensureInstance() {
  if (!_instancePromise) _instancePromise = loadInstance();
  return _instancePromise;
}

function applyInstanceSettings() {
  // Whether the region hint is required is a field marker beside the label;
  // what the hint is for is in the note behind the label's icon.
  const required = document.getElementById("onboard-region-required");
  if (required) required.classList.toggle("hidden", !INSTANCE.require_region);
  // The line above the form reads the same setting as the marker and the
  // hint, so the three cannot disagree about what has to be typed.
  const taskText = document.getElementById("onboard-task-text");
  if (taskText) {
    taskText.textContent = INSTANCE.require_region
      ? "Add a place that the catalogue lacks, from its name and its country or region."
      : "Add a place that the catalogue lacks, from its name, and its country " +
        "or region where the name is ambiguous.";
  }
  const regionNote = document.getElementById("onboard-region-hint");
  if (regionNote) {
    regionNote.textContent =
      (INSTANCE.require_region
        ? "Required on this instance. "
        : "Optional on this instance. ") +
      "Used in the drafting prompt, and to check the drafted coordinate at " +
      "country scale.";
  }
  // The headline says what the rate counts, because "false alarms" on its
  // own reads as the share of flags that are wrong, which is a different
  // quantity. The sentence the server composed is the note behind it.
  const reference = INSTANCE.flag_reference || {};
  const errorRate = document.getElementById("flag-error-rate");
  if (errorRate && reference.sentence) {
    const percent = Math.round((reference.false_alarm_rate || 0) * 100);
    const note = infoNote("i-flag-rate", "the false-alarm rate",
                          escapeHtml(reference.sentence));
    errorRate.innerHTML =
      `Flagged ${percent}% of home-consistent accounts on WNUT-2016.` +
      note.button + note.body;
  }
  // How the distances on this page are measured, said once, inside the
  // flag's expandable note.
  const method = document.getElementById("flag-distance-method");
  if (method && INSTANCE.distance_method) {
    method.textContent =
      `Every distance here, including the ${INSTANCE.flag_radius_km_default} km ` +
      `default radius, is measured as ${INSTANCE.distance_method}`;
  }
  // The caps this instance enforces on the bulk tab, and on the two boxes.
  const limits = document.getElementById("batch-limits");
  const limitsNote = document.getElementById("i-bulk-limits");
  if (limits) {
    const perHour = ((INSTANCE.limits || {}).batches || {}).per_hour || 0;
    limits.textContent = perHour > 0
      ? `Up to ${INSTANCE.max_batch_rows} rows, ${perHour} runs per hour.`
      : `Up to ${INSTANCE.max_batch_rows} rows.`;
    if (limitsNote) {
      limitsNote.textContent = perHour > 0
        ? "These caps are set by this instance, and the per-hour limit counts " +
          "per IP address."
        : "This cap is set by this instance, which sets no limit on how many " +
          "runs one address may make.";
    }
  }
  const tagline = document.getElementById("tagline-engines");
  if (tagline && rosterNames().length) tagline.textContent = String(rosterNames().length);
  // How many engines answer each task, counted off the same roster as the
  // tagline rather than written into the page.
  if (rosterNames().length) {
    const counts = {
      "task-engines-post": rosterCount("post"),
      "task-engines-verify-post": rosterCount("post"),
      "task-engines-user": rosterCount("user"),
      "task-engines-verify-user": rosterCount("user"),
    };
    for (const [id, count] of Object.entries(counts)) {
      const el = document.getElementById(id);
      if (el) el.textContent = String(count);
    }
    // The count says how many answer, and the names say which, in the
    // labels the cards and the footer use.
    for (const [id, level] of Object.entries({
      "task-names-post": "post",
      "task-names-verify-post": "post",
      "task-names-user": "user",
      "task-names-verify-user": "user",
    })) {
      const el = document.getElementById(id);
      if (el) el.textContent = taskEngineNames(level);
    }
  }
  renderFooterEngines();
  showQuota();
  showSpendNotice(INSTANCE.spend);
  updateCharCounts();
}

// The engines that answer one level, named as the cards name them. The
// level is already in the sentence, so the suffix the label carries for it
// comes off, as it does in the footer.
function taskEngineNames(granularity) {
  const names = [...new Set(rosterNames()
    .filter(n => engineMeta(n).granularity === granularity)
    .map(n => engineMeta(n).label.replace(/\s*\((post|user)\)$/, "")))];
  if (!names.length) return "";
  const last = names.pop();
  return names.length ? ` (${names.join(", ")} and ${last})` : ` (${last})`;
}

// The footer names whatever the instance registers, grouped by the chip each
// engine carries.
function renderFooterEngines() {
  const frozen = document.getElementById("footer-frozen");
  const baselines = document.getElementById("footer-baselines");
  if (!frozen || !baselines) return;
  const seen = new Set();
  // Plain names separated by a middle dot: nine bordered chips read as a tag
  // cloud, which is not what a list of engine names is.
  const named = (label) =>
    `<span class="engine-pill">${escapeHtml(label)}</span>`;
  const rows = { frozen: [], other: [] };
  for (const name of rosterNames()) {
    const meta = ENGINE_ROSTER[name];
    // One pill per published method, not one per granularity.
    const key = `${meta.family}|${meta.tag}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const label = meta.label.replace(/\s*\((post|user)\)$/, "");
    rows[meta.tag === "frozen encoder" ? "frozen" : "other"].push(named(label));
  }
  frozen.innerHTML = rows.frozen.join(" · ");
  baselines.innerHTML = rows.other.join(" · ");
}

// ---------- the links in the header ----------
// The recording's address and its label, in one place.
const VIDEO_LINK = {
  url: "https://youtu.be/ArKLLrxWXCk",
  label: "Video",
};

{
  const videoEl = document.getElementById("video-link");
  if (videoEl) {
    videoEl.href = VIDEO_LINK.url;
    videoEl.textContent = VIDEO_LINK.label;
  }
}

// ---------- map ----------
// Leaflet is served from this instance. With the script or the tile host
// unreachable the map is left out and the predictions are listed with their
// coordinates instead.
const MAP_READY = typeof L !== "undefined";
let map = null;
let markerLayer = null;
let radiusLayer = null;
let tilesFailed = false;

// The drafted coordinate's pin, declared here because the legend switches it
// on and off. It is drawn in the onboarding section below.
let draftMarker = null;
let draftCircle = null;

// How many predictions the last render put on the map. The legend is shown
// when there is something on the map to explain, and the draft pin counts.
let _mappedPoints = 0;
// Which kinds of thing the last render drew. The legend lists only these,
// so it never names a marker the reader cannot find.
let _legendKeys = new Set();

// The coordinate list sits under the verification flag while the map works,
// and moves up to just under the map when the map or its tiles cannot be
// loaded, which is the case it exists for.
let _mapListMoved = false;

function moveMapListAboveTheFlag() {
  if (_mapListMoved) return;
  const list = document.getElementById("map-list-wrap");
  const anchor = document.getElementById("near-miss-note");
  if (!list || !anchor || !anchor.parentNode) return;
  anchor.parentNode.insertBefore(list, anchor);
  _mapListMoved = true;
}

function updateLegend() {
  const legend = document.getElementById("map-legend");
  if (!legend) return;
  for (const item of legend.querySelectorAll(".legend-item")) {
    const key = item.dataset.legend;
    const shown = key === "draft" ? Boolean(draftMarker) : _legendKeys.has(key);
    item.classList.toggle("hidden", !shown);
  }
  legend.classList.toggle("hidden", !markerLayer || (!_mappedPoints && !draftMarker));
}

function showMapFallbackNote(text) {
  const el = document.getElementById("map-notice");
  if (!el) return;
  el.classList.remove("hidden");
  el.textContent = text;
  // With no map the list of coordinates is the result, so it goes above the
  // verification flag rather than under it.
  moveMapListAboveTheFlag();
}

if (MAP_READY) {
  map = L.map("map").setView([1.35, 103.82], 4);
  // The tiles come from openstreetmap.org, which the privacy notice says.
  // The attribution names the contributors and links to the licence page.
  const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" ' +
      'rel="noopener">OpenStreetMap</a> contributors',
    maxZoom: 18,
  });
  tiles.on("tileerror", () => {
    if (tilesFailed) return;
    tilesFailed = true;
    showMapFallbackNote("The map tiles could not be loaded, so the map is blank. " +
      "The predictions and their coordinates are listed below.");
  });
  tiles.addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  radiusLayer = L.layerGroup().addTo(map);
  // The flag radius is drawn only when it is big enough to see, so it has to
  // be reconsidered whenever the reader zooms.
  map.on("zoomend", () => drawFlagRadius());
} else {
  const mapEl = document.getElementById("map");
  if (mapEl) mapEl.classList.add("hidden");
  showMapFallbackNote("The map could not be loaded, so the predictions and their " +
    "coordinates are listed below instead.");
}

// ---------- geodesy for the map ----------
// Every path, circle and fitted view here is computed on the sphere, so a
// drawn line is the path its printed distance describes.

const EARTH_RADIUS_KM = 6371.0088;
const ARC_SEGMENTS = 64;
const MIN_RADIUS_PX = 15;

const toRad = (d) => (d * Math.PI) / 180;
const toDeg = (r) => (r * 180) / Math.PI;

// Great-circle distance (km) between two [lat, lon] points. The same
// haversine on the same sphere the server uses.
function haversineKm(a, b) {
  const dLat = toRad(b[0] - a[0]), dLon = toRad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

// The short way round, sampled: every point is on the great circle.
function greatCircleArc(a, b, segments = ARC_SEGMENTS) {
  const lat1 = toRad(a[0]), lon1 = toRad(a[1]);
  const lat2 = toRad(b[0]), lon2 = toRad(b[1]);
  const d = 2 * Math.asin(Math.min(1, Math.sqrt(
    Math.sin((lat2 - lat1) / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2)));
  if (!(d > 1e-9)) return [[a[0], a[1]], [b[0], b[1]]];
  const points = [];
  for (let i = 0; i <= segments; i++) {
    const f = i / segments;
    const A = Math.sin((1 - f) * d) / Math.sin(d);
    const B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
    const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
    const z = A * Math.sin(lat1) + B * Math.sin(lat2);
    points.push([toDeg(Math.atan2(z, Math.hypot(x, y))), toDeg(Math.atan2(y, x))]);
  }
  return points;
}

// One longitude frame for everything drawn: each longitude is wrapped to
// within half a turn of one reference, so a path across the antimeridian
// stays continuous and the fitted view centres on it. Leaflet repeats the
// basemap past the antimeridian, so a longitude of 242 renders as 118 west.
// The coordinate list and every export keep the real coordinate.
function wrapForFit(coords, refLon) {
  if (!coords.length) return coords;
  const ref = refLon == null ? coords[0][1] : refLon;
  return coords.map(([lat, lon]) => {
    let wrapped = lon;
    while (wrapped - ref > 180) wrapped -= 360;
    while (wrapped - ref < -180) wrapped += 360;
    return [lat, wrapped];
  });
}

function wrapOne(coords, refLon) {
  return wrapForFit([coords], refLon)[0];
}

// A point `km` away from `center` on the bearing `bearingDeg`, on the same
// sphere as every other distance here.
function geodesicPoint(center, km, bearingDeg) {
  const lat1 = toRad(center[0]), lon1 = toRad(center[1]);
  const dr = km / EARTH_RADIUS_KM, brg = toRad(bearingDeg);
  const lat = Math.asin(Math.sin(lat1) * Math.cos(dr) +
    Math.cos(lat1) * Math.sin(dr) * Math.cos(brg));
  const lon = lon1 + Math.atan2(Math.sin(brg) * Math.sin(dr) * Math.cos(lat1),
    Math.cos(dr) - Math.sin(lat1) * Math.sin(lat));
  return [toDeg(lat), toDeg(lon)];
}

// A circle of constant great-circle distance from `center`, as a polygon.
function geodesicCircle(center, km, segments = 72) {
  const ring = [];
  for (let i = 0; i <= segments; i++) {
    ring.push(geodesicPoint(center, km, (i * 360) / segments));
  }
  return ring;
}

function radiusPixels(center, km) {
  if (!map) return 0;
  const north = geodesicPoint(center, km, 0);
  const a = map.latLngToContainerPoint(L.latLng(center[0], center[1]));
  const b = map.latLngToContainerPoint(L.latLng(north[0], north[1]));
  return Math.abs(a.y - b.y);
}

// What the flag radius is drawn around, and how big. Kept so a zoom can
// redraw or drop it.
let _radiusRequest = null;

function drawFlagRadius() {
  if (!radiusLayer) return;
  radiusLayer.clearLayers();
  _legendKeys.delete("radius");
  if (!_radiusRequest) { updateLegend(); return; }
  const { center, km, refLon } = _radiusRequest;
  // Below about 15 px the circle is a smudge on the marker.
  if (radiusPixels(center, km) < MIN_RADIUS_PX) { updateLegend(); return; }
  L.polygon(wrapForFit(geodesicCircle(center, km), refLon), {
    color: "#1a5c7a", fillColor: "#1a5c7a", fillOpacity: 0.06, weight: 1,
  }).addTo(radiusLayer);
  _legendKeys.add("radius");
  updateLegend();
}

// The second marker at one coordinate is nudged by this much, so it is not
// hidden under the first.
const MARKER_OFFSET_PX = 10;
// A 24 by 24 hit area around the glyph, so a thumb can reach it.
const MARKER_BOX_PX = 24;

function bucketMarker(bucket, coords, popup, kind = "fused", overlapIndex = 0) {
  // Glyph (P/U) so the two markers are distinguishable without relying on
  // colour or shape alone (accessibility / colour-blind users). The fused
  // prediction is a filled square or circle; the per-level consensus, which
  // is what the verification flag compares, is a hollow diamond.
  const glyph = bucket === "post" ? "P" : "U";
  const kindLabel = kind === "consensus"
    ? `${bucket}-level consensus (what the verification flag compares)`
    : `${bucket}-level fused prediction`;
  const title = kind === "muted"
    ? `${bucket}-level fused prediction, from a text naming no catalogue place`
    : kindLabel;
  // The glyph is hidden from assistive technology so the marker's name is
  // the sentence above rather than the bare letter "P".
  const html =
    `<div class="marker-hit">` +
      `<div class="engine-marker ${bucket} ${kind}" aria-hidden="true"><span>${glyph}</span></div>` +
    `</div>`;
  const shift = overlapIndex * MARKER_OFFSET_PX;
  const half = MARKER_BOX_PX / 2;
  const icon = L.divIcon({
    html, className: "",
    iconSize: [MARKER_BOX_PX, MARKER_BOX_PX],
    iconAnchor: [half - shift, half + shift],
  });
  return L.marker(coords, { icon, title, alt: title }).bindPopup(popup);
}

// ---------- scenario tiles ----------
let currentScenario = null;
let replayTimers = [];
// Why the last request did not go through, as a clause a stopped preset can
// print.
let _lastFailureClause = "";

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
      const wrap = document.createElement("div");
      wrap.className = "scenario-tile-wrap";
      const btn = document.createElement("button");
      btn.className = "scenario-tile";
      btn.dataset.scenarioId = id;
      btn.setAttribute("aria-pressed", "false");
      btn.innerHTML =
        `<span class="tile-text">` +
          `<span class="tile-title">${escapeHtml(sc.title)}</span>` +
          `<span class="tile-subtitle">${escapeHtml(sc.subtitle)}</span>` +
          // What the tile's own state is, in words, so the fill colour and
          // aria-pressed are not the only things that carry it.
          `<span class="tile-tag hidden"></span>` +
        `</span>`;
      btn.addEventListener("click", () => {
        if (RECORDED_RUN) {
          window.location.search = `?replay=${encodeURIComponent(id)}`;
          return;
        }
        runScenario(sc);
      });
      wrap.appendChild(btn);
      // The recorded run needs no network beyond this page.
      const recorded = document.createElement("a");
      recorded.className = "tile-replay";
      recorded.href = `?replay=${encodeURIComponent(id)}`;
      recorded.textContent = "Show recorded run";
      wrap.appendChild(recorded);
      tilesEl.appendChild(wrap);
    } catch (e) { console.warn(`Failed to load scenario ${id}`, e); }
  }
  // A recorded run loads its scenario before this listing is on the page, so
  // the tile takes the state it should already be carrying.
  if (currentScenario) {
    setActiveTile(currentScenario.id);
    setTileTag(_scenarioState);
  }
}

function setActiveTile(id) {
  document.querySelectorAll(".scenario-tile").forEach(t => {
    const on = t.dataset.scenarioId === id;
    t.classList.toggle("active", on);
    t.setAttribute("aria-pressed", on ? "true" : "false");
  });
  // A tile that is no longer the loaded one carries no state either.
  setTileTag("");
}

// The state word on the tile of the loaded scenario: "in progress",
// "finished" or "stopped", the same word the bar's label uses. With no tile
// pressed there is nothing to tag.
function setTileTag(text) {
  document.querySelectorAll(".scenario-tile").forEach(t => {
    const tag = t.querySelector(".tile-tag");
    if (!tag) return;
    const on = Boolean(text) && t.getAttribute("aria-pressed") === "true";
    tag.textContent = on ? text : "";
    tag.classList.toggle("hidden", !on);
  });
}

function showScenarioBanner(sc) {
  const el = document.getElementById("active-scenario-banner");
  if (!sc) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  // The subtitle is already on screen, in the tile this banner belongs to.
  el.querySelector(".banner-headline").textContent = sc.headline;
  updateScenarioPlaceholderLine();
}

function clearReplay() { replayTimers.forEach(t => clearTimeout(t)); replayTimers = []; }

// The language of the text now in the input boxes, so a screen reader reads
// the Indonesian crisis posts with Indonesian phonemes.
function setInputLanguage(lang) {
  for (const id of ["post", "user_posts"]) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (lang && lang !== "en") el.setAttribute("lang", lang);
    else el.removeAttribute("lang");
  }
}

function fillInput(post, userPosts, lang) {
  document.getElementById("post").value = post || "";
  document.getElementById("user_posts").value = (userPosts || []).join("\n");
  setInputLanguage(lang);
  updateCharCounts();
}

// Once the visitor types, the text in the box is no longer the scenario's,
// so the scenario's language claim, its highlighted tile and its blurb all
// come off on the first keystroke.
for (const id of ["post", "user_posts"]) {
  const el = document.getElementById(id);
  if (!el) continue;
  el.addEventListener("input", () => {
    el.removeAttribute("lang");
    setActiveTile("");
    showScenarioBanner(null);
  });
}

// Pacing for the scripted walkthrough, in milliseconds. Long enough that a
// visitor can read each stage before the next one replaces it.
const STAGE_HOLD_MS = 3000;
const POST_HOLD_MS = 3500;
const FIELD_TYPE_MS = 500;

const pause = (ms) => new Promise(resolve => {
  const t = setTimeout(resolve, ms);
  replayTimers.push(t);
});

function prefersReducedMotion() {
  return Boolean(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function scrollIntoViewGently(el, block = "start") {
  if (!el) return;
  el.scrollIntoView({ block, behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

// A caption above the results naming the stage of the walkthrough that
// produced them. `note` is what the caption leaves out, behind the icon.
function setStage(text, note) {
  const el = document.getElementById("result-stage");
  if (!el) return;
  if (!text) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");
  if (!note) { el.textContent = text; return; }
  const info = infoNote("i-stage", "this step", escapeHtml(note));
  el.innerHTML = escapeHtml(text) + info.button + info.body;
}

// ---------- running a preset ----------
// A preset is a list of named steps. It stops at the first step whose
// request does not go through, names that step and offers Retry. Pause holds
// between steps, Next step ends a hold at once, Restart begins again.

let _preset = null;
// True when this browser may not edit the scenario's place, so the two
// onboarding steps show the place as it is instead.
let _presetSkipsOnboarding = false;

function setPresetStep(text) {
  const el = document.getElementById("preset-step");
  if (el) el.textContent = text;
}

// The state of the loaded scenario, said in words beside the step line:
// "in progress" while it runs, "finished" after the last step, "stopped"
// after a stop. The step line is the live region of the pair, so this label
// is a plain span and the change is announced once.
let _scenarioState = "";

function setScenarioState(state) {
  _scenarioState = state;
  const el = document.getElementById("preset-state");
  const title = (currentScenario && currentScenario.title) || "";
  const noun = RECORDED_RUN ? "Recorded run" : "Scenario";
  if (el) el.textContent = state && title ? `${noun} ${state}: ${title}` : "";
  setTileTag(state);
}

// How far through the steps the scenario is. It repeats what the step line
// says, for the eye.
function setPresetProgress(value, max) {
  const el = document.getElementById("preset-progress");
  if (!el) return;
  el.max = Math.max(1, max || 1);
  el.value = Math.min(Math.max(0, value || 0), el.max);
}

function setPauseLabel(paused) {
  const el = document.getElementById("preset-pause-btn");
  if (el) el.textContent = paused ? "Resume" : "Pause";
}

function showPresetControls(on) {
  const el = document.getElementById("preset-controls");
  if (el) el.classList.toggle("hidden", !on);
  if (on) {
    const restart = document.getElementById("preset-restart-btn");
    if (restart) restart.classList.remove("hidden");
    showPresetRetry(false);
  } else {
    // With the bar off the page there is no state for the tile to echo.
    setTileTag("");
  }
}

function showPresetRetry(on) {
  for (const [id, hidden] of [["preset-retry-btn", !on],
                              ["preset-pause-btn", on],
                              ["preset-next-btn", on]]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", hidden);
  }
}

// The buttons that drive the remaining steps. A preset that has stopped has
// no step to pause, advance or retry, and keeps Restart, Exit scenario and
// the line that says it stopped.
function showStepButtons(on) {
  for (const id of ["preset-pause-btn", "preset-next-btn", "preset-retry-btn"]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", !on);
  }
}

// Next step does nothing while a step's request is in flight. The button
// says so rather than ignoring the press.
function setNextStepBusy(busy) {
  const el = document.getElementById("preset-next-btn");
  if (!el) return;
  el.disabled = Boolean(busy);
  el.setAttribute("aria-disabled", busy ? "true" : "false");
}

function cancelPreset() {
  clearReplay();
  if (_preset) {
    _preset.cancelled = true;
    if (_preset.wake) _preset.wake();
  }
  _preset = null;
  setNextStepBusy(false);
}

// True while a preset still has a step to run. One that has finished, or
// stopped at a failed step, is not running.
function presetIsRunning() {
  return Boolean(_preset && !_preset.cancelled && !_preset.done);
}

// A tab or a control the visitor picks ends a running preset. The answer on
// screen, the scenario's place and the caption above the answer all stay as
// they are; only the walkthrough stops.
function stopPresetByHand() {
  if (!presetIsRunning()) return;
  cancelPreset();
  setActiveTile("");
  showStepButtons(false);
  setPresetStep("Scenario stopped.");
  setScenarioState("stopped");
}

// ---------- leaving a scenario ----------
// Exit scenario takes the visitor back to the landing state: the walkthrough
// ends, the scenario's text and its answer come off the page, and the Post
// Geolocation tab is opened. A request already in flight is ignored when it
// returns, so a late answer cannot repaint the pane the visitor came back
// to.
let _exitCount = 0;

function runIsCurrent(token) { return token === _exitCount; }

// A cold-start scenario leaves its place in the shared catalogue. Exit does
// not remove it: another visitor may be using it, and Reset onboarding is
// the control that removes one.
let _leftoverPlace = "";

// The name is kept and checked against the catalogue on every read, so the
// line follows the place rather than the moment Exit was pressed: it is
// there while the place is, and gone once it is reset or has expired.
function leftoverPlace() {
  return _leftoverPlace && isOnboarded(_leftoverPlace) ? _leftoverPlace : "";
}

function exitScenario() {
  _exitCount += 1;
  // A recorded run is a page of its own, and the way out of it is the same
  // page without the query parameter.
  if (RECORDED_RUN) {
    window.location.assign(window.location.pathname);
    return;
  }
  cancelPreset();
  stopQueryProgress();
  _leftoverPlace = (currentScenario && currentScenario.onboard_city) || "";
  currentScenario = null;
  _presetSkipsOnboarding = false;
  showPresetControls(false);
  setPresetStep("");
  setScenarioState("");
  setActiveTile("");
  showScenarioBanner(null);
  setStage("");
  fillInput("", [], null);
  // The comparison panel is turned on by the scenario file, so it is the
  // scenario's setting rather than the visitor's and goes with it.
  const comparison = document.getElementById("comparison-toggle");
  if (comparison) comparison.checked = false;
  clearResults();
  clearDemoError();
  clearDraftPin();
  const replay = document.getElementById("replay-btn");
  if (replay) replay.classList.add("hidden");
  applyMode("post");
  moveFocusTo(document.getElementById("tab-post"));
  // applyMode announces only a tab a step opened, and leaving is more than
  // the tab change, so the sentence is written here instead.
  const said = document.getElementById("tab-announce");
  if (said) said.textContent = "Left the scenario. Now on Post Geolocation.";
  // Whether the scenario's place is in the catalogue decides whether the
  // landing state carries a line about it, and a step may have added it a
  // moment ago.
  loadCatalogue(true);
}

// The hold between two steps. Pause clears the timer, Resume re-arms it, and
// Next step ends the hold immediately.
function presetHold(state, ms) {
  return new Promise(resolve => {
    let timer = null;
    const done = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      state.wake = null;
      state.arm = null;
      resolve();
    };
    state.wake = done;
    state.arm = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      if (!state.paused) { timer = setTimeout(done, ms); replayTimers.push(timer); }
    };
    state.arm();
  });
}

async function runPresetSteps(sc, steps, from = 0) {
  const state = {
    sc, steps, index: from, paused: false, cancelled: false, done: false,
    wake: null, arm: null,
  };
  _preset = state;
  showPresetControls(true);
  setScenarioState("in progress");
  setPauseLabel(false);
  for (let i = from; i < steps.length; i++) {
    if (state.cancelled) return;
    state.index = i;
    // The line the recorder parses says "Step N of M" and nothing else; the
    // tab a step opened is announced by applyMode.
    setPresetStep(`Step ${i + 1} of ${steps.length}: ${steps[i].name}`);
    setPresetProgress(i + 1, steps.length);
    let ok = true;
    setNextStepBusy(true);
    try { ok = await steps[i].run(); } catch (e) { ok = false; }
    setNextStepBusy(false);
    if (state.cancelled) return;
    if (ok === false) {
      setPresetStep(
        `Scenario stopped at step ${i + 1} of ${steps.length}: ` +
        `${_lastFailureClause || "the request did not go through"}.`);
      setScenarioState("stopped");
      state.done = true;
      showPresetRetry(true);
      return;
    }
    if (i < steps.length - 1) {
      await presetHold(state, steps[i].hold || STAGE_HOLD_MS);
      if (state.cancelled) return;
    }
  }
  setPresetStep(`Finished: ${steps.length} ${steps.length === 1 ? "step" : "steps"}.`);
  setPresetProgress(steps.length, steps.length);
  setScenarioState("finished");
  state.done = true;
  for (const id of ["preset-pause-btn", "preset-next-btn"]) {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  }
}

// The tab a scenario's queries run on. A scenario file names it; a file that
// names none is read as a verification scenario, which is the case that
// needs both boxes.
function scenarioTask(sc) {
  const task = sc && sc.task;
  return QUERY_MODES.includes(task) ? task : "verify";
}

// The tab one step runs on. A post that names a task of its own runs there,
// so a scenario can walk the three tabs; one that names none runs on the
// scenario's own tab, which is what every cold-start step does.
function postTask(sc, item) {
  const task = item && item.task;
  return QUERY_MODES.includes(task) ? task : scenarioTask(sc);
}

// Whether the answer now on screen carries the verification flag. A step
// whose caption names the flag reads this rather than assuming it: in
// placeholder mode the two levels can land close enough to raise none.
function lastAnswerRaisedTheFlag() {
  const tri = window._lastResult && window._lastResult.triangulation;
  return Boolean(tri && tri.disagreement_flag);
}

// The onboarding steps of a preset run on the onboarding tab, so the form
// the caption describes is the one on screen.
function showOnboardingStep() {
  applyMode("onboard", { announce: true });
  scrollIntoViewGently(document.getElementById("onboard-panel"));
}

// The steps of one preset, each with the name the controls print.
function presetSteps(sc, coldStart) {
  const hold = sc.stage_hold_ms || STAGE_HOLD_MS;
  const steps = [];
  if (coldStart) {
    steps.push({
      name: `${sc.onboard_city} before onboarding`,
      hold,
      run: async () => {
        applyMode(scenarioTask(sc), { announce: true });
        document.getElementById("onboard_city").value = sc.onboard_city;
        document.getElementById("onboard_region").value = sc.region || "";
        const reset = await resetOnboarded(sc.onboard_city);
        if (reset === null) return false;
        _presetSkipsOnboarding = reset === "refused";
        const first = sc.posts[0];
        fillInput(first.post, first.user_posts, sc.lang);
        if (_presetSkipsOnboarding) {
          setStage(`${sc.onboard_city} was onboarded from another browser.`,
                   "This run shows the place as it is rather than the cold start.");
        } else {
          setStage(`Before onboarding: ${sc.onboard_city} is not a candidate.`,
                   "No engine can return it.");
        }
        return runGeolocate();
      },
    });
    steps.push({
      name: "the drafted profile and its warnings",
      hold,
      run: async () => {
        if (_presetSkipsOnboarding) return true;
        showOnboardingStep();
        // Onboard adds the place and drafts its profile in one call, so the
        // caption says both. The result above it predates the call.
        setStage(`Drafted a profile, and ${sc.onboard_city} is now a candidate.`,
                 `Onboard added ${sc.onboard_city} to the catalogue and ` +
                 "drafted the profile below it. The answer above was computed " +
                 `before that, so it is what the engines give without ` +
                 `${sc.onboard_city}.`);
        return runOnboard(sc.onboard_city, false);
      },
    });
    steps.push({
      name: "the operator's edits",
      hold,
      run: async () => {
        if (_presetSkipsOnboarding) return true;
        showOnboardingStep();
        setStage("The operator reviews and saves.",
                 "The operator fixes what the panel flags before saving.");
        const saved = await applyOperatorEdits(sc);
        if (saved === false) return false;
        setStage(`Saved: ${sc.onboard_city} carries the operator's profile.`,
                 "Every engine now reads that profile when it picks the place.");
        return true;
      },
    });
  }
  sc.posts.forEach((item, i) => {
    const task = postTask(sc, item);
    steps.push({
      name: item.step
        || (sc.posts.length > 1 ? `post ${i + 1} of ${sc.posts.length}` : "the query"),
      hold: sc.posts.length > 1 ? POST_HOLD_MS : hold,
      run: async () => {
        applyMode(task, { announce: true });
        fillInput(item.post, item.user_posts, sc.lang);
        // Each post carries its own caption, in the wording the recorded
        // run uses, so the line above the answer describes this step rather
        // than the save before it.
        if (item.stage) {
          setStage(item.stage);
        } else if (coldStart) {
          const which = sc.posts.length > 1
            ? `post ${i + 1} of ${sc.posts.length}`
            : "the query";
          setStage(`After onboarding: ${which}, with ${sc.onboard_city} a candidate.`);
        }
        const ok = await runGeolocate();
        if (ok === false) return false;
        // A caption that names the flag goes up only once the answer has
        // raised one.
        if (item.stage_flagged && lastAnswerRaisedTheFlag()) setStage(item.stage_flagged);
        return ok;
      },
    });
  });
  return steps;
}

async function runScenario(sc) {
  cancelPreset();
  currentScenario = sc;
  _presetSkipsOnboarding = false;
  // The place an earlier scenario left behind is this one's business again.
  _leftoverPlace = "";
  // A tile opens the tab its first step runs on, before that step runs.
  applyMode(postTask(sc, (sc.posts || [])[0]), { announce: true });
  setActiveTile(sc.id);
  showScenarioBanner(sc);
  document.getElementById("comparison-toggle").checked = !!sc.comparison_default;
  setStage("");
  // A pin left over from an earlier draft is not part of this scenario.
  clearDraftPin();

  // A hosted instance shares one catalogue, so the place this scenario
  // onboards may be another visitor's work. Removing it is a choice, and
  // running with the place as it is is the other option.
  let coldStart = Boolean(sc.onboard_city);
  if (sc.onboard_city && isOnboarded(sc.onboard_city)) {
    coldStart = window.confirm(
      `${sc.onboard_city} is currently onboarded on this shared instance, and may be ` +
      "another visitor's work.\n\nOK removes it and replays the cold start from the " +
      "beginning.\nCancel shows the scenario with the place as it is."
    );
  }
  if (!coldStart && sc.onboard_city) {
    document.getElementById("onboard_city").value = sc.onboard_city;
    document.getElementById("onboard_region").value = sc.region || "";
    setStage(`${sc.onboard_city} was onboarded by another visitor.`,
      "This run shows the scenario with the place already in the catalogue " +
      "rather than the cold start.");
  }

  document.getElementById("replay-btn").classList.remove("hidden");
  await runPresetSteps(sc, presetSteps(sc, coldStart));
}

document.getElementById("replay-btn").addEventListener("click", () => {
  if (currentScenario) runScenario(currentScenario);
});

document.getElementById("preset-pause-btn").addEventListener("click", () => {
  if (!_preset) return;
  _preset.paused = !_preset.paused;
  setPauseLabel(_preset.paused);
  if (_preset.arm) _preset.arm();
});

document.getElementById("preset-next-btn").addEventListener("click", () => {
  if (_preset && _preset.wake) _preset.wake();
});

document.getElementById("preset-restart-btn").addEventListener("click", () => {
  if (currentScenario) runScenario(currentScenario);
});

document.getElementById("preset-exit-btn").addEventListener("click", exitScenario);

document.getElementById("preset-retry-btn").addEventListener("click", () => {
  if (!_preset || !currentScenario) return;
  const { steps, index } = _preset;
  clearDemoError();
  runPresetSteps(currentScenario, steps, index);
});

// The place Reset onboarding would remove: the running scenario's, or the
// name in the onboarding form, and only where that place is onboarded now.
// With nothing to remove the control is off the page, so the button is
// never a press that does nothing.
function resettablePlace() {
  const box = document.getElementById("onboard_city");
  const name = (currentScenario && currentScenario.onboard_city)
    || ((box && box.value) || "").trim()
    || leftoverPlace();
  return name && isOnboarded(name) ? name : "";
}

// Where the control belongs: the onboarding tab, which owns the form, a
// query tab while a cold-start scenario is loaded there, and the landing
// state after a scenario has been left with its place still in the
// catalogue.
function updateResetVisibility() {
  const row = document.getElementById("reset-row");
  if (!row) return;
  const coldStart = Boolean(currentScenario && currentScenario.onboard_city);
  const left = leftoverPlace();
  const ownsIt = CURRENT_MODE === "onboard"
    || (QUERY_MODES.includes(CURRENT_MODE) && (coldStart || Boolean(left)));
  const show = Boolean(ownsIt && resettablePlace());
  row.classList.toggle("hidden", !show);
  showLeftoverLine(show && !currentScenario ? left : "");
  closeNotesOffThisTab();
}

// One quiet line above Reset onboarding, naming the place a scenario left
// in the shared catalogue. It goes once the place is reset or has expired.
function showLeftoverLine(place) {
  const el = document.getElementById("leftover-place");
  if (!el) return;
  el.textContent = place ? `${place} is still onboarded.` : "";
  el.classList.toggle("hidden", !place);
}

document.getElementById("onboard_city").addEventListener("input", updateResetVisibility);

document.getElementById("reset-scenario-btn").addEventListener("click", async () => {
  stopPresetByHand();
  const city = resettablePlace();
  if (!city) { updateResetVisibility(); return; }
  const change = await resetOnboarded(city);
  if (change && change !== "refused") showOnboardStatus(city, change);
});

// ---------- focus ----------
// Focus follows the answer. Nothing here takes focus from a box the visitor
// is typing in.

function userIsTyping() {
  const el = document.activeElement;
  if (!el) return false;
  if (el.tagName === "TEXTAREA") return true;
  return el.tagName === "INPUT" && !["file", "checkbox", "radio", "button", "submit"]
    .includes((el.type || "").toLowerCase());
}

function moveFocusTo(el) {
  if (!el || userIsTyping()) return;
  try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
}

// Where the answer starts: the flag when one is raised, the results heading
// otherwise.
function revealResult() {
  const flag = document.getElementById("disagreement-banner");
  const heading = document.getElementById("results-heading");
  const target = flag && !flag.classList.contains("hidden") ? flag : heading;
  moveFocusTo(target);
  scrollIntoViewGently(target);
}

// ---------- geolocate ----------
function showDemoError(msg, { keepResults = false } = {}) {
  const el = document.getElementById("demo-error");
  el.textContent = msg;
  el.classList.remove("hidden");
  // The previous answer comes off screen, so it cannot be read as the answer
  // to the input that just failed.
  if (!keepResults) clearResults();
  moveFocusTo(el);
}
function clearDemoError() {
  document.getElementById("demo-error").classList.add("hidden");
}

// Every endpoint answers a failure with one envelope,
// `{"error": {"code", "message", "field", "details"}, "detail": "..."}`.
// The list-of-dictionaries form an older instance can return is read too.
// The parsed envelope, or null when the body is not JSON at all: a hosting
// platform serves an HTML error page while an instance is waking.
function jsonBody(txt) {
  if (!txt) return null;
  try {
    const parsed = JSON.parse(txt);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (e) { return null; }
}

function errorBody(txt) {
  return jsonBody(txt) || (txt ? { detail: txt } : {});
}

// What each bounded field is called in a sentence.
const FIELD_PROSE = {
  post: "the post",
  user_posts: "the timeline",
  city: "the place name",
  name: "the place name",
  region: "the country or region hint",
  aliases: "the aliases",
  landmarks: "the landmarks",
  file: "the uploaded file",
  k: "k",
};

// A refusal as a sentence rather than a field path and a schema message.
function refusalSentence(body) {
  const err = body.error || {};
  const field = String(err.field || "");
  let message = String(err.message || detailText(JSON.stringify(body)) || "").trim();
  if (field && message.toLowerCase().startsWith(`${field.toLowerCase()}: `)) {
    message = message.slice(field.length + 2);
  }
  const subject = FIELD_PROSE[field] || "";
  let match = /String should have at most ([\d]+) characters/.exec(message);
  if (match) {
    return `${cap(subject || "This field")} is longer than the ` +
      `${Number(match[1]).toLocaleString()}-character limit.`;
  }
  match = /List should have at most ([\d]+) items/.exec(message);
  if (match) {
    return `${cap(subject || "This field")} holds more than ${match[1]} entries.`;
  }
  match = /^(?:String|Input|Value|List|Decimal input) should (.*)$/.exec(message);
  if (match && subject) return `${cap(subject)} should ${match[1]}.`;
  if (!message) return "The instance refused the request.";
  return /[.!?]$/.test(message) ? message : `${message}.`;
}

function detailText(txt) {
  if (!txt) return "";
  const parsed = errorBody(txt);
  if (parsed.error && parsed.error.message) return String(parsed.error.message);
  const detail = parsed.detail;
  if (!detail) return txt;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(d => {
      const field = Array.isArray(d.loc) ? d.loc.filter(p => p !== "body").join(".") : "";
      const msg = String(d.msg || "").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    }).join(" ");
  }
  return txt;
}

function httpErrorMessage(status, txt, retryAfter, place) {
  const body = jsonBody(txt);
  if (!body) {
    return `The instance did not answer (${status}). It may be restarting; ` +
      "try again in a minute.";
  }
  const detail = detailText(txt);
  if (status === 429) {
    const seconds = Number(retryAfter);
    const wait = Number.isFinite(seconds) && seconds > 0
      ? ` Try again in about ${Math.max(1, Math.round(seconds / 60))} minutes.`
      : "";
    const base = detail && detail !== txt ? detail : "This instance limits how many calls one address may make per hour.";
    return `${base}${detail.includes("minute") ? "" : wait} Running GeoLens locally, from the ` +
      "source link in the header, applies no such limit.";
  }
  if (status === 409) {
    // The place is already a candidate under a different spelling. Saying so
    // with the name it is held under is the whole of what the operator needs.
    const held = ((body.error || {}).details || [])
      .map(d => d && d.existing_name).filter(Boolean)[0];
    if (held) {
      return `This place is already in the catalogue under the name ${held}. ` +
        `Onboard or edit it under that spelling.`;
    }
    return detail || "This place is already in the catalogue under another spelling.";
  }
  if (status === 404) {
    return detail || "The instance has nothing at that address.";
  }
  if (status === 422 || status === 400 || status === 413) {
    return refusalSentence(body);
  }
  if (status === 403) {
    const code = (body.error || {}).code || "";
    if (code === "edit_token_required" || code === "edit_token_invalid") {
      return editTokenRefusal(place);
    }
  }
  return detail ? `The instance answered ${status}. ${detail}` :
    `The instance answered ${status}.`;
}

// This browser did not draft the place, so it cannot edit or remove it.
// How long it lasts comes from the catalogue listing, which carries the
// expiry of every onboarded place.
function editTokenRefusal(place) {
  const listed = _onboardedPlaces.find(p => placeKey(p.name) === placeKey(place));
  const minutes = listed && listed.expires_in_minutes;
  const life = minutes == null
    ? ""
    : ` It expires in about ${Math.max(1, Math.round(minutes))} minutes.`;
  return "This place was onboarded from another browser, so it cannot be " +
    `edited or removed from here.${life}`;
}

// The tab whose task produced the answer now on screen, or null when there
// is none.
let _lastResultMode = null;

// The previous answer is taken off screen, so it cannot be read as the
// answer to the input that just failed.
function clearResults() {
  window._lastResult = null;
  _lastResultMode = null;
  const source = document.getElementById("result-source");
  if (source) { source.classList.add("hidden"); source.textContent = ""; }
  for (const id of ["mode-banner", "run-line", "near-miss-note", "disagreement-banner",
                    "agreement-note", "ensembles", "comparison", "per-engine-section",
                    "map-notice", "export-result-note", "map-legend", "map-list-wrap",
                    "flag-gazetteer-note"]) {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  }
  // The export note's body is a sibling of the button's wrapper, so it does
  // not go with the wrapper and is closed here instead.
  const exportNote = document.querySelector('.info-btn[aria-controls="i-export"]');
  if (exportNote) setInfoOpen(exportNote, false);
  if (markerLayer) markerLayer.clearLayers();
  if (radiusLayer) radiusLayer.clearLayers();
  _radiusRequest = null;
  _mappedPoints = 0;
  _legendKeys = new Set();
  const summaryEl = document.getElementById("map-summary");
  if (summaryEl) summaryEl.textContent = "No mapped prediction.";
}

// ---------- engine selection ----------
// A manual choice between two presets. The local preset omits the engines the
// server's roster marks as third-party.

function selectedEngines(selectId) {
  const el = document.getElementById(selectId);
  return el && el.value === "local" ? LOCAL_ENGINE_NAMES.slice() : null;
}

function engineCountLabel(selectId) {
  const chosen = selectedEngines(selectId);
  return chosen ? chosen.length : rosterNames().length;
}

function runningLabel(selectId) {
  const n = engineCountLabel(selectId);
  return n ? `Running, ${n} engines…` : "Running the selected engines…";
}

// ---------- waiting ----------
// The server answers one request with one response and reports nothing while
// it works, so the pane shows which engines were asked and the clock. The
// pane is brought into view on submit, so the wait is where the reader looks.

let _progressTimer = null;

function enginesBeingAsked() {
  const chosen = selectedEngines("engine-preset");
  return chosen && chosen.length ? chosen : rosterNames();
}

function startQueryProgress() {
  const wrap = document.getElementById("result-waiting");
  const rows = document.getElementById("waiting-rows");
  const elapsed = document.getElementById("waiting-elapsed");
  const title = document.getElementById("waiting-title");
  if (!wrap || !rows || !elapsed || !title) return;
  const names = enginesBeingAsked();
  title.textContent = names.length
    ? `Running ${names.length} engines`
    : "Running the selected engines";
  rows.innerHTML = names.map(name =>
    `<div class="waiting-row">` +
      `<div class="waiting-name">${escapeHtml(engineMeta(name).label)}</div>` +
      `<div class="waiting-bar"></div>` +
    `</div>`).join("");
  wrap.classList.remove("hidden");
  const started = Date.now();
  const tick = () => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
  };
  tick();
  clearInterval(_progressTimer);
  _progressTimer = setInterval(tick, 1000);
  scrollIntoViewGently(document.getElementById("results-heading"));
}

function stopQueryProgress() {
  clearInterval(_progressTimer);
  _progressTimer = null;
  const wrap = document.getElementById("result-waiting");
  if (wrap) wrap.classList.add("hidden");
}

// ---------- how much of a limit the text uses ----------
// Counted against the caps GET /instance reports, so the refusal is visible
// before the request is sent.

function usedChars(id) {
  const box = document.getElementById(id);
  if (!box) return 0;
  if (id !== "user_posts") return box.value.trim().length;
  return box.value.split(/\r?\n/)
    .map(line => line.trim()).filter(Boolean)
    .reduce((total, line) => total + line.length, 0);
}

function updateCharCounts() {
  const caps = {
    post: (INSTANCE && INSTANCE.max_post_chars) || 0,
    user_posts: (INSTANCE && INSTANCE.max_timeline_chars) || 0,
  };
  for (const id of ["post", "user_posts"]) {
    const out = document.getElementById(`${id}-count`);
    if (!out) continue;
    const used = usedChars(id);
    const cap = caps[id];
    if (!cap) {
      out.textContent = used ? used.toLocaleString() : "";
      out.classList.remove("over");
      continue;
    }
    out.textContent = `${used.toLocaleString()} / ${cap.toLocaleString()}`;
    out.classList.toggle("over", used > cap);
  }
}

for (const boxId of ["post", "user_posts"]) {
  const box = document.getElementById(boxId);
  if (box) box.addEventListener("input", updateCharCounts);
}

// ---------- the estimated-spend ceiling ----------
// While the ceiling is reached the paid engines stand down and the local
// ones keep answering. One notice, carrying the server's reason.
function showSpendNotice(spend) {
  const el = document.getElementById("spend-notice");
  if (!el) return;
  if (!spend || !spend.ceiling_reached) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.textContent = spend.reason ||
    "The estimated-spend ceiling is reached, so the engines that call a paid " +
    "model are standing down. The local engines keep answering.";
}

// ---------- quota ----------
// How much of each per-hour budget is left, read off the response headers.
// An unlimited budget sends no header.
function readQuota(resp) {
  const quota = {};
  for (const kind of ["Queries", "Batches", "Profiles"]) {
    const limit = Number(resp.headers.get(`X-RateLimit-Limit-${kind}`));
    const left = Number(resp.headers.get(`X-RateLimit-Remaining-${kind}`));
    if (limit > 0) quota[kind.toLowerCase()] = { limit, left };
  }
  if (Object.keys(quota).length) {
    window._quota = Object.assign(window._quota || {}, quota);
    showQuota();
  }
}

function showQuota() {
  const el = document.getElementById("quota-line");
  const counts = document.getElementById("quota-counts");
  if (!el || !counts) return;
  const quota = window._quota || quotaFromInstance();
  const names = { queries: "queries", batches: "bulk runs", profiles: "saves" };
  const parts = Object.entries(quota || {})
    .filter(([, q]) => q.limit > 0)
    .map(([kind, q]) => `${q.left}/${q.limit} ${names[kind]}`);
  if (!parts.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  counts.textContent = parts.join(" · ");
}

function quotaFromInstance() {
  if (!INSTANCE || !INSTANCE.limits) return null;
  const out = {};
  for (const [kind, l] of Object.entries(INSTANCE.limits)) {
    if (l.per_hour > 0) out[kind] = { limit: l.per_hour, left: l.remaining };
  }
  return out;
}

// A control that is running is disabled and says so, and a second click on it
// is ignored rather than firing a second paid query.
function withBusy(btn, label, work) {
  if (!btn) return work();
  if (btn.dataset.busy === "1") return Promise.resolve(null);
  const original = btn.textContent;
  btn.dataset.busy = "1";
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = label;
  return Promise.resolve()
    .then(work)
    .finally(() => {
      delete btn.dataset.busy;
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
      btn.textContent = original;
    });
}

// What is visible is what is sent. The post tab sends no recent posts and
// the user tab sends no post, so the answer matches the boxes on screen.
// Text typed in a box stays there while the tabs change.
function submittedText() {
  const sendsPost = CURRENT_MODE !== "user";
  const sendsTimeline = CURRENT_MODE !== "post";
  const post = sendsPost
    ? (document.getElementById("post").value.trim() || null)
    : null;
  const raw = sendsTimeline ? document.getElementById("user_posts").value.trim() : "";
  return { post, userPosts: raw ? raw.split(/\r?\n/).filter(Boolean) : null };
}

// What the page says when the boxes this tab shows are empty, and the clause
// a stopped preset prints after it.
const EMPTY_INPUT = {
  post: ["Enter a post, or pick a scenario above.",
         "there was no post to run on"],
  user: ["Enter the account's recent posts, or pick a scenario above.",
         "there were no recent posts to run on"],
  verify: ["Enter a post, recent posts, or both, or pick a scenario above.",
           "there was no post and no timeline to run on"],
};

async function runGeolocate() {
  const btn = document.getElementById("geolocate-btn");
  if (btn && btn.dataset.busy === "1") return false;
  // Exit scenario can land while this query is in flight. The answer to a
  // query the visitor has left behind is dropped rather than painted over
  // the pane they came back to.
  const token = _exitCount;
  // The roster decides which engines the local preset runs and how many the
  // page says are running, so the answer waits for it.
  await ensureInstance();
  clearDemoError();
  const { post, userPosts } = submittedText();
  if (!post && !userPosts) {
    const [message, clause] = EMPTY_INPUT[CURRENT_MODE] || EMPTY_INPUT.verify;
    showDemoError(message);
    _lastFailureClause = clause;
    return false;
  }
  // The drafted coordinate belongs to the onboarding form, not to this
  // answer, so it comes off the map before the query runs.
  clearDraftPin();
  // The previous answer's markers, flag and coordinate list go at the same
  // moment, so nothing under the waiting rows belongs to an earlier query.
  clearResults();
  const engines = selectedEngines("engine-preset");
  // The tab this query is asked from, kept for the answer, which can land
  // after the visitor has opened another tab.
  const askedFrom = CURRENT_MODE;
  startQueryProgress();
  return withBusy(btn, runningLabel("engine-preset"), async () => {
    try {
      const ensembleMethod = document.getElementById("ensemble-method").value;
      const resp = await fetch("/geolocate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          post, user_posts: userPosts, k: 5, ensemble_method: ensembleMethod,
          engines,
        }),
      });
      readQuota(resp);
      if (!runIsCurrent(token)) return false;
      if (!resp.ok) {
        _lastFailureClause = `the server answered ${resp.status}`;
        showDemoError(httpErrorMessage(resp.status, await resp.text().catch(() => ""),
                                       resp.headers.get("Retry-After")));
        return false;
      }
      const data = await resp.json();
      if (!runIsCurrent(token)) return false;
      data.submitted = { post, user_posts: userPosts };
      renderResults(data, askedFrom);
      revealResult();
      return true;
    } catch (e) {
      if (!runIsCurrent(token)) return false;
      _lastFailureClause = "the server could not be reached";
      showDemoError(`Could not reach the server: ${e.message || e}. Check your connection and retry.`);
      return false;
    } finally {
      stopQueryProgress();
    }
  });
}

document.getElementById("geolocate-btn").addEventListener("click", () => {
  // A query the visitor typed is not part of a scenario, so the scenario's
  // caption must not stay above the answer as though it described it.
  cancelPreset();
  showPresetControls(false);
  setStage("");
  runGeolocate();
});
document.getElementById("comparison-toggle").addEventListener("change", () => {
  const last = window._lastResult;
  if (last) renderResults(last, _lastResultMode || CURRENT_MODE);
});
// Changing the fusion method re-runs (the consensus is recomputed server-side).
document.getElementById("ensemble-method").addEventListener("change", () => {
  if (window._lastResult) runGeolocate();
});

// ---------- rendering ----------
// `mode` is the tab the answer was asked from, which is not always the tab
// on screen when it arrives: a query submitted on one tab can land after
// the visitor has moved to another.
// The two disclosures that explain the result rather than carry it. They
// open on a press and close again on the next answer, so an explanation the
// visitor opened for one result is not left standing over another.
function closeResultExplanations() {
  for (const selector of ["#fusion-note", ".disagreement-why"]) {
    document.querySelectorAll(selector).forEach(el => { el.open = false; });
  }
}

function renderResults(data, mode = CURRENT_MODE) {
  window._lastResult = data;
  closeResultExplanations();
  // The result pane serves three tasks, so it records which one produced
  // the answer: the run line names it, and a later tab switch says when the
  // answer is not the tab's own.
  _lastResultMode = mode;
  rememberCoords(data.place_coordinates);
  rememberPlaces(data.places);
  showSpendNotice(data.spend);
  renderRunLine(data.manifest || {}, data.ensembles || {});
  updateResultSource();
  renderAgreement(data.triangulation, data.per_engine || {});
  renderModeBanner(data.per_engine);
  renderEnsembles(data.ensembles || {}, data.per_engine || {});
  renderDisagreement(data.triangulation, data.per_engine || {});
  renderEngines(data.per_engine);
  renderMap(data.ensembles || {}, data.triangulation, data.per_engine || {});
  renderComparison(data.ensembles || {}, data.per_engine);
  // A recorded run is not this visitor's result, so there is nothing to export.
  if (!RECORDED_RUN) {
    document.getElementById("export-result-note").classList.remove("hidden");
  }
}

// Whether the gazetteer found a catalogue place in this level's text. `null`
// when it did not run, which is not the same as finding nothing.
function namedAPlace(perEngine, bucket) {
  const pred = (perEngine || {})[`gazetteer_${bucket}`];
  if (!pred || pred.skipped || pred.failed) return null;
  return !pred.abstain;
}

// What the gazetteer did, when a level's text matched no catalogue name or
// alias. On the view itself this is said once, by the flag's note; here it is
// the popup of the muted marker.
const NO_PLACE_STATEMENT =
  "No catalogue place or alias is named in this text, so the gazetteer " +
  "abstained. The engines shown always answer, so this prediction comes from them.";

// The task, k, the fusion method and the flag radius the answer below was
// produced with, as one compact line. The task comes first, because the
// same pane serves all three of them.
function renderRunLine(manifest, ensembles) {
  const el = document.getElementById("run-line");
  if (!el) return;
  if (!manifest.k) { el.classList.add("hidden"); return; }
  const method = manifest.ensemble_method === "rrf"
    ? "reciprocal rank fusion"
    : "sum of top-k scores";
  const selected = manifest.selected_engines || [];
  const total = Object.keys(manifest.engines || {}).length;
  const engineLine = selected.length && total && selected.length < total
    ? `${selected.length} of ${total} engines · `
    : "";
  const task = TAB_LABELS[_lastResultMode] ? `${TAB_LABELS[_lastResultMode]} · ` : "";
  // The radius belongs to a comparison of the two levels, so it is on the
  // line only where both of them answered.
  const bothLevels = Boolean((ensembles || {}).post && (ensembles || {}).user);
  const radius = bothLevels && manifest.flag_radius_km != null;
  el.classList.remove("hidden");
  const note = infoNote(
    "i-run-line", "this run's settings",
    "k is how many candidates each engine returns, and the fusion method is " +
    "the one selected in the input panel. The flag radius is the distance " +
    "beyond which the two levels count as disagreeing.");
  // The radius keeps its unit on the same line at a phone's width.
  el.innerHTML =
    escapeHtml(`${task}${engineLine}k = ${manifest.k} · ${method}`) +
    (radius
      ? escapeHtml(" · ") + `<span class="nowrap">` +
        escapeHtml(`flag radius ${manifest.flag_radius_km} km`) + `</span>`
      : "") +
    note.button + note.body;
  const radiusEl = document.getElementById("legend-radius");
  if (radiusEl && manifest.flag_radius_km) {
    radiusEl.textContent = `Flag radius, ${manifest.flag_radius_km} km`;
  }
}

// Which tab an answer came from, said once above it when the tab on screen
// is a different task. The answer itself has not changed, so this is a
// quiet line rather than a live region.
function updateResultSource() {
  const el = document.getElementById("result-source");
  if (!el) return;
  const from = _lastResultMode;
  const show = Boolean(window._lastResult) && Boolean(from)
    && QUERY_MODES.includes(from) && QUERY_MODES.includes(CURRENT_MODE)
    && from !== CURRENT_MODE;
  el.classList.toggle("hidden", !show);
  el.textContent = show ? `This result is from ${TAB_LABELS[from]}.` : "";
}

// How much of the roster landed on the same place. An input naming a place
// the catalogue does not hold is still answered with one that it does, and
// the only visible sign is that the engines scatter.
const LOW_AGREEMENT = 0.4;

function renderAgreement(tri, perEngine) {
  const el = document.getElementById("agreement-note");
  if (!el) return;
  // Agreement is judged within a level. Post-level and user-level engines
  // answer different questions, so their differing is what the flag reports.
  const low = [];
  const abstained = [];
  for (const level of ["post", "user"]) {
    const answered = Object.entries(perEngine)
      .filter(([name, p]) => engineMeta(name).granularity === level &&
        p.usable !== false && !p.skipped && !p.failed && !p.abstain && p.city)
      .map(([, p]) => p);
    if (namedAPlace(perEngine, level) === false) {
      abstained.push(level === "post" ? "the post" : "the timeline");
    }
    if (answered.length < 2) continue;
    const counts = {};
    for (const p of answered) counts[p.city] = (counts[p.city] || 0) + 1;
    const share = Math.max(...Object.values(counts)) / answered.length;
    if (share < LOW_AGREEMENT) low.push(`${Math.round(share * 100)}% of the ${level}-level engines`);
  }
  if (!low.length) { el.classList.add("hidden"); return; }
  // The gazetteer abstained, so it is not in the share above. Saying so is
  // the difference between "the engines disagree" and "there was nothing in
  // the text for one of them to agree about".
  const abstentionLine = abstained.length
    ? `The gazetteer named no catalogue place in ${abstained.join(" or ")}, so it abstained ` +
      "and is not counted in that share. "
    : "";
  el.classList.remove("hidden");
  const note = infoNote(
    "i-agreement", "why the engines scatter",
    escapeHtml(abstentionLine +
      "The catalogue is closed, so an input naming a place it does not hold is " +
      "still answered with one that it does. Check the candidate list on the " +
      "left, and onboard the place if it is missing."));
  el.innerHTML = escapeHtml(
    `The engines disagree: only ${low.join(" and ")} put the same place first.`
  ) + note.button + note.body;
}

function renderModeBanner(perEngine) {
  const el = document.getElementById("mode-banner");
  const engines = Object.values(perEngine || {});
  const ran = engines.filter(p => !p.skipped);
  const placeholders = ran.filter(p => p.mode === "stub").length;
  const failed = ran.filter(p => p.failed && !namedNoCataloguePlace(p)).length;
  const placeless = ran.filter(namedNoCataloguePlace).length;
  window._placeholderMode = placeholders > 0;
  updateScenarioPlaceholderLine();
  // The headline of each state stays on screen; what each one implies for
  // the fusion and the flag is in the one note under them.
  const lines = [];
  const tails = [];
  if (placeholders > 0) {
    lines.push(
      `Placeholder mode: ${placeholders} of the ${ran.length} engines that ran returned ` +
      `a placeholder prediction, not live model output. Set API keys for live inference. ` +
      `Do not treat this result as operational.`);
  }
  if (failed > 0) {
    lines.push(`${failed} of ${ran.length} engines failed.`);
    tails.push("Nothing they returned is counted. The cards below name the " +
               "error.");
  }
  if (placeless > 0) {
    lines.push(`${placeless} of ${ran.length} engines named a place outside the catalogue.`);
    tails.push("Their replies are discarded.");
  }
  if (!lines.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  if (!tails.length) { el.textContent = lines.join(" "); return; }
  const note = infoNote("i-mode-banner", "what this means for the result",
                        escapeHtml(tails.join(" ")));
  el.innerHTML = escapeHtml(lines.join(" ")) + note.button + note.body;
}

// What a preset shows in placeholder mode is not what the paper reports.
function updateScenarioPlaceholderLine() {
  const el = document.getElementById("active-scenario-banner");
  if (!el) return;
  const line = el.querySelector(".banner-placeholder");
  if (!line) return;
  const show = Boolean(currentScenario) && Boolean(window._placeholderMode);
  line.classList.toggle("hidden", !show);
  line.textContent = show
    ? "Placeholder mode: this is not the result reported in the paper."
    : "";
}

// The whole result as one file: the submitted text and its SHA-256, every
// engine's answer, both fused predictions, the consensus pair behind the
// verification flag, and the run manifest.
document.getElementById("export-result-btn").addEventListener("click", () => {
  const data = window._lastResult;
  if (!data) return;
  const submitted = data.submitted || { post: null, user_posts: null };
  const payload = Object.assign({}, data, {
    submitted_input: submitted,
    input_sha256: (data.manifest || {}).input_sha256 || null,
    exported_at: new Date().toISOString(),
  });
  delete payload.submitted;
  downloadFile(timestampedName("geolens-result", "json"),
               JSON.stringify(payload, null, 2), "application/json");
});

// A file name that sorts and does not collide: geolens-result-20260919-1412.json
function timestampedName(stem, extension) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${stem}-${stamp}.${extension}`;
}

// How many of the level's engines put the fused place first. Which ones
// they are is behind the icon: the same names are printed again on the
// engine cards below.
function agreementLine(bucket, e, perEngine) {
  const answered = [];
  const agreeing = [];
  const abstained = [];
  for (const name of e.contributing_engines) {
    const p = perEngine[name];
    if (!p) continue;
    const label = engineMeta(name).label;
    if (p.abstain) { abstained.push(label); continue; }
    answered.push(label);
    if (p.city === e.consensus_city) agreeing.push(label);
  }
  const city = escapeHtml(e.consensus_city);
  const named = (labels) =>
    `They are ${escapeHtml(labels.slice(0, -1).join(", "))}` +
    (labels.length > 1 ? " and " : "") +
    `${escapeHtml(labels[labels.length - 1])}.`;
  let text;
  let body = "";
  if (!answered.length) {
    text = "No engine in this bucket returned a location.";
  } else if (agreeing.length === answered.length) {
    text = `All ${answered.length} engines that answered agree.`;
    body = named(agreeing);
  } else if (agreeing.length) {
    text = `${agreeing.length} of ${answered.length} engines that answered agree.`;
    body = named(agreeing);
  } else {
    text = `No engine put <b>${city}</b> first on its own.`;
    body = "It comes out of the combined top-k lists.";
  }
  const tail = abstained.length
    ? ` ${escapeHtml(abstained.join(", "))} abstained.` : "";
  if (!body) return text + tail;
  // Both levels are on screen together, so each button names its own
  // level, and the tail goes before the button rather than between the
  // button and the note it opens.
  const note = infoNote(`i-agree-${bucket}`, `${bucket}-level agreement`, body);
  return text + tail + note.button + note.body;
}

// The reason a bucket produced no fused prediction, or "" when it did.
function bucketSkipReason(bucket, perEngine) {
  const inBucket = Object.entries(perEngine || {})
    .filter(([name]) => engineMeta(name).granularity === bucket)
    .map(([, p]) => p);
  if (!inBucket.length) return "";
  if (inBucket.every(p => p.skipped)) {
    return inBucket[0].reason || "these engines were not run";
  }
  if (inBucket.every(p => p.skipped || p.failed)) {
    return "every call in this bucket failed";
  }
  return "no engine in this bucket returned a candidate list";
}

function renderEnsembles(ensembles, perEngine) {
  const wrap = document.getElementById("ensembles");
  const cards = document.getElementById("ensemble-cards");
  cards.innerHTML = "";
  wrap.classList.remove("hidden");

  for (const bucket of ["post", "user"]) {
    const e = ensembles[bucket];
    if (!e) {
      const reason = bucketSkipReason(bucket, perEngine);
      if (!reason) continue;
      const note = document.createElement("div");
      note.className = "bucket-note";
      const info = infoNote(
        `i-bucket-${bucket}`, `the missing ${bucket}-level prediction`,
        escapeHtml(bucket === "user"
          ? "Without a timeline there is nothing to verify the post against, so no verification flag is computed either."
          : "Without a post there is nothing to verify the timeline against, so no verification flag is computed either."));
      note.innerHTML =
        escapeHtml(`No ${bucket}-level fused prediction: ${reason}.`) +
        info.button + info.body;
      cards.appendChild(note);
      continue;
    }
    const card = document.createElement("div");
    card.className = `ensemble-card ${bucket}-bucket`;
    const conf = (e.consensus_confidence * 100).toFixed(1);
    // The fusion method is on the run line and in the fused prediction's own
    // note, so the card carries the level and the engine count.
    // The text naming no catalogue place is said once, in the flag's note,
    // in the coordinate list and by the muted marker.
    card.innerHTML =
      `<div class="ensemble-bucket">${cap(bucket)}-level fusion, ${e.contributing_engines.length} engines</div>` +
      `<div class="ensemble-city">${escapeHtml(e.consensus_city)}</div>` +
      `<div class="ensemble-conf">${conf}% fused score</div>` +
      `<div class="ensemble-detail">${agreementLine(bucket, e, perEngine || {})}</div>`;
    cards.appendChild(card);
  }
}

// "Post: Singapore (3 of 4 engines)." How thin the majority behind a
// consensus place was: a flag resting on a 2-of-4 split reads differently
// from one resting on a clean sweep.
function voteText(label, city, votes, answered) {
  if (!city) return "";
  if (!answered) return `${label}: ${city}.`;
  return `${label}: ${city} (${votes} of ${answered} engines).`;
}

function consensusVotes(tri) {
  if (!tri) return "";
  return [
    voteText("Post", tri.post_consensus_city, tri.post_consensus_votes,
             tri.post_consensus_answered),
    voteText("Timeline", tri.user_consensus_city, tri.user_consensus_votes,
             tri.user_consensus_answered),
  ].filter(Boolean).join(" ");
}

// Kilometres as a reader writes them.
function kmText(km) {
  return `${Math.round(km).toLocaleString()} km`;
}

// The flag's headline: the two places, the vote behind each and how far
// apart they are. The five candidate causes, what to check next and how the
// places were chosen are in the expandable note under it. The server's own
// sentence stays on the API untouched.
function flagHeadline(tri) {
  const parts = [consensusVotes(tri)];
  if (tri.disagreement_km != null) parts.push(`${kmText(tri.disagreement_km)} apart.`);
  return parts.filter(Boolean).join(" ");
}

// The flag is computed and shown whatever the gazetteer did. When a level's
// text matched no catalogue name or alias, the flag says so and no more.
// The two levels shared a twelve-word tail, so the tail is said once, in the
// note, and each level contributes only its subject.
function gazetteerAbstentionSubjects(perEngine) {
  const subjects = [];
  if (namedAPlace(perEngine, "post") === false) subjects.push("The post");
  if (namedAPlace(perEngine, "user") === false) subjects.push("the timeline");
  return subjects;
}

function gazetteerAbstentionMarkup(subjects) {
  const sentence = subjects.length > 1
    ? `${subjects.join(" and ")} name no catalogue place.`
    : `${cap(subjects[0])} names no catalogue place.`;
  const note = infoNote(
    "i-gazetteer", "what the gazetteer did",
    "The gazetteer abstained, so the consensus comes from the engines that " +
    "always answer.");
  return escapeHtml(sentence) + note.button + note.body;
}

function renderDisagreement(tri, perEngine) {
  const el = document.getElementById("disagreement-banner");
  const near = document.getElementById("near-miss-note");
  const abstained = document.getElementById("flag-gazetteer-note");
  near.classList.add("hidden");
  abstained.classList.add("hidden");

  if (!tri || !tri.disagreement_flag) {
    el.classList.add("hidden");
    // The two consensus places differ but sit inside the radius, which is
    // on the API and is worth saying on screen.
    if (tri && tri.disagreement_km != null && tri.notes && tri.notes[0]) {
      near.classList.remove("hidden");
      near.textContent = "No verification flag: " + tri.notes[0] + " " + consensusVotes(tri);
    }
    return;
  }

  el.classList.remove("hidden");
  el.querySelector(".disagreement-text").textContent = flagHeadline(tri);

  const subjects = gazetteerAbstentionSubjects(perEngine);
  if (subjects.length) {
    abstained.classList.remove("hidden");
    abstained.innerHTML = gazetteerAbstentionMarkup(subjects);
  }
}

// Two decimals, rounding half away from zero, which is the rule the paper's
// table uses. Working from the value's decimal form keeps .285 at .29, where
// rounding the binary double would put it at .28.
function round2(value) {
  const digits = Number(value).toFixed(3);
  return Number(digits.slice(0, -1)) + (Number(digits.slice(-1)) >= 5 ? 0.01 : 0);
}

// A rate as the benchmark prints it: .90, and 1.00 for a clean sweep.
function rate2(value) {
  return round2(value).toFixed(2).replace(/^0\./, ".");
}

// The gazetteer abstains whenever no catalogue place is named, so its rate on
// those rows is zero. The committed run predates that fix and holds one hit
// in 200 rows, which eval/README.md records as an artefact.
function isAbstentionArtefact(name, split, entry) {
  return name.startsWith("gazetteer") && split === "unnamed" &&
    Math.round(entry.acc_at_1 * entry.n) === 1;
}

// What the engine scored on the committed benchmark run, as one short line.
// For a post-level engine the two splits, because the same engine is a
// different instrument on a post that names a place and one that does not.
// The intervals, the rows and the provenance stay in the tooltip and are
// also in the card's note, so a hover is not the only way to them.
function engineReference(name) {
  const reference = INSTANCE && INSTANCE.engine_reference;
  const entry = reference && reference.engines && reference.engines[name];
  if (!entry) return null;
  const full = (e) =>
    `${rate2(e.acc_at_1)} [${rate2(e.acc_at_1_ci[0])}, ${rate2(e.acc_at_1_ci[1])}], n=${e.n}`;
  if (entry.named && entry.unnamed) {
    const artefact = isAbstentionArtefact(name, "unnamed", entry.unnamed);
    // The gazetteer cannot hit a row that names no place, so the line
    // prints a plain 0 and the note says why.
    const unnamed = artefact ? "0" : rate2(entry.unnamed.acc_at_1);
    const detail = `Named: ${full(entry.named)}. Not named: ` +
      (artefact
        ? `the one hit in ${entry.unnamed.n} rows predates the abstention fix.`
        : `${full(entry.unnamed)}.`);
    const opening = artefact
      ? "The gazetteer abstains whenever no catalogue place is named, so its " +
        "rate on those rows is 0 by construction."
      : "The two figures split the rows by whether the post named a " +
        "catalogue place.";
    return {
      head: "Benchmark Acc@1",
      figures: `${rate2(entry.named.acc_at_1)} named · ${unnamed} unnamed`,
      title: `${detail} ${reference.provenance || ""}`.trim(),
      body: `${opening} ${detail}`,
    };
  }
  const detail = `${full(entry.overall)}.`;
  return {
    head: "Benchmark Acc@1",
    figures: rate2(entry.overall.acc_at_1),
    title: `${detail} ${reference.provenance || ""}`.trim(),
    body: `The figure is the rate over every scored row. ${detail}`,
  };
}

// Where the rates come from, once under the whole section. The line names
// the benchmark; what every card would otherwise repeat is behind its icon.
function referenceProvenance() {
  const reference = INSTANCE && INSTANCE.engine_reference;
  if (!reference || !reference.provenance) return null;
  return {
    line: "Benchmark figures come from one committed WNUT-2016 run and are " +
      "not a confidence in this answer.",
    body: `Every figure above comes from the same run: ${reference.provenance} ` +
      "A cost beside an engine is estimated from the provider's list prices, " +
      "and is not read from a bill.",
  };
}

// A latency a reader can compare at a glance.
function latencyText(ms) {
  if (ms == null) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

// What the call is estimated to have cost, beside the latency. Only the
// engines that call a paid model report one.
function costText(usd) {
  if (!usd) return "";
  const figure = usd < 0.01 ? usd.toFixed(6) : usd.toFixed(4);
  return `est. $${figure}`;
}

// A reply that named nothing in the closed catalogue is a content outcome,
// not a failed call.
function namedNoCataloguePlace(p) {
  return Boolean(p && p.failed && p.outcome === "no_catalogue_place");
}

// How many of each level's engines put the scenario's onboarded place first.
function renderReachSummary(perEngine) {
  const el = document.getElementById("reach-summary");
  if (!el) return;
  const place = currentScenario && currentScenario.onboard_city;
  if (!place) { el.classList.add("hidden"); return; }
  const parts = [];
  for (const level of ["post", "user"]) {
    const inLevel = Object.entries(perEngine || {})
      .filter(([name]) => engineMeta(name).granularity === level);
    if (!inLevel.length) continue;
    // A level whose engines were all stood down reached nothing because it
    // was never asked, and "0 of 5" reads as five failures, so the level is
    // left out of the sentence instead.
    if (inLevel.every(([, p]) => p && p.skipped)) continue;
    const reached = inLevel.filter(([, p]) =>
      p && !p.skipped && !p.failed && !p.abstain && p.city === place).length;
    parts.push(`${reached} of ${inLevel.length} ${level}-level engines`);
  }
  if (!parts.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.textContent = `Reached ${place}: ${parts.join(", ")}.`;
}

// Whether this answer ran any engine at the level. A level that was stood
// down has a card for every engine saying so, and nothing to read inside.
function bucketRan(bucket, perEngine) {
  const inBucket = Object.entries(perEngine || {})
    .filter(([name]) => engineMeta(name).granularity === bucket)
    .map(([, p]) => p);
  return inBucket.some(p => p && !p.skipped);
}

function renderEngines(perEngine) {
  const section = document.getElementById("per-engine-section");
  section.classList.remove("hidden");
  document.getElementById("engine-count").textContent = Object.keys(perEngine).length;
  renderReachSummary(perEngine);

  const buckets = { post: [], user: [] };
  for (const [name, p] of Object.entries(perEngine)) {
    // Every engine the response carries gets a card, described from the
    // server's roster. One the roster does not name is still shown.
    const meta = engineMeta(name);
    buckets[meta.granularity].push([name, p, meta]);
  }

  for (const bucket of ["post", "user"]) {
    const container = section.querySelector(`.engine-cards[data-bucket="${bucket}"]`);
    container.innerHTML = "";
    section.querySelector(`.bucket-count[data-bucket="${bucket}"]`).textContent = buckets[bucket].length;
    // Only the level this query used is open. A level the query stood down
    // says so beside its count, so a closed group is not read as a result
    // the page is hiding.
    const ran = bucketRan(bucket, perEngine);
    const group = section.querySelector(`.engine-group[data-bucket="${bucket}"]`);
    if (group) group.open = ran;
    const state = section.querySelector(`.bucket-state[data-bucket="${bucket}"]`);
    if (state) state.textContent = ran ? "" : ", not run";
    for (const [name, p, meta] of buckets[bucket]) {
      const card = document.createElement("div");
      card.className = (p.failed || p.skipped) ? "engine-card inactive-card" : "engine-card";
      card.dataset.family = meta.family;
      card.dataset.engine = name;
      const conf = (p.confidence * 100).toFixed(1);
      const fillStyle = `width: ${Math.max(2, p.confidence * 100)}%`;
      const topkRows = p.top_k.slice(1, 4).map(([c, prob]) =>
        `<div class="topk-row"><span>${escapeHtml(c)}</span><span>${(prob*100).toFixed(0)}%</span></div>`
      ).join("");
      // The badge is a word; what the word means is in the card's note, so a
      // reader on a touch screen can reach it. Four of the five badges are
      // exceptions, and the note leads with the gloss for those, because the
      // badge is what a reader asks about first.
      let modeBadge;
      let badgeGloss;
      let badgeIsException = true;
      if (p.skipped) {
        modeBadge = `<span class="mode-badge skipped">not run</span>`;
        badgeGloss = "\"not run\" means this engine was not called, so it " +
          "contributes nothing to the result.";
      } else if (namedNoCataloguePlace(p)) {
        modeBadge = `<span class="mode-badge stub">no place named</span>`;
        badgeGloss = "\"no place named\" means the reply named a place the " +
          "catalogue does not hold. Nothing from it is counted.";
      } else if (p.failed) {
        modeBadge = `<span class="mode-badge failed">call failed</span>`;
        badgeGloss = "\"call failed\" means the call did not return a usable " +
          "answer, so it is left out of the fusion, the consensus and the " +
          "verification flag.";
      } else if (p.mode === "stub") {
        modeBadge = `<span class="mode-badge stub">placeholder</span>`;
        badgeGloss = "\"placeholder\" means no API key or model is installed " +
          "for this engine, so the answer is a stand-in and no model " +
          "produced it.";
      } else {
        modeBadge = `<span class="mode-badge real">real</span>`;
        badgeGloss = "\"real\" means the output came from a live model.";
        badgeIsException = false;
      }
      let cityLine;
      if (p.skipped) {
        cityLine = `<span class="not-run">Not run: ${escapeHtml(p.reason || "nothing to read")}</span>`;
      } else if (namedNoCataloguePlace(p)) {
        cityLine = `<span class="no-catalogue-place">Reply named no catalogue place</span>`;
      } else if (p.failed) {
        cityLine = `<span class="call-failed">Call failed: ${escapeHtml(p.error_class || "unknown error")}</span>`;
      } else if (p.abstain) {
        cityLine = `<span class="abstain">no location signal</span>`;
      } else {
        cityLine = `${escapeHtml(p.city)} <span class="conf">(${conf}%)</span>`;
      }
      const evidenceLine = p.evidence
        ? `<div class="evidence-line">${escapeHtml(p.evidence)}</div>` : "";
      const tag = meta.tag
        ? `<span class="engine-tag">${escapeHtml(meta.tag)}</span>`
        : "";
      const reference = engineReference(name);
      const cost = costText(p.cost_usd);
      const meterLine = [latencyText(p.latency_ms), cost].filter(Boolean).join(" · ");
      // One note per card, holding only what belongs to this card: what its
      // badge says, what the engine does, and the figures behind its
      // benchmark line. What every card shares sits once under the whole
      // section, on #reference-provenance.
      const paragraphs = [];
      if (badgeIsException) paragraphs.push(escapeHtml(badgeGloss));
      if (reference) paragraphs.push(escapeHtml(reference.body));
      if (meta.tagTitle) paragraphs.push(escapeHtml(meta.tagTitle));
      if (!badgeIsException) paragraphs.push(escapeHtml(badgeGloss));
      const note = infoNote(
        `i-ref-${name}`, meta.label,
        paragraphs.map(t => `<span class="info-para">${t}</span>`).join(""));
      // The line breaks where the two spans meet rather than inside
      // "· .07 unnamed". The icon sits inside the second span, so the
      // figures and the icon are measured together and move together.
      const referenceLine = reference
        ? `<div class="reference-line">` +
          `<span class="engine-reference" title="${escapeHtml(reference.title)}">` +
          `<span class="nowrap">${escapeHtml(reference.head)}</span> ` +
          `<span class="nowrap">${escapeHtml(reference.figures)}${note.button}</span>` +
          `</span></div>`
        : "";
      card.innerHTML =
        `<div class="engine-head">` +
          `<span class="engine-name">${escapeHtml(meta.label)}</span>` +
          (reference ? "" : note.button) +
          tag +
          modeBadge +
        `</div>` +
        referenceLine +
        note.body +
        `<div class="city-line">${cityLine}</div>` +
        (p.failed || p.skipped || p.abstain
          ? ""
          : `<div class="confidence-bar"><div class="fill" style="${fillStyle}"></div></div>`) +
        (topkRows ? `<div class="topk">${topkRows}</div>` : "") +
        evidenceLine +
        (meterLine ? `<div class="meta-line">${escapeHtml(meterLine)}</div>` : "");
      container.appendChild(card);
    }
  }
  // One footnote under the whole section, not one under each group, and
  // with it the sentences every card would otherwise carry.
  const provenance = referenceProvenance();
  const footnote = document.getElementById("reference-provenance");
  if (footnote) {
    if (provenance) {
      const note = infoNote("i-provenance", "where these figures come from",
                            escapeHtml(provenance.body));
      footnote.innerHTML = escapeHtml(provenance.line) + note.button + note.body;
    } else {
      footnote.textContent = "";
    }
    footnote.classList.toggle("hidden", !provenance);
  }
}

// Which places could not be pinned, named once each. One place can be both
// a level's fused prediction and its consensus, and naming it twice in a
// row read as two places with a comma between them.
function missingCoordinateSentence(missing) {
  const byCity = new Map();
  for (const item of missing) {
    if (!byCity.has(item.city)) byCity.set(item.city, new Map());
    const byBucket = byCity.get(item.city);
    if (!byBucket.has(item.bucket)) byBucket.set(item.bucket, []);
    byBucket.get(item.bucket).push(item.role);
  }
  const parts = [];
  for (const [city, byBucket] of byCity) {
    const roles = [...byBucket].map(([bucket, list]) =>
      `${bucket}-level ${list.join(" and ")}`);
    parts.push(`${city} (${roles.join(", ")})`);
  }
  return `No map coordinate for ${parts.join("; ")}.`;
}

function renderMap(ensembles, tri, perEngine) {
  if (markerLayer) markerLayer.clearLayers();
  if (radiusLayer) radiusLayer.clearLayers();
  _radiusRequest = null;
  _legendKeys = new Set();
  const points = [];
  const consensusPoints = [];
  const missing = [];
  const seen = new Map();  // coordinate key -> how many markers already there

  // One longitude frame for everything drawn, set by the first place the
  // flag compares. Chosen before any marker is placed, because the markers,
  // the arc, the radius and the fitted view all have to agree.
  let _refLon = null;
  const inFrame = (coords) => (_refLon == null ? coords : wrapOne(coords, _refLon));

  const place = (bucket, coords, popup, kind) => {
    if (!markerLayer) return;
    const key = coords.join(",");
    const index = seen.get(key) || 0;
    seen.set(key, index + 1);
    const marker = bucketMarker(bucket, inFrame(coords), popup, kind, index);
    marker.addTo(markerLayer);
    // Leaflet makes a marker focusable, so its name is the sentence above
    // rather than the bare glyph.
    const el = marker.getElement();
    if (el) el.setAttribute("aria-label", marker.options.title || "");
    _legendKeys.add(kind);
  };

  for (const name of [tri && tri.post_consensus_city,
                      tri && tri.user_consensus_city,
                      ensembles.post && ensembles.post.consensus_city,
                      ensembles.user && ensembles.user.consensus_city]) {
    const anchor = name ? coordFor(name) : null;
    if (anchor) { _refLon = anchor[1]; break; }
  }

  for (const bucket of ["post", "user"]) {
    const e = ensembles[bucket];
    if (!e) continue;
    const coords = coordFor(e.consensus_city);
    if (!coords) {
      // Don't drop the place silently: tell the operator it has no coordinate.
      missing.push({ city: e.consensus_city, bucket, role: "fused prediction" });
      continue;
    }
    // A prediction from a text that named no catalogue place is pinned in a
    // muted style and the map does not fly to it: the answer is still the
    // engines' answer, but it rests on language and topic cues.
    const placeless = namedAPlace(perEngine, bucket) === false;
    points.push({ bucket, coords, city: e.consensus_city, placeless });
    const popup =
      `<b>${cap(bucket)}-level fused prediction</b><br>` +
      `${escapeHtml(e.consensus_city)} (${(e.consensus_confidence*100).toFixed(1)}%, ${e.contributing_engines.length} engines)` +
      (placeless ? `<br>${NO_PLACE_STATEMENT}` : "");
    place(bucket, coords, popup, placeless ? "muted" : "fused");
  }

  // The flag compares the per-level consensus, not the fused prediction, so
  // the line joins the places it names. The hollow marker is always drawn,
  // offset when it shares a coordinate, so the legend never describes a
  // marker that is not there.
  for (const bucket of ["post", "user"]) {
    const city = bucket === "post" ? (tri && tri.post_consensus_city) : (tri && tri.user_consensus_city);
    if (!city) continue;
    const coords = coordFor(city);
    if (!coords) { missing.push({ city, bucket, role: "consensus" }); continue; }
    const placeless = namedAPlace(perEngine, bucket) === false;
    consensusPoints.push({ bucket, coords, city, placeless });
    const popup =
      `<b>${cap(bucket)}-level consensus</b><br>${escapeHtml(city)}<br>` +
      "This is the place the verification flag compares: each engine's top " +
      "prediction weighted by its confidence, which can differ from the fused prediction.";
    place(bucket, coords, popup, "consensus");
  }

  const flagRadiusKm = (window._lastResult?.manifest || {}).flag_radius_km;
  if (markerLayer && tri && consensusPoints.length >= 2) {
    // The great circle, sampled, so the line is the path the printed
    // distance describes and takes the short way. Drawn in the same
    // longitude frame as the markers, so a path over the Pacific stays over
    // the Pacific instead of jumping a full turn at the antimeridian.
    const arc = greatCircleArc(consensusPoints[0].coords, consensusPoints[1].coords);
    L.polyline(wrapForFit(arc, _refLon), {
      color: tri.disagreement_flag ? "#d1550f" : "#7c8794", weight: 2, dashArray: "6 6",
    }).addTo(markerLayer);
    _legendKeys.add("line");
    // The flag's radius around the post-level consensus, so a reader can
    // see whether the other place falls inside it.
    const postPoint = consensusPoints.find(p => p.bucket === "post");
    if (postPoint && flagRadiusKm) {
      _radiusRequest = { center: postPoint.coords, km: flagRadiusKm, refLon: _refLon };
    }
  }

  // Not flying to a prediction whose text named no catalogue place is a rule
  // about a lone prediction. Whenever the flag compares a pair, the view is
  // fitted to that pair, muted or not.
  const pairIsTheStory = Boolean(tri && (tri.disagreement_flag || consensusPoints.length >= 2));
  const anchored = points.concat(consensusPoints).filter(p => !p.placeless);
  const focus = pairIsTheStory
    ? consensusPoints.concat(points.filter(p => !p.placeless))
    : anchored;
  if (map && focus.length) {
    const cities = new Set(focus.map(p => p.city));
    if (cities.size === 1) {
      map.setView(inFrame(focus[0].coords), 9);
    } else {
      // Wrapped longitudes, so a Tokyo and San Francisco pair centres on the
      // Pacific rather than on Algeria. The padding follows the map's own
      // size: a fixed 40 px on a short map left 40 px of the 120 for the
      // pair, which zoomed the whole world into the frame.
      const size = map.getSize();
      const padding = [
        Math.max(10, Math.min(40, Math.round(size.x * 0.06))),
        Math.max(10, Math.min(40, Math.round(size.y * 0.12))),
      ];
      map.fitBounds(wrapForFit(focus.map(p => p.coords), _refLon), { padding });
    }
  } else if (map && points.length) {
    // Every prediction here is one whose text named no catalogue place. The
    // view does not close in on such a place, but it must not stay on the
    // previous query's place either, so it shows the region at a wide zoom.
    map.fitBounds(wrapForFit(points.map(p => p.coords), _refLon), { maxZoom: 5 });
  }

  _mappedPoints = points.length + consensusPoints.length;
  drawFlagRadius();
  updateLegend();

  const notice = document.getElementById("map-notice");
  if (missing.length) {
    notice.classList.remove("hidden");
    const note = infoNote("i-map-missing", "the missing coordinate",
      "Onboard the place with a latitude and longitude to pin it.");
    notice.innerHTML =
      escapeHtml(missingCoordinateSentence(missing)) + note.button + note.body;
  } else if (!MAP_READY) {
    showMapFallbackNote("The map could not be loaded, so the predictions and their " +
      "coordinates are listed below instead.");
  } else if (!tilesFailed) {
    notice.classList.add("hidden");
  }

  renderMapList(points, consensusPoints, tri);

  // Text equivalent of the map for screen readers.
  const summaryEl = document.getElementById("map-summary");
  if (summaryEl) {
    const parts = points.map(p => `${p.bucket}-level fused prediction: ${p.city}`);
    for (const p of consensusPoints) {
      if (!ensembles[p.bucket] || ensembles[p.bucket].consensus_city !== p.city) {
        parts.push(`${p.bucket}-level consensus: ${p.city}`);
      }
    }
    let txt = parts.length ? parts.join("; ") : "No mapped prediction.";
    if (tri && tri.disagreement_km != null) {
      const state = tri.disagreement_flag ? "flag raised" : "no flag raised";
      txt += `. The two consensus places are about ${Math.round(tri.disagreement_km)} km apart (${state}).`;
    } else if (points.length >= 2 && points[0].city === points[1].city) {
      txt += ". Both levels agree.";
    }
    summaryEl.textContent = txt;
  }
}

// The same predictions as a list, with their coordinates: the fallback when
// Leaflet or the tile host is unreachable, and the map's text equivalent.
function renderMapList(points, consensusPoints, tri) {
  const el = document.getElementById("map-list");
  const wrap = document.getElementById("map-list-wrap");
  if (!el || !wrap) return;
  const rows = [];
  for (const p of points) {
    const coords = `${p.coords[0].toFixed(4)}, ${p.coords[1].toFixed(4)}`;
    rows.push(`<li class="map-list-row"><span>${cap(p.bucket)}-level fused prediction: ` +
      `<b>${escapeHtml(p.city)}</b>${p.placeless ? " (no catalogue place named in the text)" : ""}` +
      `</span><span class="coords">${coords}</span></li>`);
  }
  for (const p of consensusPoints) {
    const same = points.some(q => q.bucket === p.bucket && q.city === p.city);
    if (same) continue;
    const coords = `${p.coords[0].toFixed(4)}, ${p.coords[1].toFixed(4)}`;
    rows.push(`<li class="map-list-row"><span>${cap(p.bucket)}-level consensus: ` +
      `<b>${escapeHtml(p.city)}</b></span><span class="coords">${coords}</span></li>`);
  }
  if (tri && tri.disagreement_km != null) {
    // The same figure, written the same way, as the flag's headline.
    rows.push(`<li class="map-list-row"><span>The two consensus places are ` +
      `${escapeHtml(kmText(tri.disagreement_km))} apart ` +
      `(${tri.disagreement_flag ? "flag raised" : "no flag raised"}).</span></li>`);
  }
  el.innerHTML = rows.join("");
  wrap.classList.toggle("hidden", !rows.length);
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
    labelLine.textContent = `${cap(bucket)}-level`;
    rowsEl.appendChild(labelLine);

    const pair = document.createElement("div");
    pair.className = "comparison-pair";
    const ensConf = (e.consensus_confidence * 100).toFixed(1);
    const bsConf = bestSingle ? (bestSingle.confidence * 100).toFixed(1) : "n/a";
    const bsLabel = engineMeta(e.best_single_engine).label;
    pair.innerHTML =
      `<div class="col">` +
        `<h4>Best single engine: ${escapeHtml(bsLabel)}</h4>` +
        `<div class="city">${escapeHtml(e.best_single_city)} <span class="conf">(${bsConf}%)</span></div>` +
      `</div>` +
      `<div class="col unified">` +
        `<h4>Fused prediction (${e.contributing_engines.length} engines)</h4>` +
        `<div class="city">${escapeHtml(e.consensus_city)} <span class="conf">(${ensConf}%)</span></div>` +
        `<div class="note">${e.differs_from_best_single ? "Differs" : "Same place"}</div>` +
      `</div>`;
    rowsEl.appendChild(pair);
  }
}

// ---------- candidate places ----------
// The closed catalogue, listed, so a visitor can see whether the place they
// are looking for was ever a candidate.
let _catalogueLoaded = false;

let _onboardedPlaces = [];

async function loadCatalogue(force = false) {
  if (_catalogueLoaded && !force) return;
  const listEl = document.getElementById("catalogue-list");
  const sizeEl = document.getElementById("catalogue-size");
  try {
    const resp = await fetch("/catalogue");
    if (!resp.ok) { listEl.textContent = "The candidate list could not be loaded."; return; }
    const data = await resp.json();
    sizeEl.textContent = data.size;
    // Every candidate's coordinate and identifier, from the server, so a pin
    // does not depend on which tab onboarded the place and an export can
    // join on something stable.
    rememberPlaces(data.places);
    rememberCoords(Object.fromEntries(
      data.places.filter(p => p.lat != null && p.lon != null).map(p => [p.name, [p.lat, p.lon]])
    ));
    _onboardedPlaces = data.places.filter(p => p.source === "onboarded");
    setOnboardTtlNote(data.onboarding_ttl_minutes);
    listEl.innerHTML = data.places.map(p => {
      const coord = (p.lat == null || p.lon == null)
        ? "no coordinate"
        : `${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}`;
      return `<div class="catalogue-row ${p.source === "onboarded" ? "onboarded" : ""}">` +
        `<span class="place">${escapeHtml(p.name)}</span>` +
        `<span class="origin">${escapeHtml(coord)}</span>` +
        `<span class="origin">${escapeHtml(p.source)}${escapeHtml(lifetimeText(p))}</span>` +
        `</div>`;
    }).join("");
    renderOnboardedPlaces();
    // Whether there is a place to remove has just changed.
    updateResetVisibility();
    _catalogueLoaded = true;
  } catch (e) {
    listEl.textContent = `The candidate list could not be loaded: ${e.message || e}.`;
  }
}

// How long an onboarded place lasts on this deployment. The consent line
// above it is the instruction; the figure is behind its icon.
function setOnboardTtlNote(ttlMinutes) {
  const el = document.getElementById("onboard-ttl-note");
  if (!el) return;
  if (ttlMinutes == null) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.textContent = ttlMinutes > 0
    ? `An onboarded place expires after ${Math.round(ttlMinutes)} min on this instance.`
    : "An onboarded place does not expire on this instance.";
}

function lifetimeText(place) {
  const parts = [];
  if (place.age_minutes != null) {
    const age = Math.round(place.age_minutes);
    parts.push(age < 1 ? "onboarded just now" : `onboarded about ${age} min ago`);
  }
  if (place.expires_in_minutes != null) {
    parts.push(`expires in about ${Math.round(place.expires_in_minutes)} min`);
  }
  return parts.length ? `, ${parts.join(", ")}` : "";
}

// Onboarded places on the main view: they are in every visitor's catalogue
// and they expire, and the collapsed candidate list hides both facts.
function renderOnboardedPlaces() {
  const el = document.getElementById("onboarded-places");
  if (!el) return;
  if (!_onboardedPlaces.length) { el.classList.add("hidden"); return; }
  const rows = _onboardedPlaces.map(p =>
    `<div class="onboarded-row"><span class="place">${escapeHtml(p.name)}</span>` +
    `<span class="origin">${escapeHtml(lifetimeText(p).replace(/^, /, ""))}</span></div>`
  ).join("");
  el.classList.remove("hidden");
  const note = infoNote("i-onboarded-head", "what an onboarded place is",
    "Each of these is a candidate for every visitor until it expires.");
  el.innerHTML =
    `<div class="onboarded-head">Onboarded on this shared instance ` +
    `(${_onboardedPlaces.length})${note.button}${note.body}</div>` + rows;
}

function isOnboarded(city) {
  const target = (city || "").trim().toLowerCase();
  return _onboardedPlaces.some(p => p.name.toLowerCase() === target);
}

document.getElementById("catalogue-details").addEventListener("toggle", (event) => {
  if (event.target.open) loadCatalogue(true);
});

// ---------- onboarding ----------

// ---------- edit rights on an onboarded place ----------
// POST /onboard returns an edit_token where the deployment requires one for
// PUT and DELETE. It is kept per place, in memory and in sessionStorage, so
// only the browser that drafted a place can change it.
const EDIT_TOKEN_KEY = "geolens.editTokens";

function placeKey(place) { return String(place || "").trim().toLowerCase(); }

function loadEditTokens() {
  try { return JSON.parse(sessionStorage.getItem(EDIT_TOKEN_KEY) || "{}") || {}; }
  catch (e) { return {}; }
}

const EDIT_TOKENS = loadEditTokens();

function saveEditTokens() {
  try { sessionStorage.setItem(EDIT_TOKEN_KEY, JSON.stringify(EDIT_TOKENS)); }
  catch (e) { /* the in-memory copy still serves this tab */ }
}

function rememberEditToken(place, token) {
  if (!place || !token) return;
  EDIT_TOKENS[placeKey(place)] = token;
  saveEditTokens();
}

function forgetEditToken(place) {
  delete EDIT_TOKENS[placeKey(place)];
  saveEditTokens();
}

function editTokenFor(place) { return EDIT_TOKENS[placeKey(place)] || ""; }

function editHeaders(place) {
  const headers = { "Content-Type": "application/json" };
  const token = editTokenFor(place);
  if (token) headers["X-GeoLens-Edit-Token"] = token;
  return headers;
}

function isEditTokenRefusal(status, body) {
  if (status !== 403) return false;
  const code = ((body || {}).error || {}).code || "";
  return code === "edit_token_required" || code === "edit_token_invalid";
}

// The drafted coordinate on the map, live while the operator edits it, so a
// centroid on the wrong side of the equator is visible before it is saved.
// `draftMarker` and `draftCircle` are declared in the map section above.

function showDraftPin(name, lat, lon) {
  if (!map) return;
  clearDraftPin();
  if (lat == null || lon == null || Number.isNaN(lat) || Number.isNaN(lon)) return;
  const label = `Drafted coordinate for ${name}`;
  draftMarker = L.marker([lat, lon], {
    title: label,
    alt: label,
    icon: L.divIcon({
      html: `<div class="marker-hit"><div class="engine-marker draft" aria-hidden="true">D</div></div>`,
      className: "", iconSize: [MARKER_BOX_PX, MARKER_BOX_PX],
      iconAnchor: [MARKER_BOX_PX / 2, MARKER_BOX_PX / 2],
    }),
  }).bindPopup(
    `<b>${escapeHtml(name)}</b><br>Drafted coordinate ${lat.toFixed(4)}, ${lon.toFixed(4)}.` +
    "<br>This is what will be saved. Move the latitude and longitude fields until the pin " +
    "sits on the place."
  ).addTo(map);
  const el = draftMarker.getElement();
  if (el) el.setAttribute("aria-label", label);
  draftCircle = L.polygon(geodesicCircle([lat, lon], 1), {
    color: "#6d28d9", fillColor: "#6d28d9", fillOpacity: 0.06, weight: 1,
  }).addTo(map);
  map.setView([lat, lon], 10);
  updateLegend();
}

function clearDraftPin() {
  if (draftMarker) { draftMarker.remove(); draftMarker = null; }
  if (draftCircle) { draftCircle.remove(); draftCircle = null; }
  updateLegend();
}

// True between a successful save and the next edit of the coordinate, so
// the hint stops asking for a check that has already happened.
let _coordinateSaved = false;

// The hint asks for a check only once there is a pin to check.
function updateCoordHint() {
  const el = document.getElementById("coord-hint-text");
  if (!el) return;
  const lat = parseFloat(document.getElementById("onboard_lat").value);
  const lon = parseFloat(document.getElementById("onboard_lon").value);
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    el.textContent = "No coordinate yet.";
    return;
  }
  el.textContent = _coordinateSaved
    ? `Saved at ${lat}, ${lon}.`
    : "Check the pin before saving.";
}

function draftPinFromFields() {
  const name = document.getElementById("onboard_city").value.trim() || "the drafted place";
  const lat = parseFloat(document.getElementById("onboard_lat").value);
  const lon = parseFloat(document.getElementById("onboard_lon").value);
  // An edited coordinate is no longer the saved one.
  _coordinateSaved = false;
  updateCoordHint();
  if (Number.isNaN(lat) || Number.isNaN(lon)) { clearDraftPin(); return; }
  showDraftPin(name, lat, lon);
}

for (const id of ["onboard_lat", "onboard_lon"]) {
  document.getElementById(id).addEventListener("input", draftPinFromFields);
}

// What the onboarding form is saying right now, in the panel's own
// `role="status"` beside the form.
function showOnboardMessage(text, isError = false) {
  const el = document.getElementById("onboard-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("error", Boolean(isError));
  el.classList.remove("hidden");
  moveFocusTo(el);
}

async function runOnboard(city, forceRefresh) {
  const btn = document.getElementById("onboard-btn");
  const region = document.getElementById("onboard_region").value.trim();
  // As with a query: a draft the visitor has left behind does not reopen
  // the form or take the focus back.
  const token = _exitCount;
  return withBusy(btn, "Drafting a profile…", async () => {
    try {
      const resp = await fetch("/onboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ city, region, force_refresh: !!forceRefresh }),
      });
      readQuota(resp);
      if (!runIsCurrent(token)) return false;
      if (!resp.ok) {
        _lastFailureClause = `the server answered ${resp.status}`;
        showOnboardMessage(
          httpErrorMessage(resp.status, await resp.text().catch(() => ""),
                           resp.headers.get("Retry-After")),
          true);
        document.getElementById("onboard-cards").classList.add("hidden");
        return false;
      }
      const drafted = await resp.json();
      // The token is kept whatever happened in between: it is what lets this
      // browser remove the place the call has just added.
      rememberEditToken(drafted.name || city, drafted.edit_token);
      if (!runIsCurrent(token)) { loadCatalogue(true); return false; }
      populateOnboardCards(drafted);
      loadCatalogue(true);
      // The five-field form has just appeared below the button. Focus goes
      // to the first field the operator is meant to check.
      moveFocusTo(document.getElementById("onboard_aliases"));
      return true;
    } catch (e) {
      _lastFailureClause = "the server could not be reached";
      showOnboardMessage(`Could not draft a profile: ${e.message || e}.`, true);
      return false;
    }
  });
}

// Removes an onboarded place from the live catalogue and discards its
// profile. The server refuses this for a built-in place, and, where the
// deployment requires an edit token, for a place another browser drafted:
// that answer comes back as "refused", and the caller shows the place as it
// is. Null means the request did not go through.
async function resetOnboarded(city) {
  try {
    const resp = await fetch("/onboard", {
      method: "DELETE",
      headers: editHeaders(city),
      body: JSON.stringify({ city, edit_token: editTokenFor(city) || undefined }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      const refused = isEditTokenRefusal(resp.status, jsonBody(text));
      // The listing carries the expiry the message quotes.
      if (refused) await loadCatalogue(true);
      showDemoError(
        httpErrorMessage(resp.status, text, resp.headers.get("Retry-After"), city),
        { keepResults: true });
      if (refused) return "refused";
      _lastFailureClause = `the server answered ${resp.status}`;
      return null;
    }
    readQuota(resp);
    const change = await resp.json();
    forgetEditToken(city);
    loadCatalogue(true);
    delete SERVER_COORDS[city];
    clearDraftPin();
    document.getElementById("onboard-cards").classList.add("hidden");
    return change;
  } catch (e) {
    _lastFailureClause = "the server could not be reached";
    showDemoError(`Could not reach the server: ${e.message || e}.`, { keepResults: true });
    return null;
  }
}

// How long this onboarding lasts and how full the shared catalogue is: the
// figures on the line, and what sharing means behind the icon.
const SHARING_NOTE =
  "This instance is shared, so every visitor sees the places onboarded here. " +
  "When the slots are full the oldest onboarding is evicted to make room.";

function sharingFigures(change) {
  const parts = [];
  if (change.expires_in_minutes != null) {
    parts.push(`expires in ${Math.round(change.expires_in_minutes)} min`);
  } else if (change.onboarding_ttl_minutes === 0) {
    parts.push("no expiry on this instance");
  }
  if (change.onboarded_cap) {
    parts.push(`${change.onboarded_count} of ${change.onboarded_cap} slots`);
  }
  if (change.evicted && change.evicted.length) {
    parts.push(`${change.evicted.join(", ")} evicted`);
  }
  return parts.length ? ` Shared: ${parts.join(" · ")}.` : "";
}

// One status line with one note under it, whatever the two callers add.
function setOnboardStatus(headline, change, extra) {
  const el = document.getElementById("onboard-status");
  el.classList.remove("error", "hidden");
  const figures = sharingFigures(change);
  const body = [extra, figures ? SHARING_NOTE : ""].filter(Boolean).join(" ");
  if (!body) { el.textContent = headline + figures; return; }
  const note = infoNote("i-onboard-status", "this onboarding", escapeHtml(body));
  el.innerHTML = escapeHtml(headline + figures) + note.button + note.body;
}

// Onboard puts the place in the catalogue with the drafted profile, so the
// note on the line that reports it says what Save and use then does.
const DRAFT_IS_LIVE =
  "Save and use replaces the drafted profile with your corrections.";

function showOnboardStatus(city, change, extra = "") {
  setOnboardStatus(
    `${city}: ${change.catalogue_note} (catalogue now ${change.catalogue_size} places).`,
    change, extra);
}

document.getElementById("onboard-btn").addEventListener("click", () => {
  const city = document.getElementById("onboard_city").value.trim();
  if (!city) {
    showOnboardMessage(
      "No place name. Type the place to onboard in the box above the button, " +
      "for example Bidadari Estate.", true);
    return;
  }
  runOnboard(city, false);
});
document.getElementById("onboard-regen-btn").addEventListener("click", () => {
  const city = document.getElementById("onboard_city").value.trim();
  if (!city) {
    showOnboardMessage("No place name. Type the place to draft again in the box above.", true);
    return;
  }
  runOnboard(city, true);
});
async function saveOnboardProfile() {
  clearDemoError();
  let profile;
  try {
    profile = readOnboardCards();
  } catch (e) {
    showOnboardMessage(e.message || String(e), true);  // coordinate check failed
    return false;
  }
  const btn = document.getElementById("onboard-save-btn");
  return withBusy(btn, "Saving…", async () => {
    try {
      const resp = await fetch("/onboard", {
        method: "PUT",
        headers: editHeaders(profile.name),
        body: JSON.stringify(
          Object.assign({}, profile, { edit_token: editTokenFor(profile.name) || undefined })),
      });
      readQuota(resp);
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        const refused = isEditTokenRefusal(resp.status, jsonBody(text));
        if (refused) await loadCatalogue(true);
        showOnboardMessage(
          httpErrorMessage(resp.status, text, resp.headers.get("Retry-After"),
                           profile.name),
          true);
        // This browser did not draft the place, so the run continues with
        // the place as it already is.
        if (refused) return true;
        _lastFailureClause = `the server answered ${resp.status}`;
        return false;
      }
      const saved = await resp.json();
      loadCatalogue(true);
      // The coordinate in the form is the saved one until it is edited
      // again, so its hint stops asking for a check.
      _coordinateSaved = true;
      populateOnboardCards(saved, false);
      // The place is in the catalogue now, so it pins as a prediction like
      // any other; leaving the draft pin behind only clutters the map.
      clearDraftPin();
      setOnboardStatus(
        `Saved. ${saved.name} is 1 of ${saved.catalogue_size} candidates. ` +
        "Query it on a geolocation tab.",
        saved, "Every engine can now pick it.");
      moveFocusTo(document.getElementById("onboard-status"));
      return true;
    } catch (e) {
      _lastFailureClause = "the server could not be reached";
      showOnboardMessage(`Could not save the profile: ${e.message || e}.`, true);
      return false;
    }
  });
}

// The corrections a scenario's operator makes to the draft. They live in the
// scenario file, so the tile, the recorder and scripts/check_scenarios.py
// apply the same edit, and they are typed one field at a time and then
// saved, because the review step is what the scenario is about.
async function applyOperatorEdits(sc) {
  const edits = sc.operator_edits;
  if (!edits) return true;
  const cards = document.getElementById("onboard-cards");
  scrollIntoViewGently(cards, "center");
  const step = sc.field_type_ms || FIELD_TYPE_MS;

  for (const field of ["aliases", "landmarks", "foods", "slang"]) {
    if (!Array.isArray(edits[field])) continue;
    const box = cards.querySelector(`textarea[data-field="${field}"]`);
    box.value = edits[field].join(", ");
    box.classList.add("edited");
    await pause(step);
  }
  if (typeof edits.notes === "string") {
    const box = cards.querySelector('textarea[data-field="notes"]');
    box.value = edits.notes;
    box.classList.add("edited");
    await pause(step);
  }
  if (edits.lat != null) {
    const box = document.getElementById("onboard_lat");
    box.value = edits.lat;
    box.classList.add("edited");
  }
  if (edits.lon != null) {
    const box = document.getElementById("onboard_lon");
    box.value = edits.lon;
    box.classList.add("edited");
  }
  await pause(step);
  return saveOnboardProfile();
}

document.getElementById("onboard-save-btn").addEventListener("click", saveOnboardProfile);

// `withStatus` is false when a save triggered this. A save always reports
// already_present, because the draft put the place in the catalogue a moment
// earlier, and overwriting the cold-start line with that reads as though the
// place had been a candidate all along.
// `source` is a stored field value; this is what a reader should see.
const SOURCE_LABEL = {
  stub: "offline placeholder",
  openai: "drafting model",
  edited: "operator edit",
};

function populateOnboardCards(profile, withStatus = true) {
  // A fresh draft is not a saved profile, whatever the last save left.
  if (withStatus) _coordinateSaved = false;
  const cards = document.getElementById("onboard-cards");
  cards.classList.remove("hidden");
  cards.querySelectorAll(".edited").forEach(el => el.classList.remove("edited"));
  const noCoordinate = profile.lat == null || profile.lon == null;
  const sourceNote = infoNote(
    "i-onboard-source", "where this draft came from",
    "The source says where this draft came from. \"drafting model\" means an " +
    "LLM wrote it, \"offline placeholder\" means no API key was set, and " +
    "\"operator edit\" means it was saved from this form." +
    (noCoordinate
      ? " Add a latitude and longitude below so the place pins on the map."
      : ""));
  cards.querySelector(".onboard-source").innerHTML =
    escapeHtml(`${profile.name} · ${SOURCE_LABEL[profile.source] || profile.source}` +
               (noCoordinate ? ". No coordinate yet." : "")) +
    sourceNote.button + sourceNote.body;
  if (withStatus && profile.catalogue_note) {
    showOnboardStatus(profile.name, profile, DRAFT_IS_LIVE);
  }
  const warnEl = document.getElementById("onboard-warnings");
  const warnings = profile.warnings || [];
  warnEl.innerHTML = warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
  warnEl.classList.toggle("hidden", warnings.length === 0);
  for (const field of ["aliases", "landmarks", "foods", "slang"]) {
    cards.querySelector(`textarea[data-field="${field}"]`).value = (profile[field] || []).join(", ");
  }
  cards.querySelector('textarea[data-field="notes"]').value = profile.notes || "";
  if (profile.region) document.getElementById("onboard_region").value = profile.region;
  document.getElementById("onboard_lat").value = profile.lat == null ? "" : profile.lat;
  document.getElementById("onboard_lon").value = profile.lon == null ? "" : profile.lon;
  updateCoordHint();
  // Register the coordinate so this tab can pin the place immediately; every
  // other tab gets it from the server with the next response.
  if (profile.lat != null && profile.lon != null) {
    SERVER_COORDS[profile.name] = [profile.lat, profile.lon];
    showDraftPin(profile.name, profile.lat, profile.lon);
  } else {
    clearDraftPin();
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
    throw new Error("Latitude looks like a longitude. Did you swap the two fields?");
  }
  return {
    name: document.getElementById("onboard_city").value.trim(),
    region: document.getElementById("onboard_region").value.trim(),
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
  if (km == null) return "n/a";
  if (km >= 1000) return `${(km / 1000).toFixed(1)}k km`;
  return `${km.toFixed(0)} km`;
}

function ciSpan(ci) {
  if (!ci || ci.length < 2) return "";
  return `<span class="ci">[${(ci[0]*100).toFixed(0)}, ${(ci[1]*100).toFixed(0)}]</span>`;
}

function cap(s) {
  return String(s).charAt(0).toUpperCase() + String(s).slice(1);
}

const HTML_ESCAPES = {
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
};
// Constructed rather than a literal, for the reason given above csvCell.
const HTML_UNSAFE = new RegExp("[&<>\"']", "g");

function escapeHtml(s) {
  return String(s).replace(HTML_UNSAFE, c => HTML_ESCAPES[c]);
}

// ---------- the five view tabs ----------
// One tablist over the three geolocation tasks, the onboarding form and the
// bulk run, with a roving tabindex: one tab stop, arrows move inside it. The
// first four share `#panel-demo`; a block inside it carries `data-task`, the
// list of tabs it belongs to, and this function shows or hides it.

const MODE_KEY = "geolens.mode";
const modeTabs = [...document.querySelectorAll(".mode-tab")];
// The tab row is the list of views. The modes, the names the result pane
// prints and the two groupings below all come off the buttons, so the list
// is written once, in index.html.
const MODES = modeTabs.map(t => t.dataset.mode);
const TAB_LABELS = Object.fromEntries(
  modeTabs.map(t => [t.dataset.mode, t.textContent.trim()]));
// The four tabs that share `#panel-demo`, and the three of those that send a
// query. The bulk tab and the onboarding form drive a form of their own.
const PANEL_MODES = MODES.filter(m => m !== "batch");
const QUERY_MODES = PANEL_MODES.filter(m => m !== "onboard");

// What each box is called on each tab. Where one box is on screen it is
// named after itself; where both are, each is named after its task.
const FIELD_LABELS = {
  post: { post: "Post text" },
  user: { user_posts: "Recent posts, one per line" },
  verify: {
    post: "Post Geolocation",
    user_posts: "User Geolocation (Recent posts, one per line)",
  },
};

// An earlier visit stored "demo", the single query view these three tabs
// replaced, and it opens on post geolocation.
function knownMode(mode) {
  return MODES.includes(mode) ? mode : "post";
}

let CURRENT_MODE = "post";

function modeMatches(list, mode) {
  return String(list || "").split(/\s+/).filter(Boolean).includes(mode);
}

// A note whose button has gone off the tab is closed with it, so no button
// reports itself expanded while it is out of the accessibility tree, and no
// note is left explaining the tab the reader has left.
function closeNotesOffThisTab() {
  document.querySelectorAll('.info-btn[aria-expanded="true"]').forEach(button => {
    if (button.closest(".hidden") || button.closest(".replay-off")) {
      setInfoOpen(button, false);
    }
  });
}

// `persist` is true only for a tab the visitor picked; a preset step and a
// recorded run move the tabs themselves, and neither is a choice to store.
// `announce` names the new tab in the page's status line, because the tab
// row's own selection is not announced when nobody pressed a tab.
function applyMode(mode, { persist = false, announce = false } = {}) {
  const previous = CURRENT_MODE;
  CURRENT_MODE = knownMode(mode);
  // A refusal names the boxes of the tab it was raised on, so it does not
  // travel to a tab that does not show them.
  clearDemoError();
  modeTabs.forEach(t => {
    const on = t.dataset.mode === CURRENT_MODE;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
    // Roving tabindex: one stop for the whole tablist, arrows move inside it.
    t.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll("[data-task]").forEach(el => {
    const on = modeMatches(el.dataset.task, CURRENT_MODE);
    // A note's body is opened by its own button, so this tab only takes it
    // off the page and never puts it back.
    if (on && el.classList.contains("info-body")) return;
    el.classList.toggle("hidden", !on);
  });
  // Four tabs point at the same panel, so the panel names whichever of them
  // is selected. The bulk tab is not one of them, and the panel falls back
  // to the first of the four rather than keeping the tab last open.
  const panel = document.getElementById("panel-demo");
  if (panel) {
    const names = PANEL_MODES.includes(CURRENT_MODE) ? CURRENT_MODE : PANEL_MODES[0];
    panel.setAttribute("aria-labelledby", `tab-${names}`);
  }
  for (const [id, text] of Object.entries(FIELD_LABELS[CURRENT_MODE] || {})) {
    const label = document.querySelector(`label[for="${id}"]`);
    if (label) label.textContent = text;
  }
  updateResetVisibility();
  closeNotesOffThisTab();
  updateResultSource();
  if (announce && CURRENT_MODE !== previous) {
    const said = document.getElementById("tab-announce");
    if (said) said.textContent = `Now on ${TAB_LABELS[CURRENT_MODE] || CURRENT_MODE}.`;
  }
  if (persist) {
    try { localStorage.setItem(MODE_KEY, CURRENT_MODE); } catch (e) { /* ignore */ }
  }
}

// A recorded run takes the tabs it cannot drive off the page, so the arrows
// walk the tabs that are on screen.
function reachableTabs() {
  const shown = modeTabs.filter(t => t.offsetParent !== null);
  return shown.length ? shown : modeTabs;
}

// A tab the visitor picks is a choice: it is stored, and it ends a running
// preset, so no later step moves them back off it.
function chooseTab(mode) {
  stopPresetByHand();
  applyMode(mode, { persist: true });
}

modeTabs.forEach((tab) => {
  tab.addEventListener("click", () => chooseTab(tab.dataset.mode));
  tab.addEventListener("keydown", (event) => {
    const tabs = reachableTabs();
    const i = tabs.indexOf(tab);
    if (i < 0) return;
    let next = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = tabs[(i + 1) % tabs.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = tabs[(i - 1 + tabs.length) % tabs.length];
    } else if (event.key === "Home") {
      next = tabs[0];
    } else if (event.key === "End") {
      next = tabs[tabs.length - 1];
    }
    if (!next) return;
    event.preventDefault();
    chooseTab(next.dataset.mode);
    next.focus();
  });
});

// Restore the last tab on load. The landing tab is post geolocation, and an
// older stored name is written back in its new spelling.
let initialMode = "post";
try { initialMode = knownMode(localStorage.getItem(MODE_KEY) || "post"); }
catch (e) { /* ignore */ }
applyMode(initialMode, { persist: true });

// ---------- batch eval ----------

let _lastBatchResponse = null;
let _lastFileName = "results.csv";

const BUNDLED_EXAMPLE = "example_test_set.csv";

// The headline of the file the numbers came from, and what the bundled
// example is for, behind the icon.
const BUNDLED_EXAMPLE_NOTE =
  "These rows are hand-written to stress " +
  "the workbench; they are not the WNUT-2016 evaluation the paper reports.";

function fileLabel(name) {
  if (name === BUNDLED_EXAMPLE) {
    return "Authored example, 50 rows. Not the paper's evaluation.";
  }
  return `Uploaded file: ${name}.`;
}

function fileNoteMarkup(name) {
  const label = escapeHtml(fileLabel(name));
  if (name !== BUNDLED_EXAMPLE) return label;
  const note = infoNote("i-file-note", "the bundled example",
                        escapeHtml(BUNDLED_EXAMPLE_NOTE));
  return label + note.button + note.body;
}

const fileInput = document.getElementById("batch-file");
const runBtn = document.getElementById("batch-run-btn");
const rowCountEl = document.getElementById("batch-rowcount");
const statusEl = document.getElementById("batch-status");
function showBatchStatus(msg, isError) {
  statusEl.classList.remove("hidden", "error");
  if (isError) statusEl.classList.add("error");
  statusEl.textContent = msg;
}
function clearBatchStatus() {
  statusEl.classList.add("hidden");
  statusEl.classList.remove("error");
  statusEl.textContent = "";
  showRunWarnings([]);
}

// What the server warns about this run, for example a row id that reads as
// an account handle.
function showRunWarnings(warnings) {
  const el = document.getElementById("batch-warnings");
  if (!el) return;
  const list = Array.isArray(warnings) ? warnings.filter(Boolean) : [];
  el.innerHTML = list.map(w => `<li>${escapeHtml(w)}</li>`).join("");
  el.classList.toggle("hidden", list.length === 0);
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
  const note = infoNote(
    "i-rowcount", "what a ground-truth column changes",
    "A ground-truth column makes the run scored, giving accuracy, rank, " +
    "distance error and the flag's precision and recall. Without one, the " +
    "run returns predictions and the distribution table.");
  rowCountEl.innerHTML =
    escapeHtml(`${dataRows} rows · ${hasGT ? "scored" : "predictions only"}`) +
    note.button + note.body;
  runBtn.disabled = dataRows === 0;
  return { dataRows, hasGT };
}

fileInput.addEventListener("change", async () => {
  // A new file starts clean: the previous run's error is not this file's.
  clearBatchStatus();
  const f = fileInput.files[0];
  if (!f) { rowCountEl.textContent = ""; runBtn.disabled = true; return; }
  _lastFileName = f.name.replace(/\.csv$/i, "_results.csv");
  document.getElementById("batch-file-note").innerHTML = fileNoteMarkup(f.name);
  document.getElementById("batch-file-note").classList.remove("hidden");
  await previewCsv(f);
});

runBtn.addEventListener("click", async () => {
  const f = fileInput.files[0];
  if (!f || runBtn.dataset.busy === "1") return;
  await ensureInstance();
  const { hasGT, dataRows } = await previewCsv(f);
  const endpoint = hasGT ? "/eval_csv" : "/batch_predict_csv";
  const engines = selectedEngines("batch-engine-preset");
  const engineCount = engineCountLabel("batch-engine-preset");
  statusEl.classList.remove("hidden", "error");
  statusEl.textContent = `Running ${dataRows} rows through ${engineCount} engines…`;
  summaryWrap.classList.add("hidden");
  rowsWrap.classList.add("hidden");
  document.getElementById("batch-downloads").classList.add("hidden");

  const fd = new FormData();
  fd.append("file", f);
  if (engines) fd.append("engines", engines.join(","));

  await withBusy(runBtn, runningLabel("batch-engine-preset"), async () => {
    try {
      const resp = await fetch(endpoint, { method: "POST", body: fd });
      readQuota(resp);
      if (!resp.ok) {
        _lastBatchResponse = null;
        summaryWrap.classList.add("hidden");
        rowsWrap.classList.add("hidden");
        showRunWarnings([]);
        document.getElementById("batch-rollup").classList.add("hidden");
        statusEl.textContent = httpErrorMessage(resp.status, await resp.text(),
                                                resp.headers.get("Retry-After"));
        statusEl.classList.add("error");
        moveFocusTo(statusEl);
        return;
      }
      const data = await resp.json();
      _lastBatchResponse = data;
      rememberCoords(data.place_coordinates);
      rememberPlaces(data.places);
      showSpendNotice(data.spend);
      showRunWarnings(data.warnings);
      statusEl.textContent = `Done. ${data.rows.length} rows processed.`;
      renderBatchResults(data, hasGT);
    } catch (e) {
      statusEl.textContent = `Network error: ${e}`;
      statusEl.classList.add("error");
      moveFocusTo(statusEl);
    }
  });
});

// Which predictions the distribution table counts: the fused prediction per
// level, or one engine's.
function rollupOptions(data) {
  const select = document.getElementById("rollup-source");
  if (!select) return;
  const names = Object.keys((data.rows.find(r => r.per_engine &&
    Object.keys(r.per_engine).length) || {}).per_engine || {});
  const current = select.value;
  select.innerHTML =
    `<option value="fused">Fused prediction, per level</option>` +
    names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(engineMeta(n).label)}</option>`).join("");
  select.value = [...select.options].some(o => o.value === current) ? current : "fused";
}

// Counts for one engine, computed from the rows. Rows an engine abstained
// on, failed on or was not selected for are not counted anywhere.
function engineRollup(data, engine) {
  const counts = {};
  const level = engineMeta(engine).granularity === "user" ? "user" : "post";
  for (const r of data.rows) {
    const p = r.per_engine?.[engine];
    if (!p || p.skipped || p.failed || p.abstain || !p.city) continue;
    const gz = r.per_engine?.[`gazetteer_${level}`];
    const named = !gz || gz.skipped || gz.failed ? null : !gz.abstain;
    const c = counts[p.city] || (counts[p.city] = {
      city: p.city, post_count: 0, user_count: 0,
      post_named: 0, post_unnamed: 0, user_named: 0, user_unnamed: 0,
    });
    c[`${level}_count`] += 1;
    if (named === true) c[`${level}_named`] += 1;
    else if (named === false) c[`${level}_unnamed`] += 1;
  }
  return Object.values(counts)
    .sort((a, b) => (b.post_count + b.user_count) - (a.post_count + a.user_count));
}

function renderRollup(data) {
  const wrap = document.getElementById("batch-rollup");
  const table = wrap.querySelector("table");
  const tbody = table.querySelector("tbody");
  const tfoot = table.querySelector("tfoot");
  const source = document.getElementById("rollup-source").value;
  const rollup = source === "fused" ? (data.rollup || []) : engineRollup(data, source);
  const sourceNote = document.getElementById("rollup-source-note");
  if (sourceNote) {
    sourceNote.textContent = source === "fused"
      ? "Counted from the fused prediction at each level."
      : `Counted from ${engineMeta(source).label} alone.`;
  }
  if (!rollup.length) { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");
  tbody.innerHTML = "";
  const totals = { post: 0, user: 0, post_named: 0, post_unnamed: 0, user_named: 0, user_unnamed: 0 };
  for (const c of rollup) {
    totals.post += c.post_count;
    totals.user += c.user_count;
    totals.post_named += c.post_named || 0;
    totals.post_unnamed += c.post_unnamed || 0;
    totals.user_named += c.user_named || 0;
    totals.user_unnamed += c.user_unnamed || 0;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<th scope="row">${escapeHtml(c.city)}</th>` +
      `<td>${c.post_count}</td>` +
      `<td>${c.post_named || 0}</td>` +
      `<td>${c.post_unnamed || 0}</td>` +
      `<td>${c.user_count}</td>` +
      `<td>${c.user_named || 0}</td>` +
      `<td>${c.user_unnamed || 0}</td>`;
    tbody.appendChild(tr);
  }
  // Separate totals per level: a post placement and a user placement are
  // counted over different sets of rows.
  tfoot.innerHTML =
    `<tr><th scope="row">Total post-level placements</th><td><b>${totals.post}</b></td>` +
    `<td>${totals.post_named}</td><td>${totals.post_unnamed}</td><td colspan="3"></td></tr>` +
    `<tr><th scope="row">Total user-level placements</th><td colspan="3"></td>` +
    `<td><b>${totals.user}</b></td><td>${totals.user_named}</td><td>${totals.user_unnamed}</td></tr>`;
}

document.getElementById("rollup-source").addEventListener("change", () => {
  if (_lastBatchResponse) renderRollup(_lastBatchResponse);
});

// Below this many rows a rate is noise, so the interface prints the counts
// and neither an interval nor a colour.
const MIN_ROWS_FOR_A_RATE = 10;

// The column glosses of the bulk tables are `title=` attributes, which a
// touch screen never shows. One note per table lists the same strings, read
// off those same attributes so the two cannot drift.
function fillColumnNote(tableSelector, bodyId) {
  const table = document.querySelector(tableSelector);
  const body = document.getElementById(bodyId);
  if (!table || !body) return;
  const rows = [];
  for (const th of table.querySelectorAll("thead th")) {
    const carrier = th.getAttribute("title") ? th : th.querySelector("[title]");
    const gloss = carrier && carrier.getAttribute("title");
    if (!gloss) continue;
    rows.push(`<dt>${escapeHtml(th.textContent.trim())}</dt>` +
              `<dd>${escapeHtml(gloss)}.</dd>`);
  }
  body.innerHTML = rows.length ? `<dl class="info-list">${rows.join("")}</dl>` : "";
  const wrap = body.closest(".table-note");
  if (wrap) wrap.classList.toggle("hidden", !rows.length);
}

function fillColumnNotes() {
  fillColumnNote("#engine-summary-table", "i-engine-columns");
  fillColumnNote("#batch-summary .ensemble-table", "i-fusion-columns");
  fillColumnNote("#batch-rollup .rollup-table", "i-rollup-columns");
}

fillColumnNotes();

function rateCell(hits, n, ci) {
  if (!n) return "<td>n/a</td>";
  if (n < MIN_ROWS_FOR_A_RATE) {
    return `<td title="Too few rows for a rate">${Math.round(hits * n)} of ${n}</td>`;
  }
  return `<td>${(hits * 100).toFixed(1)}%${ciSpan(ci)}</td>`;
}

function rankCell(m) {
  if (!m.n_rank_found) return `<td>found 0 of ${m.n_evaluated}</td>`;
  return `<td>${m.mean_rank_found.toFixed(1)} <span class="ci">found ${m.n_rank_found} of ${m.n_evaluated}</span></td>`;
}

function distanceCell(m) {
  if (!m.n_geo) return `<td title="No row where this engine named a place">n/a</td>`;
  return `<td>${fmtKm(m.median_error_km)}</td>`;
}

function renderBatchResults(data, withSummary) {
  rollupOptions(data);
  renderRollup(data);
  renderBatchModeBanner(data.manifest || {});
  // Every run has rows, so the per-row exports are always offered; only the
  // metrics table and the LaTeX export need a ground-truth column.
  document.getElementById("batch-downloads").classList.remove("hidden");
  document.getElementById("dl-summary-tex").classList.toggle(
    "hidden", !(withSummary && data.summary));
  document.getElementById("geojson-coverage").innerHTML = geojsonCoverage(data);
  if (withSummary && data.summary) {
    summaryWrap.classList.remove("hidden");
    const s = data.summary;
    const meta1 =
      `${s.total_rows} rows · ${s.evaluated_rows} evaluated · ${s.ooc_rows} out-of-catalogue · ${s.error_rows} errors` +
      (s.catalogue_size ? ` · N=${s.catalogue_size} candidates` : "");
    const metaNote = infoNote(
      "i-summary-meta", "these counts",
      "Out-of-catalogue rows have a ground truth the catalogue does not hold, " +
      "so no engine could have returned it. Acc@1 is shown with a 95% Wilson " +
      "interval.");
    const m = data.manifest;
    const meta2 = m && m.version
      ? `<div class="run-manifest">run: GeoLens v${escapeHtml(m.version)} · ${escapeHtml(m.ensemble_method)} fusion · k=${m.k} · catalogue ${m.catalogue_size} (sha ${escapeHtml(m.catalogue_sha)}) · ${escapeHtml(m.generated_at)}</div>`
      : "";
    let bannerLine = "";
    if (s.banner && s.banner.n_labelled > 0) {
      const b = s.banner;
      const eligible = b.n_with_timeline == null ? b.n_labelled : b.n_with_timeline;
      // A precision of 100% over one row is not a measurement, so below ten
      // rows the counts are printed on their own and nothing is coloured.
      const enoughRows = eligible >= MIN_ROWS_FOR_A_RATE;
      const rates = enoughRows
        ? `precision ${(b.precision*100).toFixed(0)}%, recall ${(b.recall*100).toFixed(0)}% `
        : "";
      const cls = enoughRows ? "banner-metrics" : "banner-metrics few-rows";
      const counted =
        `Counted over ${b.n_labelled} labelled rows, of which ${eligible} carry ` +
        `a timeline and can fire the flag at all; ${b.n_positive} should fire.`;
      const bannerNote = infoNote(
        "i-banner-metrics", "the flag's precision and recall",
        escapeHtml(enoughRows
          ? counted
          : `${counted} ${eligible} of them can fire the flag at all.`));
      bannerLine =
        `<div class="${cls}">Verification flag: ${rates}` +
        `(TP ${b.true_positive} · FP ${b.false_positive} · FN ${b.false_negative}).` +
        (enoughRows ? "" : " Too few rows for a rate.") +
        bannerNote.button + bannerNote.body + "</div>";
    }
    // The file the numbers came from is named once, above, by #batch-file-note.
    summaryMetaEl.innerHTML =
      escapeHtml(meta1) + metaNote.button + metaNote.body + meta2 + bannerLine;

    // Per-engine table, sorted by Acc@1 descending. The best and worst cells
    // say which they are, because colour alone does not carry it.
    engineTbody.innerHTML = "";
    const engines = Object.values(s.per_engine).sort((a, b) => b.acc_at_1 - a.acc_at_1);
    const bestAcc1 = engines[0]?.acc_at_1 ?? 0;
    const worstAcc1 = engines[engines.length - 1]?.acc_at_1 ?? 0;
    for (const m of engines) {
      const tr = document.createElement("tr");
      const enough = m.n_evaluated >= MIN_ROWS_FOR_A_RATE;
      const rank = !enough ? ""
        : (m.acc_at_1 === bestAcc1 ? "best" : (m.acc_at_1 === worstAcc1 ? "worst" : ""));
      const cls = rank ? `acc-${rank}` : "";
      const mark = rank ? `<span class="rank-mark">${rank}</span>` : "";
      const acc1 = rateCell(m.acc_at_1, m.n_evaluated, m.acc_at_1_ci)
        .replace("<td", `<td class="${cls}"`)
        .replace("</td>", `${mark}</td>`);
      // The cards call the engines by their display names, so the tables
      // do too; the registry id a download carries is the tooltip.
      tr.innerHTML =
        `<th scope="row" title="${escapeHtml(m.name)}">` +
        `${escapeHtml(engineMeta(m.name).label)}</th>` +
        acc1 +
        rateCell(m.acc_at_5, m.n_evaluated, m.acc_at_5_ci) +
        rankCell(m) +
        distanceCell(m) +
        rateCell(m.acc_at_161km, m.n_geo, null) +
        `<td>${m.n_abstained || 0}</td>` +
        `<td>${m.median_latency_ms.toFixed(0)} ms</td>` +
        `<td>$${m.total_cost_usd.toFixed(4)}</td>` +
        `<td>${m.n_evaluated}</td>`;
      engineTbody.appendChild(tr);
    }

    ensembleTbody.innerHTML = "";
    for (const [g, m] of Object.entries(s.ensembles)) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<th scope="row">${escapeHtml(g)}-level fusion</th>` +
        rateCell(m.acc_at_1, m.n_evaluated, m.acc_at_1_ci) +
        rateCell(m.acc_at_5, m.n_evaluated, m.acc_at_5_ci) +
        rankCell(m) +
        distanceCell(m) +
        rateCell(m.acc_at_161km, m.n_geo, null) +
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
    const postCity = postEns ? postEns.consensus_city : "n/a";
    const userCity = userEns ? userEns.consensus_city : "n/a";
    // Each level is matched against its own truth when the row carries two.
    const gtMatch = (city, truth) => truth && city.toLowerCase() === truth.toLowerCase()
      ? `<span class="gt-match">${escapeHtml(city)}</span>` : escapeHtml(city);
    const postTruth = r.ground_truth_city;
    const userTruth = r.ground_truth_user_city || r.ground_truth_city;
    const truthCell = r.ground_truth_user_city
      ? `${escapeHtml(postTruth || "n/a")} / ${escapeHtml(r.ground_truth_user_city)}`
      : escapeHtml(postTruth || "n/a");
    const tri = r.triangulation;
    const disagreement = tri && tri.disagreement_flag ? "flagged" : "";
    tr.innerHTML =
      `<th scope="row">${escapeHtml(r.id)}</th>` +
      `<td>${truthCell}</td>` +
      `<td><span class="status-${r.status}">${escapeHtml(r.status)}</span></td>` +
      `<td>${gtMatch(postCity, postTruth)}</td>` +
      `<td>${gtMatch(userCity, userTruth)}</td>` +
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
  head.innerHTML = `<th scope="col">Bucket</th><th scope="col">n</th>` +
    engineNames.map(n =>
      `<th scope="col" title="${escapeHtml(n)}">${escapeHtml(engineMeta(n).label)}</th>`).join("");

  const tbody = table.querySelector("tbody");
  tbody.innerHTML = "";
  buckets.sort((a, b) => a.bucket.localeCompare(b.bucket));
  for (const b of buckets) {
    const cells = engineNames.map(n => {
      const v = b.acc_at_1[n];
      if (v == null) return `<td>n/a</td>`;
      // The number is the information; the shading only groups it. One hue,
      // the page's accent, so the ordering does not rest on a red-to-green
      // ramp that a colour-blind reader cannot read.
      const lightness = Math.round(93 - v * 18);
      return `<td style="background:hsl(197,40%,${lightness}%)">${(v*100).toFixed(0)}</td>`;
    }).join("");
    const tr = document.createElement("tr");
    tr.innerHTML = `<th scope="row"><code>${escapeHtml(b.bucket)}</code></th>` +
      `<td>${b.n_rows}</td>${cells}`;
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

// The run manifest on its own: model versions, catalogue hash, k, fusion and
// timestamp, so a reported number is attributable.
document.getElementById("dl-manifest").addEventListener("click", () => {
  if (!_lastBatchResponse?.manifest) return;
  downloadFile("geolens-run-manifest.json", JSON.stringify(_lastBatchResponse.manifest, null, 2), "application/json");
});

// The same placeholder warning the single-query view shows, above the bulk
// results.
function renderBatchModeBanner(manifest) {
  const el = document.getElementById("batch-mode-banner");
  const warning = placeholderWarning(manifest);
  if (!warning) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  // The warning already ends by saying these are not measurements, so the
  // banner does not say it a second time.
  el.textContent = `Placeholder mode: ${warning} Set API keys for live inference.`;
}

// How many rows the GeoJSON could place, for the line beside the button. A
// row counts as placed when at least one of its levels has a coordinate.
function geojsonCoverage(data) {
  const total = data.rows.length;
  let placed = 0;
  for (const r of data.rows) {
    const levels = ["post", "user"].filter(b => r.ensembles?.[b]);
    if (levels.some(b => coordFor(r.ensembles[b].consensus_city))) placed += 1;
  }
  const note = infoNote(
    "i-coverage", "what counts as placed",
    "A row counts as placed when at least one of its levels has a " +
    "coordinate. The rest are in the file with a null geometry and a reason.");
  const headline = placed === total
    ? `All ${total} rows placed.`
    : `${placed} of ${total} rows placed.`;
  return escapeHtml(headline) + note.button + note.body;
}

// ---------- the two exports ----------
// Both files stand alone: each carries the stable `place_id`, a coordinate
// per prediction, a distance to one decimal, the catalogue hash and the mode
// each engine ran in, so neither needs a join on a free-text place name.

// The truth a level is scored against, and its identifier.
function truthFor(row, level) {
  if (level === "user" && row.ground_truth_user_city) {
    return { city: row.ground_truth_user_city,
             place_id: row.ground_truth_user_place_id || placeIdFor(row.ground_truth_user_city) };
  }
  return { city: row.ground_truth_city || null,
           place_id: row.ground_truth_place_id || placeIdFor(row.ground_truth_city) };
}

// The distance from a predicted place to the truth for its level, and
// whether it is inside the default radius. The server reports both per
// engine on an /eval row; they are recomputed here only when it did not.
function errorAgainst(city, truth) {
  const a = coordFor(city);
  const b = truth && truth.city ? coordFor(truth.city) : null;
  if (!a || !b) return { error_km: null, within_161km: null };
  const km = round1(haversineKm(a, b));
  return { error_km: km, within_161km: km <= 161 };
}

function engineErrorFor(prediction, city, truth) {
  if (prediction && prediction.error_km != null) {
    return {
      error_km: round1(prediction.error_km),
      within_161km: prediction.within_161km ?? null,
    };
  }
  if (!city) return { error_km: null, within_161km: null };
  return errorAgainst(city, truth);
}

// The engines in the response, in roster order where the roster knows them.
function engineNamesIn(data) {
  const seen = [];
  for (const r of data.rows) {
    for (const name of Object.keys(r.per_engine || {})) {
      if (!seen.includes(name)) seen.push(name);
    }
  }
  const order = rosterNames();
  seen.sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    if (ia === -1 && ib === -1) return 0;
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
  return seen;
}

function latOf(city) { const c = coordFor(city); return c ? c[0].toFixed(4) : ""; }
function lonOf(city) { const c = coordFor(city); return c ? c[1].toFixed(4) : ""; }

function batchResultsToCsv(data) {
  const engineNames = engineNamesIn(data);
  const eq = (a, b) => a && b && a.toLowerCase() === b.toLowerCase();
  // The consensus columns sit next to the fused ones because the flag is
  // computed from the consensus pair, not from the fused pair, and a reader
  // who checks the 161 km rule against the fused cities gets a contradiction.
  const cols = ["id", "status", "bucket", "catalogue_sha",
    "ground_truth_city", "ground_truth_place_id", "ground_truth_lat", "ground_truth_lon",
    "ground_truth_user_city", "ground_truth_user_place_id",
    "ground_truth_user_lat", "ground_truth_user_lon",
    "post_fused_city", "post_fused_conf", "post_fused_place_id",
    "post_fused_lat", "post_fused_lon", "post_fused_error_km", "post_fused_within_161km",
    "user_fused_city", "user_fused_conf", "user_fused_place_id",
    "user_fused_lat", "user_fused_lon", "user_fused_error_km", "user_fused_within_161km",
    "post_consensus_city", "user_consensus_city",
    "post_consensus_place_id", "user_consensus_place_id",
    "disagreement_flag", "disagreement_km", "disagreement_score"];
  for (const n of engineNames) {
    cols.push(`${n}_top1`, `${n}_mode`, `${n}_place_id`, `${n}_lat`, `${n}_lon`,
              `${n}_error_km`, `${n}_within_161km`, `${n}_correct`);
  }
  const runSha = (data.manifest || {}).catalogue_sha || "";
  const lines = [cols.join(",")];
  const bool = (v) => (v == null ? "" : (v ? "1" : "0"));
  for (const r of data.rows) {
    const pe = r.ensembles?.post;
    const ue = r.ensembles?.user;
    const tri = r.triangulation;
    const postTruth = truthFor(r, "post");
    const userTruth = truthFor(r, "user");
    const postErr = pe ? errorAgainst(pe.consensus_city, postTruth) : {};
    const userErr = ue ? errorAgainst(ue.consensus_city, userTruth) : {};
    const row = [
      csvCell(r.id),
      csvCell(r.status),
      csvCell(r.bucket || ""),
      csvCell(r.catalogue_sha || runSha),
      csvCell(postTruth.city || ""),
      csvCell(postTruth.place_id || ""),
      latOf(postTruth.city),
      lonOf(postTruth.city),
      csvCell(r.ground_truth_user_city || ""),
      csvCell(r.ground_truth_user_place_id || placeIdFor(r.ground_truth_user_city) || ""),
      latOf(r.ground_truth_user_city),
      lonOf(r.ground_truth_user_city),
      csvCell(pe?.consensus_city || ""),
      pe ? pe.consensus_confidence.toFixed(4) : "",
      csvCell(pe ? (pe.consensus_place_id || placeIdFor(pe.consensus_city) || "") : ""),
      pe ? latOf(pe.consensus_city) : "",
      pe ? lonOf(pe.consensus_city) : "",
      postErr.error_km == null ? "" : postErr.error_km.toFixed(1),
      bool(postErr.within_161km),
      csvCell(ue?.consensus_city || ""),
      ue ? ue.consensus_confidence.toFixed(4) : "",
      csvCell(ue ? (ue.consensus_place_id || placeIdFor(ue.consensus_city) || "") : ""),
      ue ? latOf(ue.consensus_city) : "",
      ue ? lonOf(ue.consensus_city) : "",
      userErr.error_km == null ? "" : userErr.error_km.toFixed(1),
      bool(userErr.within_161km),
      csvCell(tri?.post_consensus_city || ""),
      csvCell(tri?.user_consensus_city || ""),
      csvCell(tri?.post_consensus_place_id || placeIdFor(tri?.post_consensus_city) || ""),
      csvCell(tri?.user_consensus_place_id || placeIdFor(tri?.user_consensus_city) || ""),
      tri ? (tri.disagreement_flag ? "1" : "0") : "",
      tri && tri.disagreement_km != null ? round1(tri.disagreement_km).toFixed(1) : "",
      tri ? (tri.disagreement_score ?? 0).toFixed(3) : "",
    ];
    for (const n of engineNames) {
      const p = r.per_engine?.[n];
      // Each engine is checked against the truth for its own level, and an
      // engine that did not run is left blank rather than scored as a miss.
      const level = engineMeta(n).granularity;
      const truth = level === "user" ? userTruth : postTruth;
      const city = p && !p.skipped && !p.failed && !p.abstain ? p.city : null;
      const err = engineErrorFor(p, city, truth);
      // Why there is no place, written out, so an abstention reads
      // differently from an engine that was never called.
      row.push(csvCell(engineCell(p)));
      row.push(csvCell(engineModeCell(p)));
      row.push(csvCell(city ? (p.place_id || placeIdFor(city) || "") : ""));
      row.push(city ? latOf(city) : "");
      row.push(city ? lonOf(city) : "");
      row.push(err.error_km == null ? "" : err.error_km.toFixed(1));
      row.push(bool(err.within_161km));
      const scored = p && !p.skipped && !p.failed && truth.city;
      row.push(scored ? (eq(p.city, truth.city) ? "1" : "0") : "");
    }
    lines.push(row.join(","));
  }
  return lines.join("\n");
}

// What one engine's top-1 column holds: the place, or why there is none.
function engineCell(p) {
  if (!p) return "";
  if (p.skipped) return p.reason === "not selected" ? "not selected" : "not run";
  if (namedNoCataloguePlace(p)) return "reply named no catalogue place";
  if (p.failed) return "call failed";
  if (p.abstain || !p.city) return "abstained";
  return p.city;
}

// Which mode the call ran in, for the export: real, placeholder or neither.
function engineModeCell(p) {
  if (!p || p.skipped) return "";
  if (namedNoCataloguePlace(p)) return "no_catalogue_place";
  if (p.failed) return "failed";
  return p.mode || "";
}

// The quote and the separator are written as a constructed pattern rather
// than as a regular-expression literal: a literal holding a quote character
// reads as an unterminated string to anything scanning the file for prose,
// including the guard in tests/test_ui_strings.py.
const CSV_NEEDS_QUOTING = new RegExp("[\",\\n]");

function csvCell(s) {
  if (s == null) return "";
  const t = String(s);
  if (!CSV_NEEDS_QUOTING.test(t)) return t;
  return '"' + t.split('"').join('""') + '"';
}

// GeoJSON, RFC 7946, one feature per (row, level, engine or fused).
//
// Every feature carries the same property keys, with an explicit null where
// there is nothing, so geopandas types `within_161km` as boolean rather than
// as float64 with NaNs; booleans are booleans; `Feature.id` is
// `<row>:<level>:<engine>` and is unique; the `crs` member RFC 7946 removed
// is gone; and a feature with no coordinate says why in
// `no_coordinate_reason` rather than being dropped.
const GEOJSON_PROPERTY_KEYS = [
  "row_id", "level", "engine", "engine_label", "is_fused", "engine_mode",
  "status", "bucket", "catalogue_sha",
  "city", "place_id", "lat", "lon", "confidence",
  "names_a_catalogue_place",
  "ground_truth_city", "ground_truth_place_id", "error_km", "within_161km",
  "post_consensus_city", "post_consensus_place_id",
  "user_consensus_city", "user_consensus_place_id",
  "disagreement_flag", "disagreement_km",
  "no_coordinate_reason",
];

function blankProperties() {
  const out = {};
  for (const key of GEOJSON_PROPERTY_KEYS) out[key] = null;
  return out;
}

// Why an engine produced no place, in the words the per-row CSV uses.
function noPlaceReason(p) {
  if (!p) return "this engine is not in the response for this row";
  if (p.skipped) {
    return p.reason === "not selected"
      ? "this engine was not selected for the run"
      : `this engine was not run: ${p.reason || "nothing to read"}`;
  }
  if (namedNoCataloguePlace(p)) return "the reply named no catalogue place";
  if (p.failed) return `the call failed: ${p.error_class || "unknown error"}`;
  if (p.abstain || !p.city) return "the engine named no place in this text";
  return null;
}

function batchResultsToGeoJSON(data) {
  const features = [];
  const engineNames = engineNamesIn(data);
  const emit = (id, geometry, properties) => features.push({
    type: "Feature", id, geometry, properties,
  });

  const runSha = (data.manifest || {}).catalogue_sha || null;
  for (const r of data.rows) {
    const shared = {
      row_id: r.id,
      status: r.status,
      bucket: r.bucket ?? null,
      catalogue_sha: r.catalogue_sha || runSha,
      post_consensus_city: r.triangulation?.post_consensus_city || null,
      post_consensus_place_id:
        r.triangulation?.post_consensus_place_id || placeIdFor(r.triangulation?.post_consensus_city),
      user_consensus_city: r.triangulation?.user_consensus_city || null,
      user_consensus_place_id:
        r.triangulation?.user_consensus_place_id || placeIdFor(r.triangulation?.user_consensus_city),
      disagreement_flag: r.triangulation?.disagreement_flag ?? null,
      disagreement_km: round1(r.triangulation?.disagreement_km),
    };

    const levels = ["post", "user"].filter(level =>
      r.ensembles?.[level] || engineNames.some(n => engineMeta(n).granularity === level &&
        r.per_engine?.[n]));
    if (!levels.length) {
      const properties = Object.assign(blankProperties(), shared, {
        is_fused: false,
        no_coordinate_reason: r.status === "ooc"
          ? "the row's ground truth is outside the catalogue, so no prediction was made"
          : (r.error || "no prediction for this row"),
      });
      emit(`${r.id}:row:none`, null, properties);
      continue;
    }

    for (const level of levels) {
      const truth = truthFor(r, level);
      const gz = r.per_engine?.[`gazetteer_${level}`];
      const named = (!gz || gz.skipped || gz.failed) ? null : !gz.abstain;
      const levelShared = Object.assign({}, shared, {
        level,
        names_a_catalogue_place: named,
        ground_truth_city: truth.city,
        ground_truth_place_id: truth.place_id || null,
      });

      const rows = [];
      const e = r.ensembles?.[level];
      if (e) {
        rows.push({
          engine: "fused",
          engine_label: `${level}-level fused prediction`,
          is_fused: true,
          mode: "fused",
          city: e.consensus_city,
          place_id: e.consensus_place_id || placeIdFor(e.consensus_city),
          confidence: e.consensus_confidence ?? null,
          reason: null,
        });
      }
      for (const name of engineNames) {
        if (engineMeta(name).granularity !== level) continue;
        const p = r.per_engine?.[name];
        const reason = noPlaceReason(p);
        rows.push({
          engine: name,
          engine_label: engineMeta(name).label,
          is_fused: false,
          mode: engineModeCell(p) || null,
          city: reason ? null : p.city,
          place_id: reason ? null : (p.place_id || placeIdFor(p.city)),
          confidence: reason ? null : (p.confidence ?? null),
          reason,
        });
      }

      for (const entry of rows) {
        const coords = entry.city ? coordFor(entry.city) : null;
        const err = entry.is_fused
          ? errorAgainst(entry.city, truth)
          : engineErrorFor(r.per_engine?.[entry.engine], entry.city, truth);
        const properties = Object.assign(blankProperties(), levelShared, {
          engine: entry.engine,
          engine_label: entry.engine_label,
          is_fused: entry.is_fused,
          engine_mode: entry.mode ?? null,
          city: entry.city,
          place_id: entry.place_id || null,
          confidence: entry.confidence,
          lat: coords ? Number(coords[0].toFixed(4)) : null,
          lon: coords ? Number(coords[1].toFixed(4)) : null,
          error_km: err.error_km,
          within_161km: err.within_161km,
          no_coordinate_reason: coords ? null : (entry.reason ||
            `${entry.city} has no coordinate in the catalogue; onboard it with a ` +
            "latitude and longitude to place it"),
        });
        const geometry = coords
          ? { type: "Point", coordinates: [Number(coords[1].toFixed(4)),
                                           Number(coords[0].toFixed(4))] }
          : null;
        emit(`${r.id}:${level}:${entry.engine}`, geometry, properties);
      }
    }
  }
  return JSON.stringify({ type: "FeatureCollection", features }, null, 2);
}

// Which engines did not return live model output, read off the manifest.
// Returns "" for a fully real run.
function placeholderWarning(manifest) {
  const modes = (manifest && manifest.engine_modes) || {};
  const names = Object.keys(modes);
  const counts = (manifest && manifest.engine_call_counts) || {};
  const unreal = n => {
    const c = counts[n];
    if (c) return (c.stub || 0) + (c.failed || 0);
    return (modes[n] === "stub" || modes[n] === "failed") ? 1 : 0;
  };
  const notReal = names.filter(n => unreal(n) > 0);
  if (!names.length || !notReal.length) return "";
  const detail = notReal.map(n => {
    const c = counts[n];
    if (!c) return `${n}: ${modes[n]}`;
    const parts = ["stub", "failed"].filter(m => c[m]).map(m => `${c[m]} ${m}`);
    return `${n}: ${parts.join(", ")}`;
  }).join("; ");
  return `${notReal.length} of ${names.length} engines did not return live model ` +
    `output (${detail}). These numbers are not measurements of those engines.`;
}

function batchSummaryToLatex(s, manifest) {
  const lines = [];
  const warning = placeholderWarning(manifest);
  if (manifest && manifest.version) {
    lines.push(`% GeoLens v${manifest.version} · ${manifest.ensemble_method} fusion · k=${manifest.k} · catalogue ${manifest.catalogue_size} (sha ${manifest.catalogue_sha}) · ${manifest.generated_at}`);
  }
  if (warning) {
    // A comment is invisible once the table is typeset, so the warning is
    // also a row of the table.
    lines.push(`% PLACEHOLDER RUN. ${warning}`);
  }
  lines.push("\\begin{tabular}{lrrrrrrr}");
  lines.push("\\toprule");
  lines.push("Engine & Acc@1 (95\\% CI) & Acc@5 & Mean rank (found) & Median km err. & Acc@161km & Abstained & N \\\\");
  lines.push("\\midrule");
  const ci = m => `[${(m.acc_at_1_ci[0]*100).toFixed(0)}, ${(m.acc_at_1_ci[1]*100).toFixed(0)}]`;
  // The mean rank is over the rows where the truth was in the list, and the
  // median error over the rows where the engine named a place.
  const rank = m => m.n_rank_found
    ? `${m.mean_rank_found.toFixed(2)} {\\scriptsize (${m.n_rank_found}/${m.n_evaluated})}`
    : `n/a {\\scriptsize (0/${m.n_evaluated})}`;
  const km = m => (m.n_geo ? m.median_error_km.toFixed(0) : "n/a");
  const acc161 = m => (m.n_geo ? (m.acc_at_161km*100).toFixed(1) : "n/a");
  const engines = Object.values(s.per_engine).sort((a, b) => b.acc_at_1 - a.acc_at_1);
  for (const m of engines) {
    lines.push(`${escapeLatex(m.name)} & ${(m.acc_at_1*100).toFixed(1)} {\\scriptsize ${ci(m)}} & ${(m.acc_at_5*100).toFixed(1)} & ${rank(m)} & ${km(m)} & ${acc161(m)} & ${m.n_abstained || 0} & ${m.n_evaluated} \\\\`);
  }
  lines.push("\\midrule");
  for (const [g, m] of Object.entries(s.ensembles)) {
    lines.push(`Fusion (${g}) & \\textbf{${(m.acc_at_1*100).toFixed(1)}} {\\scriptsize ${ci(m)}} & ${(m.acc_at_5*100).toFixed(1)} & ${rank(m)} & ${km(m)} & ${acc161(m)} & 0 & ${m.n_evaluated} \\\\`);
  }
  if (warning) {
    lines.push("\\midrule");
    lines.push(`\\multicolumn{8}{l}{\\footnotesize \\textbf{Placeholder run.} ${escapeLatex(warning)}} \\\\`);
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
// A recorded run reads one committed file and calls no endpoint, so the
// live boot is skipped for it and replay.js takes over.
loadScenarios();
if (!RECORDED_RUN) {
  ensureInstance();
  loadCatalogue();
}
