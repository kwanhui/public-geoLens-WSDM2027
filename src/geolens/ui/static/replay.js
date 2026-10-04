// ---------- recorded runs ----------
// `?replay=<scenario id>` renders the newest committed scenario check for
// that scenario through the ordinary result view, behind a banner saying
// when and where it was recorded. Nothing here calls an endpoint, so a venue
// with no network still has the demo. The files come from
// scripts/bundle_scenario_records.py.

// The controls a recorded run cannot drive, because each of them is a call.
// The two notes are the bodies of `reset-scenario-note` and
// `export-result-note`, which sit beside those wrappers rather than inside
// them, so they are named here as well. The onboarding form is one of them,
// so its tab goes too and no step opens it.
// They are hidden with a class of their own, because the tabs own `.hidden`
// on the panels this list names.
const REPLAY_HIDES = [
  "catalogue-panel", "onboard-panel", "geolocate-btn", "replay-btn",
  "reset-row", "reset-scenario-note", "export-result-note", "i-reset", "i-export",
  "tab-onboard", "tab-batch",
];

// A recorded run answers only the tasks its steps carry, and the other query
// tabs would open an empty form with no button to press, so the row shows
// the tabs the run walks and nothing else.
function replayHiddenTabs(tasks) {
  const shown = new Set([...tasks].map(t => `tab-${t}`));
  return [...document.querySelectorAll(".mode-tab")]
    .map(t => t.id)
    .filter(id => !shown.has(id));
}

// The tabs a recorded run walks, in the order its steps open them.
function replayTasks(record) {
  const fallback = scenarioTask(record.scenario);
  const tasks = [];
  for (const step of record.steps) {
    const task = QUERY_MODES.includes(step.task) ? step.task : fallback;
    if (!tasks.includes(task)) tasks.push(task);
  }
  return tasks.length ? tasks : [fallback];
}

// When the run was recorded and at which commit. Where it was recorded, and
// what that means for the network, is the note behind the icon.
function recordedBannerMarkup(record) {
  const date = String(record.recorded_at || "").slice(0, 10);
  const commit = String(record.commit || "").slice(0, 7);
  const note = infoNote(
    "i-replay", "this recorded run",
    `Recorded from ${escapeHtml(String(record.base_url || ""))}. ` +
    "Nothing on this page calls an endpoint.");
  return `Recorded run, ${escapeHtml(date)}, commit ${escapeHtml(commit)}. ` +
    "Not a live result." + note.button + note.body;
}

// The names are the live preset's own step names, carried in the record.
// An onboarding step has no result, so the answer above it stays on screen,
// as it does in a live run. A step opens the tab it was recorded on, and a
// step that names none holds the scenario's own tab, because the onboarding
// form is not on a recorded page to switch to.
function recordedSteps(record) {
  const fallback = scenarioTask(record.scenario);
  return record.steps.map((step, index) => {
    const task = QUERY_MODES.includes(step.task) ? step.task : fallback;
    return {
      name: step.name || `step ${index + 1}`,
      hold: 4000,
      run: async () => {
        applyMode(task, { announce: true });
        setStage(step.stage, step.stage_note);
        if (!step.result) return true;
        fillInput(step.post, step.user_posts, record.scenario.lang);
        rememberCoords(step.place_coordinates);
        renderResults(Object.assign({}, step.result, {
          place_coordinates: step.place_coordinates,
          submitted: { post: step.post, user_posts: step.user_posts },
        }), task);
        revealResult();
        return true;
      },
    };
  });
}

async function loadRecordedRun(id) {
  const banner = document.getElementById("replay-banner");
  if (!banner) return;
  let record;
  try {
    const resp = await fetch(`/static/scenarios/records/${encodeURIComponent(id)}.json`);
    if (!resp.ok) throw new Error(String(resp.status));
    record = await resp.json();
  } catch (e) {
    banner.classList.remove("hidden");
    banner.textContent = `This page carries no recorded run for ${id}.`;
    return;
  }

  INSTANCE = record.instance;
  _instancePromise = Promise.resolve();
  setRoster(INSTANCE.engines, INSTANCE.local_engines);
  applyInstanceSettings();

  banner.classList.remove("hidden");
  banner.innerHTML = recordedBannerMarkup(record);
  const tasks = replayTasks(record);
  for (const id_ of [...REPLAY_HIDES, ...replayHiddenTabs(tasks)]) {
    const el = document.getElementById(id_);
    if (el) el.classList.add("replay-off");
  }

  currentScenario = record.scenario;
  applyMode(tasks[0]);
  setActiveTile(record.scenario.id);
  showScenarioBanner(record.scenario);
  const comparison = document.getElementById("comparison-toggle");
  if (comparison) comparison.checked = false;

  await runPresetSteps(record.scenario, recordedSteps(record));
}

if (RECORDED_RUN) loadRecordedRun(RECORDED_RUN);
