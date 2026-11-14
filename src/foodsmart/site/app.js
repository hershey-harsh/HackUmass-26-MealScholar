// MealScholar web client: a plain ES module over the JSON API in web.py; no build step.
// Product rules (README, AGENTS.md): every number is a range until the user weighs the food,
// micronutrients are labelled estimates, and there are no calorie targets.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const els = {
  topbar: $(".topbar"),
  skip: $(".skip"),
  main: $("#main"),
  capture: $("#capture"),
  captureTitle: $("#capture-title"),
  viewfinder: $("#viewfinder"),
  library: $("#photo"),
  camera: $("#camera"),
  choose: $("#choose"),
  setup: $("#setup"),
  preview: $("#preview"),
  previewImg: $("#preview img"),
  status: $("#capture-status"),
  location: $("#use-location"),
  locationStatus: $("#location-status"),
  result: $("#result"),
  todayView: $("#today"),
  today: $("#today-body"),
  todayTitle: $("#today-title"),
  todayDate: $("#today-date"),
  tabs: $$(".tab[data-view]"),
  badge: $("#analyze-badge"),
  sheet: $("#sheet"),
  sheetKicker: $("#sheet-kicker"),
  sheetTitle: $("#sheet-title"),
  sheetBody: $("#sheet-body"),
  toast: $("#toast"),
};

const media = (query) => window.matchMedia(query);
const wide = media("(min-width: 1024px)"); // Analyze and Today side by side; no tab bar
const bottomSheet = media("(max-width: 639.98px)"); // the edit sheet slides up from the bottom
const finePointer = media("(pointer: fine)");
const reducedMotion = media("(prefers-reduced-motion: reduce)");

const state = {
  ready: false,
  analyzing: false,
  nutrients: [],
  // Each tab keeps its own meal: a new analysis, with its photo, under Analyze and a logged meal
  // under Today. Wide screens show one of them in the main column, a logged meal in front.
  slots: { analyze: null, today: null }, // { result, photoUrl }
  shown: null, // the slot rendered into #result
  open: null, // { index, kind, id } while the edit sheet is open
  dismissedQuestion: null,
  view: "analyze",
  scroll: { analyze: 0, today: 0, list: 0 },
  openedMeal: null,
};

const TIME_SOURCE = { exif: "time from the photo", device: "time from your device", upload: "time of upload" };

const icon = (name) => `<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;

// A pause for screen readers where only CSS spacing separates two pieces of text.
const SEP = '<span class="visually-hidden">, </span>';

// ------------------------------------------------------------------- api

async function api(path, { method = "GET", body, headers } = {}) {
  let response;
  try {
    response = await fetch(path, { method, body, headers });
  } catch {
    throw new Error("Can't reach MealScholar right now. Check your connection and try again.");
  }
  const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(errorDetail(data) ?? `Request failed (${response.status}).`);
  return data;
}

function errorDetail(data) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((d) => d.msg).join("; ");
  return null;
}

const jsonBody = (value) => ({ body: JSON.stringify(value), headers: { "Content-Type": "application/json" } });

// ------------------------------------------------------------ formatting

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const amount = (value, unit) => (unit === "kcal" || value >= 100 ? nf0 : nf1).format(value);
const grams = (value) => nf0.format(value);

const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const TO = '<span aria-hidden="true">–</span><span class="visually-hidden"> to </span>';

/** Likely value, then the low–high range; a single number only once low and high agree. */
function rangeHtml(range, unit, format = (v) => amount(v, unit), { stack = false } = {}) {
  const [low, likely, high] = [format(range.low), format(range.likely), format(range.high)];
  const unitHtml = unit ? ` ${esc(unit)}` : "";
  const cls = stack ? "value value--stack" : "value";
  if (low === high) return `<span class="${cls}"><strong>${likely}${unitHtml}</strong></span>`;
  return `<span class="${cls}"><strong>${likely}${unitHtml}</strong><span class="value__range"><span class="visually-hidden">, range </span>${low}${TO}${high}</span></span>`;
}

function dvHtml(range) {
  const [low, high] = [nf0.format(range.low), nf0.format(range.high)];
  return low === high ? `${low}% DV` : `${low}${TO}${high}% DV`;
}

/** Wall-clock parts of an ISO time as written, ignoring any offset: the meal's local time. */
function wallClock(iso) {
  const [, y, m, d, hh, mm] = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return new Date(Number(y), Number(m) - 1, Number(d), Number(hh), Number(mm));
}

const dayFormat = new Intl.DateTimeFormat("en-US", { weekday: "short", day: "numeric", month: "short" });
const timeFormat = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

function localDate(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const isExact = (range) => Math.abs(range.high - range.low) < 1e-9;
const labelOf = (key) => state.nutrients.find((n) => n.key === key)?.label ?? key;
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/** "Fast food, american, burger": the OpenStreetMap place type and its cuisines, readable. */
function placeKind(place) {
  const words = (s) => s.replace(/_/g, " ").trim();
  const cuisines = (place.cuisine ?? "").split(";").map(words).filter(Boolean);
  return capitalize([words(place.kind), ...cuisines].filter(Boolean).join(", "));
}

// ---------------------------------------------------------------- bands
// A band is a track with the low–high range and an ink tick at the likely value. Positions are
// transforms only; on re-render each band moves from where it was, so weighing a food visibly
// collapses its range onto one line.

/** `scale` is the value at the track's right end; bands that share a scale are comparable. */
function bandHtml(key, range, { index = 0, scale = range.high * 1.12 || 1, dark = false } = {}) {
  const cls = `band${isExact(range) ? " is-exact" : ""}${dark ? " band--dark" : ""}`;
  return `<span class="${cls}" style="--i:${index}" data-band="${key}" data-scale="${scale}" data-low="${range.low}" data-likely="${range.likely}" data-high="${range.high}" aria-hidden="true"><span class="band__range"></span><span class="band__tick"></span></span>`;
}

const readBand = (el) => ({ low: Number(el.dataset.low), likely: Number(el.dataset.likely), high: Number(el.dataset.high) });

function placeBand(el, range, scale) {
  const pct = (v) => (scale > 0 ? Math.min(100, (v / scale) * 100) : 0);
  const [low, likely, high] = [pct(range.low), pct(range.likely), pct(range.high)];
  el.querySelector(".band__range").style.transform = `translateX(${low}%) scaleX(${(high - low) / 100})`;
  el.querySelector(".band__tick").style.transform = `translateX(${likely}%)`;
}

function snapshotBands(root) {
  return new Map($$(".band[data-band]", root).map((el) => [el.dataset.band, readBand(el)]));
}

function animateBands(root, previous) {
  const bands = $$(".band[data-band]", root);
  const scaleOf = (el) => Number(el.dataset.scale);
  for (const el of bands) {
    const target = readBand(el);
    const from = previous.get(el.dataset.band) ?? { low: target.likely, likely: target.likely, high: target.likely };
    el.classList.add("no-transition");
    placeBand(el, from, scaleOf(el));
  }
  root.getBoundingClientRect(); // commit the starting positions before transitioning
  requestAnimationFrame(() => {
    for (const el of bands) {
      el.classList.remove("no-transition");
      placeBand(el, readBand(el), scaleOf(el));
    }
  });
}

/** Bands straight at their values, with no motion: for a meal you come back to. */
function placeBands(root) {
  const bands = $$(".band[data-band]", root);
  for (const el of bands) {
    el.classList.add("no-transition");
    placeBand(el, readBand(el), Number(el.dataset.scale));
  }
  root.getBoundingClientRect();
  for (const el of bands) el.classList.remove("no-transition");
}

// ---------------------------------------------------------------- views
// Phones show one view at a time behind the tab bar; wide screens show both (see styles.css).

/** The slot #result shows: on phones the active tab's meal; on wide screens a logged meal, else a new one. */
function shownKey() {
  if (wide.matches) return state.slots.today ? "today" : state.slots.analyze ? "analyze" : null;
  return state.slots[state.view] ? state.view : null;
}

const shownMeal = () => state.shown?.result ?? null;

function replayEntrance(el) {
  el.classList.remove("is-entering");
  void el.offsetWidth;
  el.classList.add("is-entering");
}

/** Put the meal that belongs on screen into #result, or hide it. `enter` names a slot that just arrived. */
function syncResult({ enter = null } = {}) {
  const key = shownKey();
  const slot = key ? state.slots[key] : null;
  const entering = Boolean(slot) && key === enter;
  if (key) document.body.dataset.result = key;
  else delete document.body.dataset.result;
  els.result.hidden = !slot;
  if (slot !== state.shown || entering) {
    if (els.sheet.open) closeSheet({ restoreFocus: false });
    state.shown = slot;
    state.open = null;
    if (slot) renderResult(entering ? "enter" : "switch");
    else els.result.innerHTML = "";
  }
  markOpenMeal();
  if (!entering) return;
  replayEntrance(els.result);
  window.scrollTo(0, 0);
  $("#result-title", els.result)?.focus({ preventScroll: true });
}

function setView(view) {
  if (view === state.view) return;
  if (!wide.matches) state.scroll[state.view] = window.scrollY;
  state.view = view;
  document.body.dataset.view = view;
  for (const tab of els.tabs) {
    if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  if (view === "analyze") showBadge(false);
  syncResult();
  if (wide.matches) return;
  $$(".view").forEach(replayEntrance);
  window.scrollTo(0, state.scroll[view] ?? 0);
}

function scrollTop() {
  window.scrollTo({ top: 0, behavior: reducedMotion.matches ? "auto" : "smooth" });
}

for (const tab of els.tabs) {
  tab.addEventListener("click", (event) => {
    event.preventDefault();
    const view = tab.dataset.view;
    if (view !== state.view) setView(view);
    // Tapping the active tab again goes back to the start of that view, as in native apps.
    else if (state.slots[view]) closeSlot(view, { focus: view === "today" });
    else scrollTop();
    syncHistory();
  });
}

const onScroll = () => els.topbar.classList.toggle("is-scrolled", window.scrollY > 4);
window.addEventListener("scroll", onScroll, { passive: true });

// On phones Today is a screen of its own, so its title is the page's level-1 heading there;
// on wide screens it is a side panel next to the Analyze h1.
function syncHeadingLevel() {
  if (wide.matches) els.todayTitle.removeAttribute("aria-level");
  else els.todayTitle.setAttribute("aria-level", "1");
}

wide.addEventListener("change", () => {
  syncHeadingLevel();
  syncResult();
});

// --------------------------------------------------------------- capture

function setViewfinder(mode) {
  els.viewfinder.dataset.state = mode;
  const analyzing = mode === "analyzing";
  $(".viewfinder__empty", els.viewfinder).hidden = analyzing;
  els.preview.hidden = !analyzing;
  els.viewfinder.setAttribute("aria-busy", String(analyzing));
}

function setStatus(message, { error = false, target = els.status } = {}) {
  target.textContent = message;
  target.classList.toggle("is-error", error);
  target.setAttribute("role", error ? "alert" : "status");
  // Keep an error in view on a phone, clear of the tab bar (see scroll-margin in styles.css).
  if (error && message) target.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
}

function setPickers(enabled) {
  for (const button of $$("[data-pick], [data-action='start']")) button.disabled = !enabled;
}

function renderSetup(status) {
  const steps = [];
  if (!status.gemini_ready) {
    steps.push(
      "<p>Add your Gemini key to <code>.env</code> as <code>GEMINI_API_KEY=…</code>, then restart <code>uv run foodsmart serve</code>.</p>"
    );
  }
  if (!status.food_db_ready) steps.push("<p>Build the USDA database once: <code>uv run foodsmart fdc-import</code></p>");
  els.setup.innerHTML = steps.length ? `<p class="field-label">Setup needed</p>${steps.join("")}` : "";
  els.setup.hidden = !steps.length;
  state.ready = !steps.length;
  setPickers(state.ready);
  els.choose.setAttribute("aria-describedby", steps.length ? "photo-hint setup" : "photo-hint");
}

function devicePosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("This browser can't share location."));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve([p.coords.latitude, p.coords.longitude]),
      (err) => reject(new Error(err.code === err.PERMISSION_DENIED ? "Location permission was denied." : "Couldn't get your location.")),
      { timeout: 8000, maximumAge: 60_000 }
    );
  });
}

/** A dot on the Analyze tab: a result finished while you were on Today. */
function showBadge(on) {
  els.badge.hidden = !on;
}

/** Focus the start of the capture screen: the photo button, or the heading while it can't be used. */
function focusCapture() {
  const usable = !state.analyzing && !els.choose.disabled;
  (usable ? els.choose : els.captureTitle).focus({ preventScroll: true });
}

async function analyze(file) {
  if (!state.ready) return;
  if (state.analyzing) return toast("Still analyzing the last photo.");
  if (file.type && !file.type.startsWith("image/")) {
    setStatus("That file isn't an image. Choose a JPEG, PNG or HEIC photo.", { error: true });
    return;
  }
  // A new photo replaces the last analysis; on wide screens it also takes over the main column.
  const leaving = Boolean(state.shown);
  discardSlot("analyze");
  if (wide.matches) discardSlot("today");
  setView("analyze");
  syncResult();
  if (leaving) {
    window.scrollTo(0, 0);
    replayEntrance(els.capture);
  }
  syncHistory();

  // The photo's URL belongs to this request, so opening another meal meanwhile can't take it away.
  const photoUrl = URL.createObjectURL(file);
  els.previewImg.src = photoUrl;
  state.analyzing = true;
  setViewfinder("analyzing");
  setStatus("Analyzing your photo. This usually takes a few seconds.");

  let query = "";
  if (els.location.checked) {
    try {
      const [lat, lon] = await devicePosition();
      query = `?lat=${lat}&lon=${lon}`;
    } catch (err) {
      setStatus(`${err.message} Continuing without location.`);
    }
  }

  try {
    const result = await api(`/api/meals${query}`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    });
    setStatus("");
    state.slots.analyze = { result, photoUrl };
    state.scroll.analyze = 0;
    if (wide.matches) discardSlot("today");
    if (wide.matches || state.view === "analyze") {
      syncResult({ enter: "analyze" });
    } else {
      // The user moved to Today while waiting: leave them there and point at the Analyze tab.
      syncResult();
      showBadge(true);
      toast("Your meal is ready. Open Analyze to see it.");
    }
    syncHistory();
    loadToday();
  } catch (err) {
    URL.revokeObjectURL(photoUrl);
    // On wide screens the outcome takes the main column, as a result would.
    if (wide.matches && state.slots.today) {
      closeSlot("today");
      syncHistory();
    }
    setStatus(err.message, { error: true });
    if (!wide.matches && state.view !== "analyze") toast("That photo couldn't be analyzed. Open Analyze to see why.");
  } finally {
    state.analyzing = false;
    setViewfinder("empty");
    els.previewImg.removeAttribute("src");
    els.library.value = "";
    els.camera.value = "";
  }
}

function discardSlot(key) {
  const slot = state.slots[key];
  if (!slot) return;
  if (slot.photoUrl) URL.revokeObjectURL(slot.photoUrl);
  state.slots[key] = null;
  if (key === "analyze") showBadge(false);
}

/** Close a tab's meal: Analyze goes back to the capture screen, Today to its list. */
function closeSlot(key, { focus = false } = {}) {
  const slot = state.slots[key];
  if (!slot) return;
  const wasShown = slot === state.shown;
  discardSlot(key);
  if (!state.analyzing) setStatus("");
  syncResult();
  if (!wasShown) return;
  if (key === "today" && !wide.matches) {
    window.scrollTo(0, state.scroll.list);
    replayEntrance(els.todayView);
    if (focus) $(`[data-meal="${state.openedMeal}"]`, els.today)?.focus({ preventScroll: true });
    return;
  }
  window.scrollTo(0, 0);
  replayEntrance(state.shown ? els.result : els.capture);
  if (!focus) return;
  if (state.shown) $("#result-title", els.result)?.focus({ preventScroll: true });
  else focusCapture();
}

/** Back to a fresh capture screen, optionally opening the photo picker right away. */
function startNewMeal({ pick = false } = {}) {
  if (state.shown && state.shown === state.slots.today) discardSlot("today"); // leaving a logged meal closes it
  discardSlot("analyze");
  if (!state.analyzing) setStatus("");
  setView("analyze");
  syncResult();
  window.scrollTo(0, 0);
  replayEntrance(els.capture);
  syncHistory();
  if (pick && state.analyzing) toast("Still analyzing the last photo.");
  if (pick && state.ready && !state.analyzing) els.library.click();
  else focusCapture();
}

// ---------------------------------------------------------------- result

/** Highlight the open meal in Today's list (the one beside it on wide screens). */
function markOpenMeal() {
  const id = (state.slots.today ?? state.slots.analyze)?.result.id;
  for (const button of $$("[data-meal]", els.today)) {
    if (id != null && button.dataset.meal === String(id)) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
}

/** `enter` plays the entrance; `switch` shows a meal you come back to, still; `update` animates changes. */
function renderResult(mode = "update", { focus } = {}) {
  const previous = mode === "update" ? snapshotBands(els.result) : new Map();
  els.result.innerHTML = resultHtml(state.shown);
  els.result.classList.toggle("is-settled", mode !== "enter"); // entrance animations play once per meal
  if (mode === "switch") placeBands(els.result);
  else animateBands(els.result, previous);
  if (focus) $(focus, els.result)?.focus();
}

function resultHtml(slot) {
  const { result, photoUrl } = slot;
  const ctx = result.context;
  const when = wallClock(ctx.taken_at);
  const place = ctx.place ? ` <span class="result__place">at ${esc(ctx.place.name)}</span>` : "";
  // Date and time source stay whole; a long list of OpenStreetMap cuisines may wrap.
  const meta = [`${dayFormat.format(when)}, ${timeFormat.format(when)}`, TIME_SOURCE[ctx.time_source]]
    .filter(Boolean)
    .map((part, i) => `<span class="nowrap">${i ? "· " : ""}${esc(part)}</span>`);
  if (ctx.place) meta.push(`<span>·&nbsp;${esc(placeKind(ctx.place))}</span>`);
  const fromToday = slot === state.slots.today;
  const back = fromToday ? '<span class="if-narrow">Today</span><span class="if-wide">Back</span>' : "New photo";

  return `
    <div class="result__nav">
      <button class="back" type="button" data-action="back">${icon("chevron-left")}${back}</button>
    </div>
    <article class="card meal-card${photoUrl ? " meal-card--photo" : ""}">
      ${photoUrl ? photoHtml(result, photoUrl) : ""}
      <div class="meal-card__body">
        ${result.id != null ? `<p class="eyebrow">Meal #${result.id}</p>` : ""}
        <h1 id="result-title" class="result__title" tabindex="-1">${esc(capitalize(ctx.meal_slot))}${place}</h1>
        <p class="result__meta">${meta.join(" ")}</p>
        ${totalHtml(result, photoUrl)}
      </div>
    </article>
    ${questionHtml(result)}
    ${itemsHtml(result)}
    ${result.items.length ? nutrientsHtml(result) : ""}
    ${notesHtml(result)}
    <div class="result__foot">
      <button class="cta cta--block" type="button" data-action="new">
        <span class="cta__pill">Analyze another meal</span>
        <span class="cta__dot" aria-hidden="true">${icon("arrow")}</span>
      </button>
      <button class="btn btn--secondary btn--block if-narrow" type="button" data-action="see-today">${icon("today")}${fromToday ? "Back to today" : "See today"}</button>
    </div>`;
}

/** Big low–high calories; one number once every counted food is weighed. */
function kcalRangeHtml(kcal) {
  const [low, high] = [amount(kcal.low, "kcal"), amount(kcal.high, "kcal")];
  const numbers =
    low === high
      ? `<span>${low}</span>`
      : `<span>${low}</span><span class="total__dash" aria-hidden="true">–</span><span class="visually-hidden"> to </span><span>${high}</span>`;
  return `${numbers}<span class="total__unit">kcal</span>`;
}

function totalHtml(result, photoUrl) {
  const kcal = result.totals.energy_kcal;
  if (!result.items.length) {
    return `<p class="total__empty">No food or drink found in this photo. Try another shot with the whole plate in view.</p>`;
  }
  const noPhoto = photoUrl ? "" : '<p class="answered">Photos are never stored, so saved meals appear without one.</p>';
  const answered = result.answer
    ? `<p class="answered">${icon("check")}<span>You said: “${esc(result.answer)}”. The foods and ranges below include it.</span></p>`
    : "";
  if (!kcal) {
    return `
      <div class="total">
        <p class="eyebrow">Calories, this meal</p>
        <p class="total__likely">Nothing is counted yet. Pick a USDA food for each item below.</p>
      </div>${answered}${noPhoto}`;
  }
  const [low, likely, high] = [amount(kcal.low, "kcal"), amount(kcal.likely, "kcal"), amount(kcal.high, "kcal")];
  const exact = low === high;
  const weighable = result.items.some((r) => r.match && !isExact(itemGrams(r.item)));
  return `
    <div class="total">
      <p class="eyebrow">Calories, this meal</p>
      <p class="total__value">${kcalRangeHtml(kcal)}</p>
      ${bandHtml("total-kcal", kcal, { dark: true })}
      <p class="total__likely">${
        exact
          ? "Every counted food is weighed, so this is a single number."
          : `Most likely <strong>${likely}&nbsp;kcal</strong>.${weighable ? " Weigh any food to narrow the range." : ""}`
      }</p>
    </div>${answered}${noPhoto}`;
}

function photoHtml(result, photoUrl) {
  const boxes = result.items
    .map((r, i) => {
      const b = r.item.box_2d;
      if (!b) return "";
      const [ymin, xmin, ymax, xmax] = b.map((v) => Math.max(0, Math.min(1000, v)) / 10);
      return `<span class="box" data-index="${i}" style="--i:${i};top:${ymin}%;left:${xmin}%;width:${xmax - xmin}%;height:${ymax - ymin}%"><span class="box__n">${i + 1}</span></span>`;
    })
    .join("");
  const caption = result.scale_reference ? `<figcaption class="shot__caption">Size judged against: ${esc(result.scale_reference)}</figcaption>` : "";
  return `
    <figure class="shot">
      <div class="shot__frame">
        <div class="shot__img">
          <img src="${photoUrl}" alt="Your meal photo, with each detected food outlined and numbered">
          <div class="boxes" aria-hidden="true">${boxes}</div>
        </div>
      </div>
      ${caption}
    </figure>`;
}

function questionHtml(result) {
  if (!result.question || result.id == null || state.dismissedQuestion === result.id) return "";
  return `
    <form class="question" data-form="answer" aria-labelledby="question-text">
      <p class="eyebrow">One answer narrows the range</p>
      <p id="question-text" class="question__text">${esc(result.question)}</p>
      <div class="question__row">
        <label class="visually-hidden" for="answer">Your answer</label>
        <input id="answer" class="field" name="answer" type="text" required maxlength="500" autocomplete="off" enterkeyhint="send" placeholder="e.g. fried in oil">
        <button class="btn btn--primary" type="submit">Answer</button>
        <button class="btn btn--ghost" type="button" data-action="dismiss-question">Not now</button>
      </div>
      <p class="question__error" role="alert"></p>
    </form>`;
}

const itemGrams = (item) => ({ low: item.grams_low, likely: item.grams_likely, high: item.grams_high });

function itemsHtml(result) {
  if (!result.items.length) return "";
  // One scale for every food, so the bars also compare portion sizes across the plate.
  const gramsScale = Math.max(...result.items.map((r) => r.item.grams_high)) * 1.05 || 1;
  return `
    <section class="block" aria-labelledby="foods-title">
      <div class="section-head">
        <h2 id="foods-title" class="section-title" tabindex="-1">Foods</h2>
        <p class="eyebrow">${result.items.length} found</p>
      </div>
      <ol class="items">${result.items.map((r, i) => itemHtml(r, i, gramsScale)).join("")}</ol>
    </section>`;
}

function itemHtml(r, i, gramsScale) {
  const { item, match } = r;
  const range = itemGrams(item);
  const weighed = isExact(range);
  const kcal = r.nutrients.energy_kcal;
  const name = esc(capitalize(item.name));
  const method = item.cooking_method === "unknown" ? "" : capitalize(item.cooking_method.replace(/_/g, " "));
  const meta = [
    method && `<span class="chip">${esc(method)}</span>`,
    weighed ? `<span class="chip chip--ok">${icon("check")}Weighed</span>` : null,
    item.hidden_fat_likely ? `<span class="chip chip--warn">${icon("drop")}Likely hidden oil or butter</span>` : null,
  ].filter(Boolean);
  const energy = match
    ? kcal
      ? rangeHtml(kcal, "kcal", undefined, { stack: true })
      : '<span class="value__none">No energy data</span>'
    : '<span class="chip chip--alert">Not counted</span>';
  const matchLine = match
    ? `<p class="item__match"><span class="item__src">USDA<span class="visually-hidden">:</span></span> ${esc(match.description)}<span class="item__id">${SEP}${esc(match.data_type)} #${match.fdc_id}</span></p>`
    : `<p class="item__match is-missing">No close USDA match, so this food isn't in the totals yet. Pick one to count it.</p>`;
  return `
    <li class="item card" data-index="${i}" style="--i:${i}">
      <div class="item__head">
        <span class="item__n" aria-hidden="true">${i + 1}</span>
        <h3 class="item__name">${name}</h3>
        <p class="item__kcal">${energy}</p>
      </div>
      ${meta.length ? `<p class="item__meta">${meta.join(SEP)}</p>` : ""}
      <div class="item__grams">
        <span class="field-label">${weighed ? "Weight" : "Estimated weight"}</span>
        ${bandHtml(`grams-${i}`, range, { scale: gramsScale })}${rangeHtml(range, "g", grams)}
      </div>
      ${matchLine}
      <div class="item__actions">
        <button class="btn btn--soft" type="button" data-action="weigh" data-index="${i}" aria-haspopup="dialog">${icon("scale")}${weighed ? "Change weight" : "Weigh it"}<span class="visually-hidden">: ${name}</span></button>
        <button class="btn btn--soft" type="button" data-action="food" data-index="${i}" aria-haspopup="dialog">${icon("swap")}${match ? "Change food" : "Pick a USDA food"}<span class="visually-hidden">: ${name}</span></button>
      </div>
    </li>`;
}

function nutrientRowsHtml(totals, dv, { micro, prefix, skip = [] }) {
  return state.nutrients
    .filter((n) => n.micro === micro && totals[n.key] && !skip.includes(n.key))
    .map(
      (n, j) => `
      <li class="nutrient">
        <span class="nutrient__head">
          <span class="nutrient__label">${esc(n.label)}${SEP}</span>
          <span class="nutrient__value">${rangeHtml(totals[n.key], n.unit)}</span>
        </span>
        ${
          dv[n.key]
            ? bandHtml(`${prefix}-${n.key}`, dv[n.key], { index: j, scale: Math.max(100, dv[n.key].high * 1.05) })
            : `<span class="nutrient__nodv">${SEP}No Daily Value</span>`
        }
        <span class="nutrient__dv">${dv[n.key] ? `${SEP}${dvHtml(dv[n.key])}` : ""}</span>
      </li>`
    )
    .join("");
}

function nutrientsHtml(result) {
  const macros = nutrientRowsHtml(result.totals, result.daily_value_pct, { micro: false, prefix: "n", skip: ["energy_kcal"] });
  const micros = nutrientRowsHtml(result.totals, result.daily_value_pct, { micro: true, prefix: "n" });
  if (!macros && !micros) return "";
  const partial = result.partial_nutrients.length
    ? `<p class="note">Some foods have no USDA data for ${esc(
        result.partial_nutrients.map((k) => labelOf(k).toLowerCase()).join(", ")
      )}, so those totals undercount.</p>`
    : "";
  return `
    <section class="block" aria-labelledby="nutrients-title">
      <div class="section-head">
        <h2 id="nutrients-title" class="section-title">Nutrients</h2>
        <p class="eyebrow">Bars: % Daily Value</p>
      </div>
      <div class="card nutrients-card">
        <ul class="nutrients">${macros}</ul>
        <p class="note">Bars run to 100% of the FDA Daily Value, the reference on food labels. It is not a goal.</p>
        ${micros ? microDetailsHtml(micros) : ""}
        ${partial}
      </div>
    </section>`;
}

function microDetailsHtml(rows) {
  return `
    <details class="micro">
      <summary>Micronutrients <span class="chip">Estimates</span></summary>
      <p class="note">Photos can't show vitamins and minerals well. Treat these as rough estimates; a weekly trend says more than any single meal or day.</p>
      <ul class="nutrients">${rows}</ul>
    </details>`;
}

function notesHtml(result) {
  const notes = [...result.context.notes, ...result.notes];
  if (!notes.length) return "";
  return `
    <section class="block" aria-labelledby="notes-title">
      <h2 id="notes-title" class="eyebrow section-head">Notes</h2>
      <ul class="card notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    </section>`;
}

// ----------------------------------------------------------- result actions

async function withBusy(button, task) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await task();
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

function highlight(index) {
  const boxes = $(".boxes", els.result);
  $$(".item", els.result).forEach((el) => el.classList.toggle("is-active", el.dataset.index === index));
  if (!boxes) return;
  $$(".box", boxes).forEach((el) => el.classList.toggle("is-active", el.dataset.index === index));
  if (index != null && $(`.box[data-index="${index}"]`, boxes)) boxes.dataset.active = index;
  else delete boxes.dataset.active;
}

/** A meal came back changed: update every tab that holds it, and the screen if it shows it. */
function updateMeal(result, { focus } = {}) {
  for (const slot of [state.slots.analyze, state.slots.today]) {
    if (slot && result.id != null && slot.result.id === result.id) slot.result = result;
  }
  if (state.shown?.result === result) renderResult("update", { focus });
}

els.result.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const index = Number(button.closest("[data-index]")?.dataset.index);
  switch (button.dataset.action) {
    case "back":
      closeSlot(shownKey(), { focus: true });
      syncHistory();
      break;
    case "new":
      startNewMeal();
      break;
    case "see-today":
      if (shownKey() === "today") closeSlot("today", { focus: true });
      else setView("today");
      syncHistory();
      break;
    case "dismiss-question":
      state.dismissedQuestion = shownMeal().id;
      renderResult("update", { focus: "#foods-title" });
      break;
    case "weigh":
    case "food":
      openSheet(button.dataset.action, index);
      break;
  }
});

els.result.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.form !== "answer") return;
  const button = $('button[type="submit"]', form);
  const answer = String(new FormData(form).get("answer")).trim();
  const id = shownMeal()?.id;
  if (!answer || id == null) return;
  await withBusy(button, async () => {
    try {
      updateMeal(await api(`/api/meals/${id}/answer`, { method: "POST", ...jsonBody({ answer }) }), { focus: "#result-title" });
      toast("Thanks. The ranges now include your answer.");
      loadToday();
    } catch (err) {
      if (form.isConnected) $(".question__error", form).textContent = err.message;
    }
  });
});

els.result.addEventListener("pointerover", (event) => {
  if (!state.open) highlight(event.target.closest(".item")?.dataset.index ?? null);
});
els.result.addEventListener("pointerleave", () => state.open || highlight(null));
els.result.addEventListener("focusin", (event) => highlight(event.target.closest(".item")?.dataset.index ?? null));

// ------------------------------------------------------------ edit sheet
// Fixing a food (exact grams or a different USDA match) happens in a sheet over the result.

function openSheet(kind, index) {
  const meal = shownMeal();
  const r = meal.items[index];
  clearTimeout(closeTimer);
  els.sheet.classList.remove("is-closing");
  els.sheet.style.transform = "";
  sheetMessage = "";
  state.open = { index, kind, id: meal.id };
  els.sheet.dataset.kind = kind;
  const weighed = isExact(itemGrams(r.item));
  els.sheetKicker.textContent = kind === "weigh" ? (weighed ? "Change weight" : "Weigh it") : r.match ? "Change food" : "Pick a USDA food";
  els.sheetTitle.textContent = capitalize(r.item.name);
  els.sheetBody.innerHTML = kind === "weigh" ? weighHtml(r, index) : foodHtml(r, index);
  animateBands(els.sheetBody, new Map());
  document.documentElement.classList.add("has-sheet");
  if (!els.sheet.open) els.sheet.showModal();
  highlight(String(index));
  if (kind === "weigh") $("input", els.sheetBody).focus();
  else if (finePointer.matches) $("[data-search]", els.sheetBody).focus();
  else els.sheetTitle.focus(); // no keyboard popping up over the suggestions on phones
}

let closeTimer = 0;
let restoreFocus = true;
let sheetMessage = "";

/** `message` becomes a toast once the sheet has closed: while a modal is open, the rest of the page,
    the toast's live region included, is hidden from screen readers. */
function closeSheet({ restoreFocus: restore = true, swiped = false, message = "" } = {}) {
  if (!els.sheet.open) {
    if (message) toast(message);
    return;
  }
  restoreFocus = restore;
  if (message) sheetMessage = message;
  const done = () => els.sheet.open && els.sheet.close();
  clearTimeout(closeTimer);
  if (reducedMotion.matches) return done();
  if (swiped) {
    els.sheet.style.transition = "transform 200ms ease-in";
    els.sheet.style.transform = "translateY(100%)";
  } else {
    els.sheet.classList.add("is-closing");
  }
  closeTimer = setTimeout(done, 190);
}

els.sheet.addEventListener("close", () => {
  clearTimeout(closeTimer);
  els.sheet.classList.remove("is-closing");
  els.sheet.style.transition = "";
  els.sheet.style.transform = "";
  document.documentElement.classList.remove("has-sheet");
  const open = state.open;
  state.open = null;
  els.sheetBody.innerHTML = "";
  highlight(null);
  if (open && restoreFocus) $(`[data-action="${open.kind}"][data-index="${open.index}"]`, els.result)?.focus();
  restoreFocus = true;
  if (sheetMessage) toast(sheetMessage);
  sheetMessage = "";
});

// Escape and the Android back gesture: animate out instead of vanishing.
els.sheet.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeSheet();
});

let pressedBackdrop = false;
let drag = null;

els.sheet.addEventListener("pointerdown", (event) => {
  pressedBackdrop = event.target === els.sheet;
  // Drag the grabber or the header down to dismiss, like a native sheet.
  if (!bottomSheet.matches || event.button !== 0 || event.target.closest("button")) return;
  if (!event.target.closest(".sheet__grab, .sheet__head")) return;
  drag = { id: event.pointerId, y: event.clientY, dy: 0 };
  els.sheet.setPointerCapture(event.pointerId);
  els.sheet.style.transition = "none";
});

els.sheet.addEventListener("pointermove", (event) => {
  if (drag?.id !== event.pointerId) return;
  drag.dy = Math.max(0, event.clientY - drag.y);
  els.sheet.style.transform = `translateY(${drag.dy}px)`;
});

function endDrag(event) {
  if (drag?.id !== event.pointerId) return;
  const { dy } = drag;
  drag = null;
  if (dy > 80) return closeSheet({ swiped: true });
  els.sheet.style.transition = "transform 240ms cubic-bezier(0.16, 1, 0.3, 1)";
  els.sheet.style.transform = "";
}

els.sheet.addEventListener("pointerup", endDrag);
els.sheet.addEventListener("pointercancel", endDrag);

// Keep the bottom sheet above the on-screen keyboard: --kb is the part of the screen it covers.
if (window.visualViewport) {
  const vv = window.visualViewport;
  const lift = () => {
    const covered = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty("--kb", `${Math.round(covered)}px`);
  };
  vv.addEventListener("resize", lift);
  vv.addEventListener("scroll", lift);
}

els.sheet.addEventListener("click", (event) => {
  if (event.target === els.sheet && pressedBackdrop) return closeSheet();
  const button = event.target.closest("[data-action]");
  if (!button || !state.open) return;
  if (button.dataset.action === "close-sheet") closeSheet();
  if (button.dataset.action === "pick") correct(state.open.index, { fdc_id: Number(button.dataset.fdc) }, button, "food");
});

els.sheet.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.form !== "weigh" || !state.open) return;
  const value = Number(new FormData(form).get("grams"));
  correct(state.open.index, { grams: Math.round(value) }, $('button[type="submit"]', form), "weigh");
});

let searchTimer = 0;
let searchSeq = 0;

els.sheet.addEventListener("input", (event) => {
  const input = event.target.closest("[data-search]");
  if (!input) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchFoods(input), 220);
});

els.sheet.addEventListener("keydown", (event) => {
  const input = event.target.closest("[data-search]");
  if (!input || event.key !== "Enter") return;
  event.preventDefault();
  clearTimeout(searchTimer);
  searchFoods(input);
  if (!finePointer.matches) input.blur(); // put the keyboard away so the results show
});

function weighHtml(r, i) {
  const range = itemGrams(r.item);
  return `
    <div class="estimate">
      <span class="field-label">${isExact(range) ? "Current weight" : "Estimated weight"}</span>
      ${bandHtml(`sheet-grams-${i}`, range, { scale: range.high * 1.1 || 1 })}${rangeHtml(range, "g", grams)}
    </div>
    <form class="weigh" data-form="weigh">
      <label class="field-label" for="grams-${i}">Exact weight in grams<span class="visually-hidden"> of ${esc(r.item.name)}</span></label>
      <div class="weigh__row">
        <span class="field-unit">
          <input id="grams-${i}" class="field" name="grams" type="number" inputmode="numeric" pattern="[0-9]*" min="1" max="10000" step="1" required placeholder="${r.item.grams_likely}" enterkeyhint="done" aria-describedby="weigh-hint">
          <span aria-hidden="true">g</span>
        </span>
        <button class="btn btn--primary" type="submit">${icon("check")}Set weight</button>
      </div>
      <p id="weigh-hint" class="sheet-hint">Collapses this food's range to one number. MealScholar remembers it as your usual portion.</p>
      <p class="sheet-error" role="alert"></p>
    </form>`;
}

function foodHtml(r, i) {
  const options = [r.match, ...r.alternatives].filter(Boolean);
  const list = options.length
    ? options.map((m) => optionHtml(m, r.match?.fdc_id === m.fdc_id)).join("")
    : '<li class="options__empty">No suggestions. Try a search above.</li>';
  return `
    <label class="field-label" for="search-${i}">Search USDA FoodData Central</label>
    <span class="search">${icon("search")}<input id="search-${i}" class="field" type="search" data-search="${i}" autocomplete="off" autocapitalize="none" spellcheck="false" enterkeyhint="search" placeholder="e.g. rice, white, cooked"></span>
    <p class="visually-hidden" data-count aria-live="polite"></p>
    <ul class="options" data-results="${i}" aria-label="Search results"></ul>
    <p class="sheet-error" role="alert"></p>
    <div class="suggest" data-suggest>
      <p class="field-label">${r.match ? "Closest USDA foods" : "Suggestions (loose matches)"}</p>
      <ul class="options">${list}</ul>
    </div>`;
}

function optionHtml(food, current = false) {
  const kcal = food.energy_kcal_per_100g;
  const meta = [food.data_type, kcal != null ? `${nf0.format(kcal)} kcal/100 g` : null].filter(Boolean).join(" · ");
  return `
    <li>
      <button class="option" type="button" data-action="pick" data-fdc="${food.fdc_id}"${current ? ' aria-current="true" disabled' : ""}>
        <span class="option__name">${esc(food.description)}${SEP}</span>
        <span class="option__meta">${esc(meta)}</span>
        <span class="option__end">${current ? `${icon("check")}${SEP}Current` : icon("chevron-right")}</span>
      </button>
    </li>`;
}

async function correct(index, body, button, kind) {
  const error = $(".sheet-error", els.sheetBody);
  const id = state.open?.id;
  if (id == null) return;
  await withBusy(button, async () => {
    try {
      updateMeal(await api(`/api/meals/${id}/items/${index}`, { method: "PATCH", ...jsonBody(body) }));
      closeSheet({ message: kind === "weigh" ? "Weight set. Totals updated." : "Food changed. Totals updated." });
      loadToday();
    } catch (err) {
      if (error?.isConnected) error.textContent = err.message;
    }
  });
}

async function searchFoods(input) {
  const index = Number(input.dataset.search);
  const list = $(`[data-results="${index}"]`, els.sheetBody);
  const count = $("[data-count]", els.sheetBody);
  const suggest = $("[data-suggest]", els.sheetBody);
  if (!list) return;
  const q = input.value.trim();
  const seq = ++searchSeq;
  if (q.length < 2) {
    list.innerHTML = "";
    count.textContent = "";
    suggest.hidden = false;
    return;
  }
  try {
    const found = await api(`/api/foods?q=${encodeURIComponent(q)}&limit=6`);
    if (seq !== searchSeq || !list.isConnected) return;
    const current = shownMeal()?.items[index]?.match?.fdc_id;
    list.innerHTML = found.foods.length
      ? (found.exact ? "" : '<li class="options__empty">No exact match. Closest results:</li>') +
        found.foods.map((f) => optionHtml(f, f.fdc_id === current)).join("")
      : '<li class="options__empty">No USDA foods match that.</li>';
    count.textContent = found.foods.length
      ? `${found.foods.length} ${found.exact ? "results" : "closest results, no exact match"}.`
      : "No USDA foods match that.";
    // The suggestions would repeat what the search shows; they come back when the field is cleared.
    suggest.hidden = found.foods.length > 0;
  } catch (err) {
    if (seq !== searchSeq || !list.isConnected) return;
    list.innerHTML = `<li class="options__empty is-error">${esc(err.message)}</li>`;
    count.textContent = err.message; // the live region announces the failure too
  }
}

// ----------------------------------------------------------------- toast

let toastTimer = 0;

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.classList.remove("is-visible");
    toastTimer = setTimeout(() => (els.toast.textContent = ""), 320);
  }, 2600);
}

// ----------------------------------------------------------------- today

async function loadToday() {
  const day = localDate();
  els.todayDate.textContent = dayFormat.format(new Date());
  try {
    renderToday(await api(`/api/days/${day}`));
  } catch (err) {
    els.today.innerHTML = `<p class="status is-error" role="alert">${esc(err.message)}</p>`;
  }
}

function renderToday(view) {
  if (!view.meals.length) {
    els.today.innerHTML = `
      <div class="card empty">
        <span class="empty__icon" aria-hidden="true">${icon("plate")}</span>
        <p class="empty__title">Nothing logged yet today</p>
        <p>Each meal you analyze lands here with its ranges, and the day's total adds up. No targets, just what you ate.</p>
        <button class="btn btn--primary if-narrow" type="button" data-action="start"${state.ready ? "" : " disabled"}>${icon("camera")}Take or choose a photo</button>
      </div>`;
    return;
  }
  const meals = view.meals
    .map((m) => {
      const kcal = m.totals.energy_kcal;
      return `
      <li>
        <button class="meal" type="button" data-meal="${m.id}">
          <span class="meal__slot">${esc(capitalize(m.context.meal_slot))}${SEP}<span class="meal__time">${timeFormat.format(wallClock(m.context.taken_at))}</span></span>
          <span class="meal__names">${SEP}${esc(m.items.map((r) => r.item.name).join(", "))}</span>
          <span class="meal__kcal">${SEP}${kcal ? rangeHtml(kcal, "kcal", undefined, { stack: true }) : '<span class="value__none">Not counted</span>'}</span>
          <span class="meal__chev" aria-hidden="true">${icon("chevron-right")}</span>
        </button>
      </li>`;
    })
    .join("");
  const kcal = view.totals.energy_kcal;
  const macros = nutrientRowsHtml(view.totals, view.daily_value_pct, { micro: false, prefix: "d", skip: ["energy_kcal"] });
  const micros = nutrientRowsHtml(view.totals, view.daily_value_pct, { micro: true, prefix: "d" });
  const count = `${view.meals.length} meal${view.meals.length === 1 ? "" : "s"}`;
  const kcalHtml = kcal
    ? `<p class="day-total__value">${kcalRangeHtml(kcal)}</p>
       ${bandHtml("d-kcal", kcal, { dark: true })}
       ${isExact(kcal) ? "" : `<p class="day-total__likely">Most likely <strong>${amount(kcal.likely, "kcal")}&nbsp;kcal</strong>.</p>`}`
    : '<p class="day-total__likely">Nothing counted yet.</p>';
  const previous = snapshotBands(els.today);
  els.today.innerHTML = `
    <div class="card day-total">
      <p class="eyebrow">Day total · ${count}</p>
      ${kcalHtml}
    </div>
    <section class="block" aria-labelledby="meals-title">
      <div class="section-head">
        <h3 id="meals-title" class="section-title">Meals</h3>
        <p class="eyebrow">${count}</p>
      </div>
      <ol class="card meals" aria-label="Meals logged today">${meals}</ol>
    </section>
    ${
      macros || micros
        ? `<section class="block" aria-labelledby="day-nutrients-title">
            <div class="section-head">
              <h3 id="day-nutrients-title" class="section-title">Nutrients</h3>
              <p class="eyebrow">% Daily Value</p>
            </div>
            <div class="card nutrients-card">
              <ul class="nutrients">${macros}</ul>
              <p class="note">Information only. The Daily Value is a label reference, not a target.</p>
              ${micros ? microDetailsHtml(micros) : ""}
            </div>
          </section>`
        : ""
    }`;
  animateBands(els.today, previous);
  markOpenMeal();
}

/** A logged meal opens over Today's list (in the main column on wide screens). */
function openLogged(result) {
  discardSlot("today");
  state.slots.today = { result, photoUrl: null };
  syncResult({ enter: "today" });
  syncHistory();
}

els.today.addEventListener("click", async (event) => {
  if (event.target.closest('[data-action="start"]')) return startNewMeal({ pick: true });
  const button = event.target.closest("[data-meal]");
  if (!button) return;
  const id = button.dataset.meal;
  $("[data-meal-error]", els.today)?.remove();
  // Wide screens: the meal you just analyzed opens as itself, photo included, not as a copy without it.
  if (wide.matches && String(state.slots.analyze?.result.id) === id) {
    if (state.slots.today) closeSlot("today", { focus: true });
    else $("#result-title", els.result)?.focus();
    syncHistory();
    return;
  }
  state.scroll.list = window.scrollY;
  state.openedMeal = id;
  await withBusy(button, async () => {
    try {
      openLogged(await api(`/api/meals/${id}`));
    } catch (err) {
      // One alert at a time, however often it fails.
      $("[data-meal-error]", els.today)?.remove();
      els.today.insertAdjacentHTML("afterbegin", `<p class="status is-error" role="alert" data-meal-error>${esc(err.message)}</p>`);
    }
  });
});

// --------------------------------------------------------------- history
// Every screen above the capture screen has a history entry, so the Android back gesture and the
// browser's back button step back through the app (a logged meal, then Today, then a result)
// instead of leaving it.

const baseUrl = location.pathname + location.search;

/** The screens stacked above the capture screen right now. */
function layers() {
  const out = [];
  if (state.slots.analyze) out.push("result");
  if (wide.matches) {
    if (state.slots.today) out.push("meal");
  } else if (state.view === "today") {
    out.push("today");
    if (state.slots.today) out.push("meal");
  }
  return out;
}

const urlFor = (list) => (list.includes("today") ? "#today" : baseUrl);
const entryLayers = (entry) => (Array.isArray(entry?.layers) ? entry.layers : []);

let historyTask = Promise.resolve();
let ownTraversal = null;

/** After the app navigates by itself, make the history entries match the screen. */
function syncHistory() {
  historyTask = historyTask.then(stepHistory, stepHistory);
}

async function stepHistory() {
  const want = layers();
  const have = entryLayers(history.state);
  let same = 0;
  while (same < want.length && same < have.length && want[same] === have[same]) same += 1;
  if (have.length > same) await traverse(same - have.length);
  for (let i = same; i < want.length; i += 1) {
    const list = want.slice(0, i + 1);
    history.pushState({ layers: list }, "", urlFor(list));
  }
}

function traverse(delta) {
  return new Promise((resolve) => {
    let timer = 0;
    const done = () => {
      clearTimeout(timer);
      ownTraversal = null;
      resolve();
    };
    ownTraversal = done;
    timer = setTimeout(done, 800);
    history.go(delta);
  });
}

window.addEventListener("popstate", (event) => {
  if (ownTraversal) return ownTraversal();
  applyLayers(entryLayers(event.state));
});

/** The user went back (or forward): show the screens of the entry they landed on. */
function applyLayers(target) {
  const current = layers();
  const dropped = (name) => current.includes(name) && !target.includes(name);
  if (els.sheet.open) closeSheet({ restoreFocus: false });
  if (dropped("meal")) closeSlot("today", { focus: true });
  if (dropped("result")) closeSlot("analyze", { focus: true });
  if (!wide.matches) setView(target.includes("today") ? "today" : "analyze");
  const now = layers();
  if (now.join() !== target.join()) history.replaceState({ layers: now }, "", urlFor(now));
  // Focus that was left in a view that is now hidden goes to the view's tab.
  const active = document.activeElement;
  if (active && active !== document.body && !active.getClientRects().length) {
    if (!wide.matches) $(".tab[aria-current]")?.focus({ preventScroll: true });
    else if (state.shown) $("#result-title", els.result)?.focus({ preventScroll: true });
    else focusCapture();
  }
}

// ----------------------------------------------------------------- wiring

for (const button of $$("[data-pick]")) {
  button.addEventListener("click", () => (button.dataset.pick === "camera" ? els.camera : els.library).click());
}
for (const input of [els.library, els.camera]) {
  input.addEventListener("change", () => input.files[0] && analyze(input.files[0]));
}

let dragDepth = 0;
els.capture.addEventListener("dragenter", (event) => {
  if (!state.ready || !event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  dragDepth += 1;
  if (els.viewfinder.dataset.state === "empty") els.viewfinder.dataset.state = "dragging";
});
els.capture.addEventListener("dragover", (event) => {
  if (state.ready && event.dataTransfer?.types.includes("Files")) event.preventDefault();
});
els.capture.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth && els.viewfinder.dataset.state === "dragging") els.viewfinder.dataset.state = "empty";
});
els.capture.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  if (els.viewfinder.dataset.state === "dragging") els.viewfinder.dataset.state = "empty";
  const file = event.dataTransfer?.files[0];
  if (file) analyze(file);
});

els.location.addEventListener("change", async () => {
  const target = els.locationStatus;
  if (!els.location.checked) return setStatus("", { target });
  try {
    await devicePosition(); // ask for permission now, not mid-analysis
    setStatus("Location is on.", { target });
  } catch (err) {
    els.location.checked = false;
    setStatus(err.message, { error: true, target });
  }
});

// Skip to the content without adding a "#main" history entry.
els.skip.addEventListener("click", (event) => {
  event.preventDefault();
  els.main.focus();
});

async function init() {
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const startOnToday = location.hash === "#today";
  history.replaceState({ layers: [] }, "", baseUrl);
  if (startOnToday) {
    setView("today");
    syncHistory();
  }
  onScroll();
  syncHeadingLevel();
  try {
    const [status, nutrients] = await Promise.all([api("/api/status"), api("/api/nutrients")]);
    state.nutrients = nutrients;
    renderSetup(status);
  } catch (err) {
    setPickers(false);
    setStatus(err.message, { error: true });
    els.today.innerHTML = `<p class="status is-error" role="alert">${esc(err.message)}</p>`;
    return;
  }
  loadToday();
}

init();
