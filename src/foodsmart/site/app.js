// MealScholar web client: a plain ES module over the JSON API (web.py, plus auth.py when accounts
// are on); no build step. Product rules (README, AGENTS.md): every number is a range until the user
// weighs the food, micronutrients are labelled estimates, and there are no calorie targets, goals or
// "remaining" numbers. % Daily Value is information only.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const els = {
  topbar: $(".topbar"),
  skip: $(".skip"),
  main: $("#main"),
  auth: $("#auth"),
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
  greeting: $("#today-greeting"),
  banner: $("#today-banner"),
  dayPrev: $("#day-prev"),
  dayNext: $("#day-next"),
  dayToday: $("#day-today"),
  dayStatus: $("#day-status"),
  dashNotice: $("#dash-notice"),
  dashDay: $("#dash-day"),
  dashWeek: $("#dash-week"),
  dashMicro: $("#dash-micro"),
  tabs: $$(".tab[data-view]"),
  badges: $$(".tab__badge"),
  accountButton: $("#account-button"),
  account: $("#account"),
  accountTitle: $("#account-title"),
  accountBody: $("#account-body"),
  sheet: $("#sheet"),
  sheetKicker: $("#sheet-kicker"),
  sheetTitle: $("#sheet-title"),
  sheetBody: $("#sheet-body"),
  toast: $("#toast"),
};

const media = (query) => window.matchMedia(query);
const bottomSheet = media("(max-width: 39.99em)"); // sheets slide up from the bottom
const finePointer = media("(pointer: fine)");
const reducedMotion = media("(prefers-reduced-motion: reduce)");

const state = {
  ready: false,
  analyzing: false,
  nutrients: [],
  // Each tab keeps its own meal: a new analysis, with its photo, under Analyze and a logged meal
  // under Today. Only the active tab's meal is on screen.
  slots: { analyze: null, today: null }, // { result, photoUrl }
  shown: null, // the slot rendered into #result
  open: null, // { index, kind, id } while the edit sheet is open
  dismissedQuestion: null,
  view: "analyze",
  home: "analyze", // the screen everything else stacks on: Analyze, or the Today dashboard with accounts
  scroll: { analyze: 0, today: 0, list: 0 },
  openedMeal: null,
  // Accounts: only when GET /api/status says auth_required.
  authRequired: false,
  emailProvider: null,
  user: null,
  demoVerifyUrl: null,
  resetToken: null,
  // Dashboard
  day: null, // the day on show, YYYY-MM-DD
  todayKey: null, // what "today" was when last checked
  dashSeq: 0,
  microOpen: false,
};

const TIME_SOURCE = { exif: "time from the photo", device: "time from your device", upload: "time of upload" };

const icon = (name) => `<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;

// A pause for screen readers where only CSS spacing separates two pieces of text.
const SEP = '<span class="visually-hidden">, </span>';

// ------------------------------------------------------------------- api

const OFFLINE = "Can't reach MealScholar right now. Check your connection and try again.";

class ApiError extends Error {
  constructor(message, { status = 0, field = null, retryAfter = null } = {}) {
    super(message);
    this.status = status;
    this.field = field; // the form field the server says the error is about
    this.retryAfter = retryAfter;
    this.signedOut = false; // the sign-in ended and the sign-in screen has taken over
  }
}

/** `session`: a 401 here means the sign-in ended (meal and day routes; some account routes). */
async function api(path, { method = "GET", body, headers, session = !path.startsWith("/api/auth/") } = {}) {
  let response;
  try {
    response = await fetch(path, { method, body, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(OFFLINE);
  }
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await response.json().catch(() => null) : null;
  if (response.ok) return data;
  const retryAfter = retrySeconds(response.headers.get("retry-after"));
  const field = typeof data?.field === "string" ? data.field : null;
  let message = errorDetail(data) ?? `Request failed (${response.status}).`;
  if (response.status === 429) message = tooManyMessage(retryAfter) ?? message;
  const error = new ApiError(message, { status: response.status, field, retryAfter });
  if (response.status === 401 && session && !field) {
    error.signedOut = state.authRequired;
    signInLost();
  }
  throw error;
}

function errorDetail(data) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((d) => d.msg).join("; ");
  return null;
}

/** Retry-After as whole seconds: a number of seconds or an HTTP date. */
function retrySeconds(header) {
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return Math.max(1, Math.ceil(seconds));
  const at = Date.parse(header);
  return Number.isNaN(at) ? null : Math.max(1, Math.ceil((at - Date.now()) / 1000));
}

function waitText(seconds) {
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 90) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  return `${Math.ceil(minutes / 60)} hours`;
}

const tooManyMessage = (seconds) => (seconds ? `Too many attempts. Try again in ${waitText(seconds)}.` : null);

const jsonBody = (value) => ({ body: JSON.stringify(value), headers: { "Content-Type": "application/json" } });

// ------------------------------------------------------------ formatting

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const amount = (value, unit) => (unit === "kcal" || value >= 100 ? nf0 : nf1).format(value);
const grams = (value) => nf0.format(value);
/** Dashboard tiles: whole numbers once the range reaches 10, else one decimal, the same for all three. */
const tileFormat = (range) => (value) => (range.high >= 10 ? nf0 : nf1).format(value);
/** Week chart labels: 980, 1.7k, 12k. */
const compactKcal = (value) => (value < 999.5 ? nf0.format(value) : `${nf1.format(value / 1000).replace(/\.0$/, "")}k`);

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
const longDay = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" });
const monthDay = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric" });
const monthDayYear = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric" });
const monthYear = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" });
const weekdayLong = new Intl.DateTimeFormat("en-US", { weekday: "long" });
const weekdayShort = new Intl.DateTimeFormat("en-US", { weekday: "short" });

const pad = (n) => String(n).padStart(2, "0");

function localDate(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function parseDay(key) {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shiftDay(key, delta) {
  const date = parseDay(key);
  date.setDate(date.getDate() + delta);
  return localDate(date);
}

/** `count` days ending with `end`, oldest first. */
const lastDays = (end, count) => Array.from({ length: count }, (_, i) => shiftDay(end, i - count + 1));

/** The diary files a meal under the local date of its photo. */
const mealDay = (result) => localDate(wallClock(result.context.taken_at));

const isExact = (range) => Math.abs(range.high - range.low) < 1e-9;
const labelOf = (key) => state.nutrients.find((n) => n.key === key)?.label ?? key;
const unitOf = (key) => state.nutrients.find((n) => n.key === key)?.unit ?? (key.endsWith("_mg") ? "mg" : "g");
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const placeName = (ctx) => ctx.place?.name ?? ctx.place_name ?? null;

/** "Fast food, american, burger": the OpenStreetMap place type and its cuisines, readable. */
function placeKind(place) {
  const words = (s) => s.replace(/_/g, " ").trim();
  const cuisines = (place.cuisine ?? "").split(";").map(words).filter(Boolean);
  return capitalize([words(place.kind ?? ""), ...cuisines].filter(Boolean).join(", "));
}

// ---------------------------------------------------------------- bands
// A band is a track with the low–high range and an ink tick at the likely value. Positions are
// transforms only; on re-render each band moves from where it was, so weighing a food visibly
// collapses its range onto one line, and changing days slides every bar to the new day.

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
// One view at a time on every screen: Today (the dashboard, or a logged meal opened from it) and
// Analyze (the capture screen, or a new result). Phones switch with the tab bar at the bottom,
// wide screens with the tabs in the top bar.

/** The slot #result shows: the active tab's meal, if it has one. */
function shownKey() {
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

function syncTabs() {
  for (const tab of els.tabs) {
    if (tab.dataset.view === state.view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
}

function setView(view) {
  if (view === state.view) return;
  state.scroll[state.view] = window.scrollY;
  state.view = view;
  document.body.dataset.view = view;
  syncTabs();
  if (view === "analyze") showBadge(false);
  syncResult();
  $$(".view").forEach(replayEntrance);
  window.scrollTo(0, state.scroll[view] ?? 0);
  if (view === "today") ensureDashboard();
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
    else if (view === "today" && state.day !== state.todayKey) goToDay(state.todayKey);
    else scrollTop();
    syncHistory();
  });
}

const onScroll = () => els.topbar.classList.toggle("is-scrolled", window.scrollY > 4);
window.addEventListener("scroll", onScroll, { passive: true });

/** The tab that is on screen for this layout: the tab bar on phones, the top bar on wide screens. */
const visibleTab = () => els.tabs.find((tab) => tab.matches("[aria-current]") && tab.getClientRects().length);

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
  for (const button of $$("[data-pick], [data-action='start'], [data-action='snap']")) button.disabled = !enabled;
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
  for (const badge of els.badges) badge.hidden = !on;
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
  // A new photo replaces the last analysis and brings Analyze to the front.
  const leaving = Boolean(state.shown) || state.view !== "analyze";
  discardSlot("analyze");
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
  // On a short phone the frame's bottom, where the progress pill sits, would hide behind the tab bar.
  els.viewfinder.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
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
    if (state.view === "analyze") {
      syncResult({ enter: "analyze" });
    } else {
      // The user moved to Today while waiting: leave them there and point at the Analyze tab.
      syncResult();
      showBadge(true);
      toast("Your meal is ready. Open Analyze to see it.");
    }
    syncHistory();
    if (result.id != null) refreshDays([mealDay(result)]);
  } catch (err) {
    URL.revokeObjectURL(photoUrl);
    if (!err.signedOut) {
      setStatus(err.message, { error: true });
      if (state.view !== "analyze") toast("That photo couldn't be analyzed. Open Analyze to see why.");
    }
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

/** Close a tab's meal: Analyze goes back to the capture screen, Today to the dashboard. */
function closeSlot(key, { focus = false } = {}) {
  const slot = state.slots[key];
  if (!slot) return;
  const wasShown = slot === state.shown;
  discardSlot(key);
  if (!state.analyzing) setStatus("");
  syncResult();
  if (!wasShown) return;
  if (key === "today") {
    window.scrollTo(0, state.scroll.list);
    replayEntrance(els.todayView);
    if (focus && state.openedMeal != null) {
      $(`[data-meal="${CSS.escape(String(state.openedMeal))}"]`, els.today)?.focus({ preventScroll: true });
    }
    return;
  }
  window.scrollTo(0, 0);
  replayEntrance(els.capture);
  if (focus) focusCapture();
}

/** Back to a fresh capture screen. */
function startNewMeal() {
  if (state.shown && state.shown === state.slots.today) discardSlot("today"); // leaving a logged meal closes it
  discardSlot("analyze");
  if (!state.analyzing) setStatus("");
  setView("analyze");
  syncResult();
  window.scrollTo(0, 0);
  replayEntrance(els.capture);
  syncHistory();
  focusCapture();
}

/** "Snap a meal": straight to the photo picker. The app moves to Analyze once a photo is chosen,
    so cancelling the picker leaves you where you were. */
function snapMeal() {
  if (!state.ready) return;
  if (state.analyzing) {
    toast("Still analyzing the last photo.");
    if (state.view !== "analyze") {
      setView("analyze");
      syncHistory();
    }
    return;
  }
  els.library.click();
}

// ---------------------------------------------------------------- result

/** Mark the open meal in the dashboard's list. */
function markOpenMeal() {
  const id = state.slots.today?.result.id;
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

/** "Today", "Yesterday" or the weekday of the day on show, for buttons that lead back to it. */
function dayName(key = state.day ?? localDate()) {
  return dayTitle(key).title;
}

function resultHtml(slot) {
  const { result, photoUrl } = slot;
  const ctx = result.context;
  const when = wallClock(ctx.taken_at);
  const place = placeName(ctx) ? ` <span class="result__place">at ${esc(placeName(ctx))}</span>` : "";
  // Date and time source stay whole; a long list of OpenStreetMap cuisines may wrap.
  const meta = [`${dayFormat.format(when)}, ${timeFormat.format(when)}`, TIME_SOURCE[ctx.time_source]]
    .filter(Boolean)
    .map((part, i) => `<span class="nowrap">${i ? "· " : ""}${esc(part)}</span>`);
  if (ctx.place) meta.push(`<span>·&nbsp;${esc(placeKind(ctx.place))}</span>`);
  const fromToday = slot === state.slots.today;
  const name = dayName();
  const backTo = /^(Today|Yesterday)$/.test(name) ? name.toLowerCase() : name;

  return `
    <div class="result__nav">
      <button class="back" type="button" data-action="back">${icon("chevron-left")}${fromToday ? esc(name) : "New photo"}</button>
    </div>
    <article class="card meal-card${photoUrl ? " meal-card--photo" : ""}">
      ${photoUrl ? photoHtml(result, photoUrl) : ""}
      <div class="meal-card__body">
        ${result.id != null ? `<p class="eyebrow">Meal #${esc(result.id)}</p>` : ""}
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
      <button class="btn btn--secondary btn--block" type="button" data-action="see-today">${icon("home")}${fromToday ? `Back to ${esc(backTo)}` : "See today"}</button>
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
  const noPhoto = photoUrl ? "" : '<p class="answered">Saved meals show their foods, not the photo.</p>';
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
    ? `<p class="item__match"><span class="item__src">USDA<span class="visually-hidden">:</span></span> ${esc(match.description)}<span class="item__id">${SEP}${esc(match.data_type)} #${esc(match.fdc_id)}</span></p>`
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

const DV_NOTE = "Bars show % of the FDA Daily Value. It's a label reference, not a goal.";

function nutrientsHtml(result) {
  const macros = nutrientRowsHtml(result.totals, result.daily_value_pct, { micro: false, prefix: "n", skip: ["energy_kcal"] });
  const micros = nutrientRowsHtml(result.totals, result.daily_value_pct, { micro: true, prefix: "n" });
  if (!macros && !micros) return "";
  // Nutrient names keep their own case (Vitamin D, not "vitamin d").
  const partial = result.partial_nutrients.length
    ? `<p class="note">Some foods have no USDA data for ${esc(result.partial_nutrients.map(labelOf).join(", "))}, so those totals undercount.</p>`
    : "";
  return `
    <section class="block" aria-labelledby="nutrients-title">
      <div class="section-head">
        <h2 id="nutrients-title" class="section-title">Nutrients</h2>
        <p class="eyebrow">Bars: % Daily Value</p>
      </div>
      <div class="card nutrients-card">
        <ul class="nutrients">${macros}</ul>
        <p class="note">${DV_NOTE}</p>
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

/** A meal came back changed: update every tab that holds it, the screen if it shows it, and its day. */
function updateMeal(result, { focus } = {}) {
  for (const slot of [state.slots.analyze, state.slots.today]) {
    if (slot && result.id != null && slot.result.id === result.id) slot.result = result;
  }
  if (state.shown?.result === result) renderResult("update", { focus });
  if (result.id != null) refreshDays([mealDay(result)]);
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
      if (shownKey() === "today") {
        closeSlot("today", { focus: true });
      } else {
        setView("today");
        window.scrollTo(0, 0);
        els.todayTitle.focus({ preventScroll: true });
      }
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
    } catch (err) {
      if (form.isConnected && !err.signedOut) $(".question__error", form).textContent = err.message;
    }
  });
});

// The plate's matching box lights up with its food card under a mouse or keyboard focus. Touch skips
// it: a finger scrolling the list would otherwise flash a selection ring on every card it crosses.
els.result.addEventListener("pointerover", (event) => {
  if (event.pointerType !== "mouse" || state.open) return;
  highlight(event.target.closest(".item")?.dataset.index ?? null);
});
els.result.addEventListener("pointerleave", (event) => {
  if (event.pointerType === "mouse" && !state.open) highlight(null);
});
els.result.addEventListener("focusin", (event) => {
  if (state.open) return;
  const item = event.target.closest(".item");
  if (item && !event.target.matches(":focus-visible")) return; // a tap, not the keyboard
  highlight(item?.dataset.index ?? null);
});

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
  if (!els.account.open) document.documentElement.classList.remove("has-sheet");
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

/** Drag a bottom sheet down by its grabber or header to dismiss it, like a native sheet. */
function dragToDismiss(dialog, close) {
  let drag = null;
  dialog.addEventListener("pointerdown", (event) => {
    if (!bottomSheet.matches || event.button !== 0 || event.target.closest("button")) return;
    if (!event.target.closest(".sheet__grab, .sheet__head")) return;
    drag = { id: event.pointerId, y: event.clientY, dy: 0 };
    dialog.setPointerCapture(event.pointerId);
    dialog.style.transition = "none";
  });
  dialog.addEventListener("pointermove", (event) => {
    if (drag?.id !== event.pointerId) return;
    drag.dy = Math.max(0, event.clientY - drag.y);
    dialog.style.transform = `translateY(${drag.dy}px)`;
  });
  const end = (event) => {
    if (drag?.id !== event.pointerId) return;
    const { dy } = drag;
    drag = null;
    if (dy > 80) return close({ swiped: true });
    dialog.style.transition = "transform 240ms cubic-bezier(0.16, 1, 0.3, 1)";
    dialog.style.transform = "";
  };
  dialog.addEventListener("pointerup", end);
  dialog.addEventListener("pointercancel", end);
}

let pressedBackdrop = false;
els.sheet.addEventListener("pointerdown", (event) => {
  pressedBackdrop = event.target === els.sheet;
});
dragToDismiss(els.sheet, closeSheet);

// Keep a bottom sheet above the on-screen keyboard: --kb is the part of the screen it covers.
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
          <input id="grams-${i}" class="field" name="grams" type="number" inputmode="numeric" pattern="[0-9]*" min="1" max="10000" step="1" required placeholder="${esc(r.item.grams_likely)}" enterkeyhint="done" aria-describedby="weigh-hint">
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
      <button class="option" type="button" data-action="pick" data-fdc="${esc(food.fdc_id)}"${current ? ' aria-current="true" disabled' : ""}>
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
    } catch (err) {
      if (error?.isConnected && !err.signedOut) error.textContent = err.message;
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
    if (seq !== searchSeq || !list.isConnected || err.signedOut) return;
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

// ------------------------------------------------------------- dashboard
// Today: one day's calories, nutrients and meals, plus the last 7 days. Every day comes from
// GET /api/days/{day} and stays cached, so switching days is instant; a change to a meal marks its
// day stale and the next render fetches it again while the old numbers stay on screen.

const SLOTS = ["breakfast", "lunch", "dinner", "snack"];
const MACROS = [["protein_g", "Protein"], ["carbs_g", "Carbs"], ["fat_g", "Fat"]];
const MINIS = [["fiber_g", "Fiber"], ["sugars_g", "Total sugars"], ["sodium_mg", "Sodium"]];

const dayCache = new Map(); // "YYYY-MM-DD" -> { value, error, pending, stale, promise, version }
const rendered = { day: "", week: "" }; // what each region shows, so unchanged data isn't redrawn

function fetchDay(key) {
  let entry = dayCache.get(key);
  if (entry?.pending) return entry.promise;
  if (entry?.value && !entry.stale) return Promise.resolve(entry.value);
  if (!entry) {
    entry = { version: 0 };
    dayCache.set(key, entry);
  }
  entry.pending = true;
  entry.promise = api(`/api/days/${key}`).then(
    (view) => {
      Object.assign(entry, { value: view, error: null, stale: false, pending: false, version: entry.version + 1 });
      return view;
    },
    (err) => {
      Object.assign(entry, { error: err, pending: false, version: entry.version + 1 });
      throw err;
    }
  );
  entry.promise.catch(() => {}); // callers that need the error read it from the entry
  return entry.promise;
}

/** Mark days changed; the dashboard fetches them again and keeps showing the old numbers meanwhile. */
function refreshDays(keys) {
  for (const key of keys) {
    const entry = dayCache.get(key);
    if (entry) entry.stale = true;
  }
  loadDashboard();
}

/** Keep "today" current across midnight: a dashboard left on today moves to the new day. */
function ensureToday() {
  const today = localDate();
  if (state.todayKey === today) return false;
  const followToday = !state.day || state.day === state.todayKey;
  state.todayKey = today;
  if (followToday || state.day > today) state.day = today;
  return true;
}

function dayTitle(key) {
  const today = state.todayKey ?? localDate();
  const date = parseDay(key);
  if (key === today) return { title: "Today", sub: longDay.format(date) };
  if (key === shiftDay(today, -1)) return { title: "Yesterday", sub: longDay.format(date) };
  const sameYear = date.getFullYear() === parseDay(today).getFullYear();
  return { title: weekdayLong.format(date), sub: (sameYear ? monthDay : monthDayYear).format(date) };
}

const firstName = (user) => (user?.name ?? "").trim().split(/\s+/)[0] ?? "";

function greetingText() {
  const hour = new Date().getHours();
  const part = hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 18 ? "afternoon" : "evening";
  const name = state.authRequired ? firstName(state.user) : "";
  return name ? `Good ${part}, ${name}` : `Good ${part}`;
}

function renderDashHead() {
  const key = state.day;
  const { title, sub } = dayTitle(key);
  els.todayTitle.textContent = title;
  els.todayDate.textContent = sub;
  els.greeting.textContent = greetingText(); // text, never HTML: the name comes from the server
  const isToday = key === state.todayKey;
  const active = document.activeElement;
  els.dayNext.disabled = isToday; // never a future day
  els.dayToday.hidden = isToday;
  if (isToday && (active === els.dayNext || active === els.dayToday)) els.dayPrev.focus();
  els.dayPrev.setAttribute("aria-label", `Previous day, ${longDay.format(parseDay(shiftDay(key, -1)))}`);
  els.dayNext.setAttribute("aria-label", isToday ? "Next day" : `Next day, ${longDay.format(parseDay(shiftDay(key, 1)))}`);
}

function ensureDashboard() {
  if (ensureToday() || !rendered.day.startsWith(`${state.day}|`)) return loadDashboard();
  const keys = [state.day, ...lastDays(state.todayKey, 7)];
  if (keys.some((key) => !dayCache.get(key)?.value || dayCache.get(key).stale)) loadDashboard();
}

async function loadDashboard() {
  if (document.body.dataset.screen !== "app") return;
  ensureToday();
  const seq = ++state.dashSeq;
  const week = lastDays(state.todayKey, 7);
  const dayTask = fetchDay(state.day);
  const weekTasks = week.map((key) => fetchDay(key));
  renderDashHead();
  renderDay(); // straight from the cache: the day if it is known, else a skeleton
  renderWeek();
  await Promise.allSettled([dayTask]);
  if (seq !== state.dashSeq) return;
  renderDay();
  await Promise.allSettled(weekTasks);
  if (seq !== state.dashSeq) return;
  renderWeek();
}

function goToDay(key) {
  ensureToday();
  if (key > state.todayKey) key = state.todayKey; // never a future day
  if (key === state.day) return;
  state.day = key;
  els.dayStatus.textContent = `Showing ${longDay.format(parseDay(key))}.`;
  loadDashboard();
}

/** Redraw a region and put keyboard focus back on the same control (a meal row, a day) if it had it. */
function redraw(region, html, key) {
  const focused = document.activeElement?.closest?.(`[${key}]`);
  const value = focused && region.contains(focused) ? focused.getAttribute(key) : null;
  region.innerHTML = html;
  if (value != null) $(`[${key}="${CSS.escape(value)}"]`, region)?.focus({ preventScroll: true });
}

function renderDay() {
  const key = state.day;
  const entry = dayCache.get(key);
  const view = entry?.value ?? null;
  const failed = Boolean(entry?.error) && !entry?.pending;
  els.today.setAttribute("aria-busy", String(Boolean(entry?.pending)));
  els.today.dataset.state = view ? (view.meals.length ? "ready" : "empty") : failed ? "error" : "loading";
  // A refresh that failed keeps the last numbers and says so above them.
  els.dashNotice.innerHTML =
    view && failed
      ? `<div class="dash-alert"><p class="status is-error" role="alert">Couldn't refresh this day. ${esc(entry.error.message)}</p><button class="btn btn--soft" type="button" data-action="retry">Try again</button></div>`
      : "";
  const sig = `${key}|${entry?.version ?? 0}|${view ? "v" : failed ? "e" : "l"}|${state.nutrients.length}`;
  if (sig === rendered.day) return;
  rendered.day = sig;
  if (!view) {
    els.dashDay.innerHTML = failed ? dayErrorHtml(entry.error) : daySkeletonHtml();
    els.dashMicro.innerHTML = "";
    return;
  }
  const previous = new Map([...snapshotBands(els.dashDay), ...snapshotBands(els.dashMicro)]);
  if (view.meals.length) {
    redraw(els.dashDay, heroHtml(view) + statsHtml(view) + mealsHtml(view), "data-meal");
    els.dashMicro.innerHTML = microHtml(view);
  } else {
    els.dashDay.innerHTML = emptyDayHtml(key);
    els.dashMicro.innerHTML = "";
  }
  animateBands(els.dashDay, previous);
  animateBands(els.dashMicro, previous);
  markOpenMeal();
}

function mealCount(n) {
  return `${n} meal${n === 1 ? "" : "s"}`;
}

function heroHtml(view) {
  const kcal = view.totals.energy_kcal;
  const head = `
    <div class="hero__top">
      <h2 id="hero-title" class="eyebrow">Total calories</h2>
      <p class="eyebrow">${mealCount(view.meals.length)}</p>
    </div>`;
  if (!kcal) {
    return `
      <section class="card hero" aria-labelledby="hero-title">
        ${head}
        <p class="hero__none">Nothing is counted yet. Open a meal and pick a USDA food for each item.</p>
      </section>`;
  }
  const [low, likely, high] = [amount(kcal.low, "kcal"), amount(kcal.likely, "kcal"), amount(kcal.high, "kcal")];
  const exact = low === high;
  return `
    <section class="card hero" aria-labelledby="hero-title">
      ${head}
      <div class="hero__main">
        <p class="hero__value"><span class="hero__num">${likely}</span><span class="hero__unit">kcal</span>${exact ? "" : '<span class="visually-hidden">, most likely</span>'}</p>
        <p class="hero__range">${
          exact
            ? "Every counted food is weighed, so this is one number."
            : `Most likely, in a range of <strong>${low}${TO}${high}&nbsp;kcal</strong>`
        }</p>
      </div>
      <div class="hero__foot">
        ${bandHtml("dash-kcal", kcal, { dark: true })}
        <p class="hero__note">${icon("info")}<span>A range, not a target. There's no goal to hit.</span></p>
      </div>
    </section>`;
}

function statHtml(view, key, label, size) {
  const range = view.totals[key];
  const pct = view.daily_value_pct[key];
  const unit = unitOf(key);
  const fmt = range ? tileFormat(range) : null;
  const figure = range
    ? `<p class="stat__figure">
         <span class="stat__value"><strong>${fmt(range.likely)}</strong><span class="stat__unit">${esc(unit)}</span>${isExact(range) ? "" : '<span class="visually-hidden">, most likely</span>'}</span>
         <span class="stat__range">${isExact(range) ? "Weighed" : `<span class="visually-hidden">range </span>${fmt(range.low)}${TO}${fmt(range.high)}`}</span>
       </p>`
    : '<p class="stat__figure"><span class="stat__none">No data</span></p>';
  const dv =
    range && pct
      ? `${bandHtml(`dash-${key}`, pct, { scale: Math.max(100, pct.high * 1.05) })}<p class="stat__dv">${SEP}${dvHtml(pct)}</p>`
      : `<p class="stat__dv stat__dv--none">${SEP}No Daily Value</p>`;
  return `<li class="card stat stat--${size}"><p class="stat__label">${esc(label)}</p>${figure}${dv}</li>`;
}

function statsHtml(view) {
  return `
    <section class="stats-block" aria-labelledby="stats-title">
      <h2 id="stats-title" class="visually-hidden">Nutrients</h2>
      <ul class="stats stats--macro">${MACROS.map(([key, label]) => statHtml(view, key, label, "macro")).join("")}</ul>
      <ul class="stats stats--mini">${MINIS.map(([key, label]) => statHtml(view, key, label, "mini")).join("")}</ul>
      <p class="note stats__note">${DV_NOTE}</p>
    </section>`;
}

function mealRowHtml(m) {
  const kcal = m.totals.energy_kcal;
  const place = placeName(m.context);
  const names = m.items.map((r) => r.item.name).join(", ");
  return `
    <li>
      <button class="meal" type="button" data-meal="${esc(m.id)}">
        <span class="meal__when"><span class="visually-hidden">${esc(capitalize(m.context.meal_slot))}, </span><span class="meal__time">${timeFormat.format(wallClock(m.context.taken_at))}</span>${
          place ? `<span class="meal__place"><span aria-hidden="true"> · </span><span class="visually-hidden">, </span>${esc(place)}</span>` : ""
        }</span>
        <span class="meal__names">${SEP}${names ? esc(names) : "No foods found"}</span>
        <span class="meal__kcal">${SEP}${kcal ? rangeHtml(kcal, "kcal", undefined, { stack: true }) : '<span class="value__none">Not counted</span>'}</span>
        <span class="meal__chev" aria-hidden="true">${icon("chevron-right")}</span>
      </button>
    </li>`;
}

function mealsHtml(view) {
  const known = new Set(SLOTS);
  const slots = [...SLOTS, ...new Set(view.meals.map((m) => m.context.meal_slot).filter((s) => !known.has(s)))];
  const groups = slots
    .map((slot) => [slot, view.meals.filter((m) => m.context.meal_slot === slot)])
    .filter(([, meals]) => meals.length)
    .map(
      ([slot, meals], i) => `
        <section class="meal-group" aria-labelledby="slot-${i}">
          <h3 id="slot-${i}" class="meal-group__title">${esc(capitalize(slot))}</h3>
          <ol class="meals">${meals.map(mealRowHtml).join("")}</ol>
        </section>`
    )
    .join("");
  return `
    <section class="block meals-block" aria-labelledby="meals-title">
      <div class="section-head">
        <h2 id="meals-title" class="section-title">Meals</h2>
        <p class="eyebrow">${mealCount(view.meals.length)}</p>
      </div>
      <div class="card meals-card">${groups}</div>
    </section>`;
}

function microHtml(view) {
  const rows = nutrientRowsHtml(view.totals, view.daily_value_pct, { micro: true, prefix: "dm" });
  if (!rows) return "";
  return `
    <details class="card micro-card"${state.microOpen ? " open" : ""}>
      <summary><h2 class="micro-card__title">Vitamins and minerals</h2><span class="chip">Estimates</span></summary>
      <div class="micro-card__body">
        <p class="note">Photos can't show vitamins and minerals well, so these are rough estimates. A weekly trend says more than one day.</p>
        <ul class="nutrients">${rows}</ul>
        <p class="note">${DV_NOTE}</p>
      </div>
    </details>`;
}

function emptyDayHtml(key) {
  const isToday = key === state.todayKey;
  const name = dayName(key);
  const when = isToday ? "today" : name === "Yesterday" ? "yesterday" : `on ${name}`;
  return `
    <div class="card empty dash-empty">
      <span class="empty__icon" aria-hidden="true">${icon("plate")}</span>
      <h2 class="empty__title">Nothing logged ${esc(when)}</h2>
      <p>${
        isToday
          ? "Snap a meal and it lands here with its ranges. No targets, just what you ate."
          : "Meals with photos from this day show up here."
      }</p>
      ${
        isToday
          ? `<button class="btn btn--primary" type="button" data-action="snap"${state.ready ? "" : " disabled"}>${icon("camera")}Snap a meal</button>`
          : ""
      }
    </div>`;
}

function dayErrorHtml(err) {
  return `
    <div class="card dash-error">
      <p class="status is-error" role="alert">${esc(err.message)}</p>
      <button class="btn btn--secondary" type="button" data-action="retry">Try again</button>
    </div>`;
}

function daySkeletonHtml() {
  const tile = (size) =>
    `<li class="card stat stat--${size} is-skeleton"><span class="sk sk--label"></span><span class="sk sk--num"></span><span class="sk sk--band"></span></li>`;
  return `
    <div class="card hero is-skeleton" aria-hidden="true"><span class="sk sk--eyebrow"></span><span class="sk sk--hero"></span><span class="sk sk--line"></span><div class="hero__foot"><span class="sk sk--band"></span></div></div>
    <div class="stats-block" aria-hidden="true"><ul class="stats">${tile("macro").repeat(3)}</ul><ul class="stats">${tile("mini").repeat(3)}</ul></div>
    <div class="block meals-block" aria-hidden="true"><span class="sk sk--title"></span><div class="card meals-card is-skeleton">${'<span class="sk-row"><span class="sk sk--line"></span><span class="sk sk--short"></span></span>'.repeat(3)}</div></div>
    <p class="visually-hidden" role="status">Loading this day</p>`;
}

// ---- the week

function niceTop(value) {
  const step = value <= 1000 ? 250 : value <= 4000 ? 500 : 1000;
  return Math.max(step, Math.ceil((value * 1.06) / step) * step);
}

function renderWeek() {
  const keys = lastDays(state.todayKey, 7);
  const entries = keys.map((key) => dayCache.get(key));
  const sig = `${state.day}|${keys.map((key, i) => `${key}:${entries[i]?.version ?? 0}:${entries[i]?.value ? 1 : 0}`).join(",")}`;
  if (sig === rendered.week) return;
  const entering = !rendered.week || els.dashWeek.dataset.state !== "ready";
  rendered.week = sig;
  const loaded = entries.some((entry) => entry?.value);
  els.dashWeek.dataset.state = loaded ? "ready" : "loading";
  redraw(els.dashWeek, loaded ? weekHtml(keys, entries) : weekSkeletonHtml(), "data-day");
  els.dashWeek.classList.toggle("is-entering", loaded && entering && !reducedMotion.matches);
}

function weekHead() {
  return `
    <div class="section-head">
      <h2 id="week-title" class="section-title">This week</h2>
      <p class="eyebrow">Likely kcal and range</p>
    </div>`;
}

function weekSkeletonHtml() {
  const bars = [0.55, 0.7, 0.35, 0.8, 0.6, 0.45, 0.65]
    .map((h) => `<li class="wk__day"><span class="wk__col is-skeleton"><span class="wk__plot"><span class="sk sk--bar" style="--h:${h}"></span></span><span class="sk sk--tick"></span></span></li>`)
    .join("");
  return `${weekHead()}<div class="card week" aria-hidden="true"><ol class="wk">${bars}</ol></div>`;
}

function weekHtml(keys, entries) {
  const days = keys.map((key, i) => {
    const view = entries[i]?.value ?? null;
    const kcal = view?.meals.length ? view.totals.energy_kcal ?? null : null;
    return { key, view, kcal, logged: Boolean(view?.meals.length), failed: !view && Boolean(entries[i]?.error), pending: !view && !entries[i]?.error };
  });
  const counted = days.filter((d) => d.kcal);
  const top = niceTop(Math.max(1, ...counted.map((d) => d.kcal.high)));
  const cols = days.map((d, i) => weekColHtml(d, top, i)).join("");
  let avg = '<p class="wk__avg-label">No meals logged in the last 7 days.</p>';
  if (counted.length) {
    // Only days with logged meals count; an empty day is missing data, not a zero.
    const mean = (part) => counted.reduce((sum, d) => sum + d.kcal[part], 0) / counted.length;
    const n = counted.length;
    avg = `
      <p class="wk__avg-label">Average over ${n} logged day${n === 1 ? "" : "s"}</p>
      <p class="wk__avg-value">${rangeHtml({ low: mean("low"), likely: mean("likely"), high: mean("high") }, "kcal")}</p>`;
  }
  return `
    ${weekHead()}
    <div class="card week">
      <ol class="wk">${cols}</ol>
      <div class="wk__avg">${avg}</div>
      <div class="wk__tip" aria-hidden="true" hidden></div>
    </div>`;
}

function weekColHtml(d, top, i) {
  const date = parseDay(d.key);
  const isToday = d.key === state.todayKey;
  const name = isToday ? "Today" : weekdayShort.format(date);
  const spoken = `${isToday ? "Today, " : ""}${dayFormat.format(date)}`;
  const current = d.key === state.day ? ' aria-current="date"' : "";
  let plot = '<span class="wk__plot"></span>';
  let short = "";
  let full = "";
  let label = `${spoken}: no meals logged`;
  let tip = "No meals logged";
  if (d.kcal) {
    const f = (v) => Math.max(0, Math.min(1, v / top)).toFixed(4);
    const [low, likely, high] = [amount(d.kcal.low, "kcal"), amount(d.kcal.likely, "kcal"), amount(d.kcal.high, "kcal")];
    const exact = low === high;
    plot = `<span class="wk__plot" style="--lo:${f(d.kcal.low)};--likely:${f(d.kcal.likely)};--hi:${f(d.kcal.high)};--i:${i}"><span class="wk__bar"></span>${
      exact ? "" : '<span class="wk__whisker wk__whisker--lo"></span><span class="wk__whisker wk__whisker--hi"></span>'
    }</span>`;
    short = compactKcal(d.kcal.likely);
    full = `${likely} kcal`;
    label = exact ? `${spoken}: ${likely} kcal` : `${spoken}: ${likely} kcal likely, range ${low} to ${high}`;
    tip = exact ? `${likely} kcal` : `${likely} kcal|${low}–${high}`;
  } else if (d.logged) {
    label = `${spoken}: meals logged, calories not counted`;
    tip = "Calories not counted";
    full = "Not counted";
  } else if (d.failed) {
    label = `${spoken}: couldn't load`;
    tip = "Couldn't load this day";
  } else if (d.pending) {
    label = `${spoken}: loading`;
    tip = "Loading";
  }
  const cls = `wk__col${isToday ? " is-today" : ""}${d.kcal ? "" : " is-empty"}${d.pending ? " is-pending" : ""}`;
  return `
    <li class="wk__day">
      <button class="${cls}" type="button" data-day="${d.key}"${current} aria-label="${esc(label)}" data-tip-day="${esc(dayFormat.format(date))}" data-tip="${esc(tip)}">
        ${plot}
        <span class="wk__name" aria-hidden="true">${name}</span>
        <span class="wk__val" aria-hidden="true"><span class="wk__val--short">${short}</span><span class="wk__val--full">${esc(full)}</span></span>
      </button>
    </li>`;
}

// A tooltip with the full numbers for the column under the mouse or keyboard focus. Every number
// in it is also in the column's accessible name; on touch, a tap opens that day instead.
function showWeekTip(col) {
  const card = col.closest(".week");
  const tip = card && $(".wk__tip", card);
  if (!tip) return;
  const [value, range] = col.dataset.tip.split("|");
  tip.replaceChildren();
  const day = document.createElement("span");
  day.className = "wk__tip-day";
  day.textContent = col.dataset.tipDay;
  const strong = document.createElement("strong");
  strong.textContent = value;
  tip.append(day, strong);
  if (range) {
    const span = document.createElement("span");
    span.className = "wk__tip-range";
    span.textContent = range;
    tip.append(span);
  }
  tip.hidden = false;
  const plot = $(".wk__plot", col).getBoundingClientRect();
  const box = card.getBoundingClientRect();
  const half = tip.offsetWidth / 2;
  const x = Math.min(Math.max(plot.left + plot.width / 2 - box.left, half + 6), box.width - half - 6);
  tip.style.left = `${x}px`;
  tip.style.top = `${plot.top - box.top - 6}px`;
}

function hideWeekTip() {
  for (const tip of $$(".wk__tip", els.dashWeek)) tip.hidden = true;
}

els.dashWeek.addEventListener("pointerover", (event) => {
  const col = event.target.closest(".wk__col[data-tip]");
  if (event.pointerType === "mouse" && col) showWeekTip(col);
});
els.dashWeek.addEventListener("pointerout", (event) => {
  if (event.pointerType === "mouse" && !event.relatedTarget?.closest?.(".wk__col")) hideWeekTip();
});
els.dashWeek.addEventListener("focusin", (event) => {
  const col = event.target.closest(".wk__col[data-tip]");
  if (col && col.matches(":focus-visible")) showWeekTip(col);
});
els.dashWeek.addEventListener("focusout", hideWeekTip);

// ---- dashboard events

els.todayView.addEventListener("click", (event) => {
  const target = event.target;
  const day = target.closest("[data-day]");
  if (day) {
    const which = day.dataset.day;
    if (which === "prev") goToDay(shiftDay(state.day, -1));
    else if (which === "next") goToDay(shiftDay(state.day, 1));
    else if (which === "today") goToDay(state.todayKey);
    else goToDay(which);
    return;
  }
  const action = target.closest("[data-action]");
  switch (action?.dataset.action) {
    case "snap":
    case "start":
      return snapMeal();
    case "retry":
      for (const entry of dayCache.values()) if (entry.error) entry.stale = true;
      rendered.day = "";
      return loadDashboard();
    case "resend":
      return resendLink(action);
    case "demo-verify":
      return demoVerify(action);
  }
  const meal = target.closest("[data-meal]");
  if (meal) openMealFrom(meal);
});

els.dashMicro.addEventListener("toggle", (event) => (state.microOpen = event.target.open), true);

function clearMealError() {
  $("[data-meal-error]", els.today)?.remove();
}

async function openMealFrom(button) {
  const id = button.dataset.meal;
  clearMealError();
  state.scroll.list = window.scrollY;
  state.openedMeal = id;
  await withBusy(button, async () => {
    try {
      openLogged(await api(`/api/meals/${encodeURIComponent(id)}`));
    } catch (err) {
      if (err.signedOut) return;
      // One alert at a time, right under the meal that was tapped.
      clearMealError();
      const alert = document.createElement("p");
      alert.className = "status is-error meal-error";
      alert.setAttribute("role", "alert");
      alert.dataset.mealError = "";
      alert.textContent = err.message;
      (button.closest("li") ?? button.parentElement).append(alert);
      alert.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
    }
  });
}

/** A logged meal opens over the dashboard. */
function openLogged(result) {
  discardSlot("today");
  state.slots.today = { result, photoUrl: null };
  syncResult({ enter: "today" });
  syncHistory();
}

// -------------------------------------------------------------- accounts
// Only when GET /api/status says auth_required: true. The session is an HttpOnly cookie, so the
// page never sees a token; GET /api/auth/me says who is signed in.

/** Emails go out for real only with a sending provider; the default outbox just queues them. */
const emailSending = () => Boolean(state.emailProvider) && state.emailProvider !== "outbox";

function enableAccounts(status) {
  state.authRequired = true;
  state.emailProvider = status.email_provider ?? null;
  state.home = "today";
  document.body.dataset.accounts = "on";
}

let recheck = null;

/** A 401 without accounts on: the server may have just switched them on. */
function signInLost() {
  if (state.authRequired) return sessionEnded();
  recheck ??= api("/api/status", { session: false })
    .then((status) => {
      if (status?.auth_required !== true) return;
      enableAccounts(status);
      sessionEnded({ notice: "MealScholar now uses accounts. Sign in or create one to continue." });
    })
    .catch(() => {})
    .finally(() => (recheck = null));
}

function initials(user) {
  const letter = (s) => s.match(/[\p{L}\p{N}]/u)?.[0] ?? "";
  const words = (user?.name ?? "").trim().split(/\s+/).filter(Boolean);
  let out = words.length ? letter(words[0]) + (words.length > 1 ? letter(words[words.length - 1]) : "") : "";
  if (!out) out = letter(user?.email ?? "") || "?";
  return out.toUpperCase();
}

function updateAccountUi() {
  const user = state.authRequired ? state.user : null;
  els.accountButton.hidden = !user;
  document.body.dataset.account = user ? "on" : "off";
  if (!user) return;
  els.accountButton.textContent = initials(user);
  els.accountButton.setAttribute("aria-label", `Account, ${user.name || user.email}`);
}

/** Leave the app for one of the account screens. */
function showAuth(screen, options = {}) {
  if (els.sheet.open) closeSheet({ restoreFocus: false });
  if (els.account.open) els.account.close();
  document.body.dataset.screen = "auth";
  els.auth.hidden = false;
  els.auth.dataset.screen = screen;
  els.auth.innerHTML = AUTH_SCREENS[screen](options);
  placeBands(els.auth);
  window.scrollTo(0, 0);
  replayEntrance(els.auth);
  const field = finePointer.matches ? $("input:not([value]), input[value='']", els.auth) : null;
  (field ?? $("h1", els.auth))?.focus({ preventScroll: true });
}

/** Into the app: the home screen, with this account's data. */
function enterApp({ focus = false } = {}) {
  document.body.dataset.screen = "app";
  els.auth.hidden = true;
  els.auth.innerHTML = "";
  updateAccountUi();
  renderBanner();
  state.view = state.home;
  document.body.dataset.view = state.home;
  syncTabs();
  syncResult();
  history.replaceState({ layers: [] }, "", baseUrl);
  window.scrollTo(0, 0);
  if (!state.nutrients.length) {
    loadNutrients().then(() => {
      rendered.day = "";
      renderDay();
    });
  }
  loadDashboard();
  if (focus) (state.home === "today" ? els.todayTitle : els.captureTitle).focus({ preventScroll: true });
}

/** Forget everything that belongs to the account that was signed in. */
function resetData() {
  state.dashSeq += 1;
  dayCache.clear();
  rendered.day = "";
  rendered.week = "";
  if (els.sheet.open) closeSheet({ restoreFocus: false });
  discardSlot("analyze");
  discardSlot("today");
  state.shown = null;
  state.open = null;
  state.day = null;
  state.todayKey = null;
  state.microOpen = false;
  state.scroll = { analyze: 0, today: 0, list: 0 };
  els.result.innerHTML = "";
  els.result.hidden = true;
  delete document.body.dataset.result;
  els.dashDay.innerHTML = "";
  els.dashWeek.innerHTML = "";
  els.dashMicro.innerHTML = "";
  els.dashNotice.innerHTML = "";
  els.today.dataset.state = "loading";
  els.dashWeek.dataset.state = "loading";
  if (!state.analyzing) setStatus("");
}

function signedIn(user, message) {
  resetData();
  state.user = user;
  enterApp({ focus: true });
  if (message) toast(message);
}

/** The sign-in ended (a 401 from a meal or day call): back to the sign-in screen. */
function sessionEnded({ notice = "Your session ended. Sign in again to continue." } = {}) {
  if (document.body.dataset.screen === "auth") return;
  const email = state.user?.email ?? "";
  resetData();
  state.user = null;
  state.demoVerifyUrl = null;
  updateAccountUi();
  renderBanner();
  history.replaceState({ layers: [] }, "", baseUrl);
  showAuth("signin", { email, notice });
}

async function loadNutrients() {
  try {
    state.nutrients = await api("/api/nutrients", { session: false });
  } catch {
    // Labels for the nutrient rows; the next sign-in tries again.
  }
}

async function refreshUser() {
  try {
    const { user } = await api("/api/auth/me", { session: true });
    state.user = user;
    updateAccountUi();
  } catch {
    // signed out (handled by api) or offline: keep what we have
  }
}

// ---- screens

const backHtml = (to, label) => `<div class="result__nav"><button class="back" type="button" data-auth="${to}">${icon("chevron-left")}${label}</button></div>`;

function fieldHtml({ id, name, label, type = "text", autocomplete, value = "", hint = "", optional = false }) {
  const describedby = [hint && `${id}-hint`, `${id}-error`].filter(Boolean).join(" ");
  const email = type === "email" ? ' autocapitalize="none" spellcheck="false"' : "";
  return `
    <div class="form-field">
      <label class="form-field__label" for="${id}">${label}${optional ? ' <span class="form-field__optional">Optional</span>' : ""}</label>
      <input id="${id}" class="field" name="${name}" type="${type}" autocomplete="${autocomplete}" value="${esc(value)}" aria-describedby="${describedby}"${email}>
      ${hint ? `<p id="${id}-hint" class="form-field__hint">${hint}</p>` : ""}
      <p id="${id}-error" class="field-error" hidden></p>
    </div>`;
}

function passwordFieldHtml({ id, name, label, autocomplete, hint = "" }) {
  const describedby = [hint && `${id}-hint`, `${id}-error`].filter(Boolean).join(" ");
  return `
    <div class="form-field">
      <label class="form-field__label" for="${id}">${label}</label>
      <span class="pw">
        <input id="${id}" class="field" name="${name}" type="password" autocomplete="${autocomplete}" autocapitalize="none" spellcheck="false" aria-describedby="${describedby}">
        <button class="pw__toggle" type="button" data-action="toggle-password" aria-controls="${id}"><span class="pw__label">Show</span><span class="visually-hidden"> password</span></button>
      </span>
      ${hint ? `<p id="${id}-hint" class="form-field__hint">${hint}</p>` : ""}
      <p id="${id}-error" class="field-error" hidden></p>
    </div>`;
}

const EMAIL_NOTE = `<p class="auth-note">${icon("info")}<span>Email sending isn't switched on yet, so the link may not arrive.</span></p>`;

function sampleHtml() {
  return `
    <div class="card welcome__sample" aria-hidden="true">
      <p class="eyebrow">Example</p>
      <p class="welcome__sample-title">Lunch</p>
      <div class="total">
        <p class="eyebrow">Calories, this meal</p>
        <p class="total__value"><span>407</span><span class="total__dash">–</span><span>655</span><span class="total__unit">kcal</span></p>
        ${bandHtml("sample-kcal", { low: 407, likely: 520, high: 655 }, { dark: true })}
        <p class="total__likely">Most likely <strong>520&nbsp;kcal</strong>.</p>
      </div>
      <ul class="welcome__foods">
        <li><span>Chicken fried rice</span>${rangeHtml({ low: 220, likely: 280, high: 350 }, "g", grams)}</li>
        <li><span>Steamed broccoli</span>${rangeHtml({ low: 70, likely: 95, high: 130 }, "g", grams)}</li>
      </ul>
    </div>`;
}

const AUTH_SCREENS = {
  welcome: () => `
    <div class="auth-screen auth-screen--welcome">
      <header class="view-head welcome__head">
        <p class="eyebrow">Meal photo <span aria-hidden="true">&rarr;</span><span class="visually-hidden">to</span> foods <span aria-hidden="true">&rarr;</span><span class="visually-hidden">to</span> USDA nutrients</p>
        <h1 class="view-title" tabindex="-1">Every meal is a range</h1>
        <p class="lede">Snap a photo of your meal. It works out the foods and how much is there, then shows the nutrients as honest ranges, not one fake-precise number.</p>
      </header>
      ${sampleHtml()}
      <div class="welcome__actions">
        <button class="cta cta--block" type="button" data-auth="register"><span class="cta__pill">Create account</span><span class="cta__dot" aria-hidden="true">${icon("arrow")}</span></button>
        <button class="btn btn--secondary btn--block" type="button" data-auth="signin">Sign in</button>
        <p class="welcome__fine">Your meals go into your own diary. No targets, just what you ate.</p>
      </div>
    </div>`,

  signin: ({ email = "", notice = "" } = {}) => `
    <div class="auth-screen">
      ${backHtml("welcome", "Back")}
      <header class="auth-head">
        <h1 class="view-title" tabindex="-1">Sign in</h1>
        <p class="lede">Welcome back. Your diary is where you left it.</p>
      </header>
      ${notice ? `<p class="notice" role="status">${icon("info")}<span>${esc(notice)}</span></p>` : ""}
      <form class="card form" data-form="signin" novalidate>
        ${fieldHtml({ id: "signin-email", name: "email", label: "Email", type: "email", autocomplete: "email", value: email })}
        ${passwordFieldHtml({ id: "signin-password", name: "password", label: "Password", autocomplete: "current-password" })}
        <p class="form__aside"><button class="link-btn" type="button" data-auth="forgot">Forgot password?</button></p>
        <p class="form-error" role="alert" hidden></p>
        <button class="btn btn--primary btn--block" type="submit">Sign in</button>
      </form>
      <p class="auth-switch">New to MealScholar? <button class="link-btn" type="button" data-auth="register">Create an account</button></p>
    </div>`,

  register: ({ email = "" } = {}) => `
    <div class="auth-screen">
      ${backHtml("welcome", "Back")}
      <header class="auth-head">
        <h1 class="view-title" tabindex="-1">Create your account</h1>
        <p class="lede">Your meals go into your own private diary.</p>
      </header>
      <form class="card form" data-form="register" novalidate>
        ${fieldHtml({ id: "register-name", name: "name", label: "Name", autocomplete: "name", optional: true })}
        ${fieldHtml({ id: "register-email", name: "email", label: "Email", type: "email", autocomplete: "email", value: email })}
        ${passwordFieldHtml({ id: "register-password", name: "password", label: "Password", autocomplete: "new-password", hint: "At least 8 characters." })}
        <p class="form-error" role="alert" hidden></p>
        <button class="btn btn--primary btn--block" type="submit">Create account</button>
      </form>
      <p class="auth-switch">Already have an account? <button class="link-btn" type="button" data-auth="signin">Sign in</button></p>
    </div>`,

  forgot: ({ email = "", sent = false } = {}) => {
    const note = emailSending() ? "" : EMAIL_NOTE;
    if (sent) {
      return `
        <div class="auth-screen">
          ${backHtml("signin", "Sign in")}
          <header class="auth-head"><h1 class="view-title" tabindex="-1">Check your email</h1></header>
          <div class="card sent" role="status">
            <span class="sent__icon" aria-hidden="true">${icon("mail")}</span>
            <p>If there's an account for that email, we sent a reset link.</p>
          </div>
          ${note}
          <button class="btn btn--secondary btn--block" type="button" data-auth="signin">Back to sign in</button>
        </div>`;
    }
    return `
      <div class="auth-screen">
        ${backHtml("signin", "Sign in")}
        <header class="auth-head">
          <h1 class="view-title" tabindex="-1">Reset your password</h1>
          <p class="lede">Enter the email for your account. We'll send a link to set a new password.</p>
        </header>
        <form class="card form" data-form="forgot" novalidate>
          ${fieldHtml({ id: "forgot-email", name: "email", label: "Email", type: "email", autocomplete: "email", value: email })}
          <p class="form-error" role="alert" hidden></p>
          <button class="btn btn--primary btn--block" type="submit">Send reset link</button>
        </form>
        ${note}
      </div>`;
  },

  reset: () => `
    <div class="auth-screen">
      <header class="auth-head">
        <h1 class="view-title" tabindex="-1">Set a new password</h1>
        <p class="lede">Choose a new password. You'll be signed in right after.</p>
      </header>
      <form class="card form" data-form="reset" novalidate>
        ${passwordFieldHtml({ id: "reset-password", name: "password", label: "New password", autocomplete: "new-password", hint: "At least 8 characters." })}
        <p class="form-error" role="alert" hidden></p>
        <button class="btn btn--primary btn--block" type="submit">Save new password</button>
      </form>
      <p class="auth-switch"><button class="link-btn" type="button" data-auth="${state.user ? "app" : "signin"}">${state.user ? "Keep my current password" : "Back to sign in"}</button></p>
    </div>`,

  verify: ({ phase = "pending", message = "" } = {}) => {
    if (phase === "pending") {
      return `
        <div class="auth-screen">
          <header class="auth-head">
            <h1 class="view-title" tabindex="-1">Confirming your email</h1>
            <p class="lede auth-wait"><span class="spinner" aria-hidden="true"></span>One moment.</p>
          </header>
        </div>`;
    }
    const next = state.user ? "app" : "signin";
    const label = state.user ? "Continue" : "Sign in";
    if (phase === "done") {
      return `
        <div class="auth-screen">
          <header class="auth-head">
            <span class="auth-badge" aria-hidden="true">${icon("check")}</span>
            <h1 class="view-title" tabindex="-1">Email confirmed</h1>
            <p class="lede">Thanks. Your account is all set.</p>
          </header>
          <button class="cta cta--block" type="button" data-auth="${next}"><span class="cta__pill">${label}</span><span class="cta__dot" aria-hidden="true">${icon("arrow")}</span></button>
        </div>`;
    }
    return `
      <div class="auth-screen">
        <header class="auth-head">
          <span class="auth-badge" aria-hidden="true">${icon("mail")}</span>
          <h1 class="view-title" tabindex="-1">That link didn't work</h1>
          <p class="lede">You can ask for a new one after you sign in.</p>
        </header>
        <p class="status is-error" role="alert">${esc(message)}</p>
        <button class="btn btn--primary btn--block" type="button" data-auth="${next}">${label}</button>
      </div>`;
  },
};

// ---- forms

function setFieldError(input, message) {
  const error = document.getElementById(`${input.id}-error`);
  input.setAttribute("aria-invalid", "true");
  if (error) {
    error.textContent = message;
    error.hidden = false;
  }
}

function clearFieldError(input) {
  if (!(input instanceof HTMLInputElement) || input.getAttribute("aria-invalid") !== "true") return;
  input.removeAttribute("aria-invalid");
  const error = document.getElementById(`${input.id}-error`);
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
}

function setFormAlert(form, message, action = null) {
  const alert = $(".form-error", form);
  const text = document.createElement("span");
  text.className = "form-error__text";
  text.textContent = message;
  alert.replaceChildren(text);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-btn";
    button.dataset.auth = action.to;
    button.textContent = action.label;
    alert.append(button);
  }
  alert.hidden = false;
}

function clearFormErrors(form) {
  $$('[aria-invalid="true"]', form).forEach(clearFieldError);
  const alert = $(".form-error", form);
  if (alert) {
    alert.hidden = true;
    alert.textContent = "";
  }
}

/** A server error goes next to its field when it names one (detail.field), else above the button. */
function showFormError(form, err) {
  if (err.signedOut || !form.isConnected) return;
  const input = err.field ? form.elements.namedItem(err.field) : null;
  if (input instanceof HTMLInputElement && !input.classList.contains("visually-hidden")) {
    setFieldError(input, err.message);
    input.focus();
    return;
  }
  setFormAlert(form, err.message, err.field === "token" ? { to: "forgot", label: "Get a new link" } : null);
}

const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Quick checks before a round trip; the server checks again and has the last word. */
function checkForm(form, kind, data) {
  const problems = [];
  if ("email" in data && kind !== "change-password") {
    const email = data.email.trim();
    if (!email) problems.push(["email", "Enter your email address."]);
    else if (!EMAIL_SHAPE.test(email)) problems.push(["email", "Enter a valid email address."]);
  }
  if (kind === "signin" && !data.password) problems.push(["password", "Enter your password."]);
  if ((kind === "register" || kind === "reset") && [...data.password].length < 8) {
    problems.push(["password", "Use at least 8 characters for your password."]);
  }
  if (kind === "change-password") {
    if (!data.current_password) problems.push(["current_password", "Enter your current password."]);
    if ([...data.new_password].length < 8) problems.push(["new_password", "Use at least 8 characters for your password."]);
  }
  for (const [name, message] of problems) setFieldError(form.elements.namedItem(name), message);
  if (problems.length) form.elements.namedItem(problems[0][0]).focus();
  return !problems.length;
}

async function submitAccountForm(form) {
  const kind = form.dataset.form;
  const data = Object.fromEntries(new FormData(form));
  clearFormErrors(form);
  if (!checkForm(form, kind, data)) return;
  const button = $('button[type="submit"]', form);
  const post = (path, body, options = {}) => api(path, { method: "POST", ...jsonBody(body), ...options });
  await withBusy(button, async () => {
    try {
      if (kind === "signin") {
        const { user } = await post("/api/auth/login", { email: data.email.trim(), password: data.password });
        const name = firstName(user);
        signedIn(user, name ? `Welcome back, ${name}.` : "Welcome back.");
      } else if (kind === "register") {
        const name = data.name.trim();
        const reply = await post("/api/auth/register", { email: data.email.trim(), password: data.password, ...(name ? { name } : {}) });
        state.demoVerifyUrl = reply.demo_verify_url ?? null;
        signedIn(reply.user, "Account created.");
      } else if (kind === "forgot") {
        await post("/api/auth/forgot-password", { email: data.email.trim() });
        showAuth("forgot", { email: data.email.trim(), sent: true });
      } else if (kind === "reset") {
        const { user } = await post("/api/auth/reset-password", { token: state.resetToken ?? "", password: data.password });
        state.resetToken = null;
        signedIn(user, "New password saved. You're signed in.");
      } else if (kind === "change-password") {
        const body = { current_password: data.current_password, new_password: data.new_password };
        const { user } = await post("/api/auth/change-password", body, { session: true });
        if (user) state.user = user;
        closeAccount({ message: "Password changed." });
      }
    } catch (err) {
      showFormError(form, err);
    }
  });
}

function togglePassword(button) {
  const input = document.getElementById(button.getAttribute("aria-controls"));
  if (!input) return;
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  $(".pw__label", button).textContent = show ? "Hide" : "Show";
}

els.auth.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-auth]");
  if (nav) {
    if (nav.dataset.auth === "app") return enterApp({ focus: true });
    const email = $("input[name=email]", els.auth)?.value.trim() ?? "";
    return showAuth(nav.dataset.auth, { email });
  }
  const toggle = event.target.closest('[data-action="toggle-password"]');
  if (toggle) togglePassword(toggle);
});

els.auth.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAccountForm(event.target);
});

els.auth.addEventListener("input", (event) => clearFieldError(event.target));

/** ?verify=TOKEN: confirm, then carry on into the app when already signed in. */
async function confirmEmail(token) {
  showAuth("verify", { phase: "pending" });
  try {
    const { user } = await api("/api/auth/verify-email", { method: "POST", ...jsonBody({ token }) });
    if (state.user && user?.id === state.user.id) state.user = user;
    if (state.user) {
      enterApp();
      toast("Email confirmed. Thanks.");
    } else {
      showAuth("verify", { phase: "done" });
    }
  } catch (err) {
    showAuth("verify", { phase: "failed", message: err.message });
  }
}

// ---- the unconfirmed-email banner and its actions

function confirmActionsHtml() {
  return `
    <button class="btn btn--soft" type="button" data-action="resend">${icon("mail")}Resend link</button>
    ${state.demoVerifyUrl ? `<button class="btn btn--primary" type="button" data-action="demo-verify">${icon("check")}Confirm now (demo)</button>` : ""}`;
}

function renderBanner() {
  const user = state.user;
  if (!state.authRequired || !user || user.email_verified) {
    els.banner.innerHTML = "";
    return;
  }
  els.banner.innerHTML = `
    <div class="banner" data-confirm="banner">
      <span class="banner__icon" aria-hidden="true">${icon("mail")}</span>
      <div class="banner__text">
        <p class="banner__title">Confirm your email</p>
        <p>Open the link we sent to <strong>${esc(user.email)}</strong>.</p>
      </div>
      <div class="banner__actions">${confirmActionsHtml()}</div>
      <p class="banner__status" role="status"></p>
    </div>`;
}

/** Redraw both places that offer the confirm actions, keeping focus on the same action. */
function refreshConfirmUi(where, action) {
  renderBanner();
  if (els.account.open) renderAccountBody();
  if (!where) return null;
  const box = $(`[data-confirm="${where}"]`);
  $(`[data-action="${action}"]`, box ?? document)?.focus({ preventScroll: true });
  return box;
}

function confirmStatus(box, message, { error = false } = {}) {
  const out = box && $(".banner__status", box);
  if (!out) return;
  out.textContent = message;
  out.classList.toggle("is-error", error);
}

async function resendLink(button) {
  const where = button.closest("[data-confirm]")?.dataset.confirm;
  await withBusy(button, async () => {
    try {
      const reply = await api("/api/auth/resend-verification", { method: "POST", session: true });
      if (reply?.demo_verify_url) state.demoVerifyUrl = reply.demo_verify_url;
      confirmStatus(refreshConfirmUi(where, "resend"), "We sent a new link.");
    } catch (err) {
      if (err.signedOut) return;
      if (err.status === 409) {
        await refreshUser();
        refreshConfirmUi();
        (els.account.open ? els.accountTitle : els.todayTitle).focus({ preventScroll: true });
        toast("Your email is already confirmed.");
        return;
      }
      confirmStatus(button.closest("[data-confirm]"), err.message, { error: true });
    }
  });
}

/** Demo only: the server put the link in its reply, so confirm with its token right here. */
async function demoVerify(button) {
  let token = null;
  try {
    token = new URL(state.demoVerifyUrl, location.origin).searchParams.get("verify");
  } catch {
    token = null;
  }
  if (!token) return;
  const box = button.closest("[data-confirm]");
  await withBusy(button, async () => {
    try {
      const { user } = await api("/api/auth/verify-email", { method: "POST", ...jsonBody({ token }) });
      if (state.user && user?.id === state.user.id) state.user = user;
      else await refreshUser();
      state.demoVerifyUrl = null;
      updateAccountUi();
      refreshConfirmUi();
      (els.account.open ? els.accountTitle : els.todayTitle).focus({ preventScroll: true });
      toast("Email confirmed. Thanks.");
    } catch (err) {
      if (!err.signedOut) confirmStatus(box, err.message, { error: true });
    }
  });
}

// ---- the account sheet

function renderAccountBody() {
  const user = state.user;
  if (!user) return;
  const created = new Date(user.created_at);
  const since = Number.isNaN(created.getTime()) ? "" : `<p class="account__since">Member since ${monthYear.format(created)}</p>`;
  els.accountTitle.textContent = user.name || "Your account";
  els.accountBody.innerHTML = `
    <div class="account__id">
      <span class="avatar avatar--lg" aria-hidden="true">${esc(initials(user))}</span>
      <div class="account__who">
        <p class="account__email">${esc(user.email)}</p>
        <p>${
          user.email_verified
            ? `<span class="chip chip--ok">${icon("check")}Confirmed</span>`
            : '<span class="chip chip--alert">Not confirmed</span>'
        }</p>
      </div>
    </div>
    ${
      user.email_verified
        ? ""
        : `<div class="account__confirm" data-confirm="account">
             <p>Open the link we sent to confirm your email.</p>
             <div class="banner__actions">${confirmActionsHtml()}</div>
             <p class="banner__status" role="status"></p>
           </div>`
    }
    ${since}
    <section class="account__section" aria-labelledby="password-title">
      <h3 id="password-title" class="account__section-title">Password</h3>
      <button class="btn btn--soft btn--block" type="button" data-action="show-change-password" aria-expanded="false" aria-controls="change-password">${icon("key")}Change password</button>
      <form id="change-password" class="form form--plain" data-form="change-password" novalidate hidden>
        <input class="visually-hidden" type="email" name="username" autocomplete="username" value="${esc(user.email)}" tabindex="-1" aria-hidden="true" readonly>
        ${passwordFieldHtml({ id: "current-password", name: "current_password", label: "Current password", autocomplete: "current-password" })}
        ${passwordFieldHtml({ id: "new-password", name: "new_password", label: "New password", autocomplete: "new-password", hint: "At least 8 characters." })}
        <p class="form-error" role="alert" hidden></p>
        <button class="btn btn--primary btn--block" type="submit">Save new password</button>
      </form>
    </section>
    <p class="account__status status" role="status"></p>
    <button class="btn btn--soft btn--block" type="button" data-install>${icon("home")}Add to Home Screen</button>
    <button class="btn btn--secondary btn--block" type="button" data-action="sign-out">${icon("logout")}Sign out</button>`;
}

function openAccount() {
  if (!state.user) return;
  clearTimeout(accountTimer);
  els.account.classList.remove("is-closing");
  els.account.style.transform = "";
  accountMessage = "";
  renderAccountBody();
  document.documentElement.classList.add("has-sheet");
  if (!els.account.open) els.account.showModal();
  els.accountTitle.focus();
}

let accountTimer = 0;
let accountRestore = true;
let accountMessage = "";
let pressedAccountBackdrop = false;

function closeAccount({ restoreFocus: restore = true, swiped = false, message = "" } = {}) {
  if (!els.account.open) {
    if (message) toast(message);
    return;
  }
  accountRestore = restore;
  if (message) accountMessage = message;
  const done = () => els.account.open && els.account.close();
  clearTimeout(accountTimer);
  if (reducedMotion.matches) return done();
  if (swiped) {
    els.account.style.transition = "transform 200ms ease-in";
    els.account.style.transform = "translateY(100%)";
  } else {
    els.account.classList.add("is-closing");
  }
  accountTimer = setTimeout(done, 190);
}

els.account.addEventListener("close", () => {
  clearTimeout(accountTimer);
  els.account.classList.remove("is-closing");
  els.account.style.transition = "";
  els.account.style.transform = "";
  if (!els.sheet.open) document.documentElement.classList.remove("has-sheet");
  els.accountBody.innerHTML = "";
  if (accountRestore && !els.accountButton.hidden) els.accountButton.focus();
  accountRestore = true;
  if (accountMessage) toast(accountMessage);
  accountMessage = "";
});

els.account.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeAccount();
});

els.account.addEventListener("pointerdown", (event) => {
  pressedAccountBackdrop = event.target === els.account;
});
dragToDismiss(els.account, closeAccount);

els.account.addEventListener("click", (event) => {
  if (event.target === els.account && pressedAccountBackdrop) return closeAccount();
  const button = event.target.closest("[data-action]");
  switch (button?.dataset.action) {
    case "close-account":
      return closeAccount();
    case "toggle-password":
      return togglePassword(button);
    case "show-change-password": {
      const form = $("#change-password", els.account);
      const open = form.hidden;
      form.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
      if (open) $("input:not(.visually-hidden)", form).focus();
      return;
    }
    case "resend":
      return resendLink(button);
    case "demo-verify":
      return demoVerify(button);
    case "sign-out":
      return signOut(button);
  }
});

els.account.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAccountForm(event.target);
});

els.account.addEventListener("input", (event) => clearFieldError(event.target));

els.accountButton.addEventListener("click", openAccount);

async function signOut(button) {
  await withBusy(button, async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (err) {
      if (err.status !== 401) {
        setStatus(err.message, { error: true, target: $(".account__status", els.account) });
        return;
      }
    }
    closeAccount({ restoreFocus: false });
    resetData();
    state.user = null;
    state.demoVerifyUrl = null;
    updateAccountUi();
    renderBanner();
    showAuth("welcome");
    toast("You're signed out.");
  });
}

// --------------------------------------------------------------- history
// Every screen above the home screen has a history entry, so the Android back gesture and the
// browser's back button step back through the app (a logged meal, then Today, then a result)
// instead of leaving it. Home is Analyze, or the Today dashboard when accounts are on.

// "install" belongs to install.js (the Home Screen sheet); it never stays in the address bar.
const _bootParams = new URLSearchParams(location.search);
_bootParams.delete("install");
let baseUrl = location.pathname + (_bootParams.toString() ? "?" + _bootParams.toString() : "");

const SLOT_LAYER = { analyze: "result", today: "meal" };
const otherView = () => (state.home === "analyze" ? "today" : "analyze");

/** The screens stacked above the home screen right now. */
function layers() {
  if (document.body.dataset.screen !== "app") return [];
  const out = [];
  if (state.slots[state.home]) out.push(SLOT_LAYER[state.home]);
  const other = otherView();
  if (state.view === other) {
    out.push(other);
    if (state.slots[other]) out.push(SLOT_LAYER[other]);
  }
  return out;
}

const urlFor = (list) => (list.includes("today") ? `${baseUrl}#today` : baseUrl);
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
  if (document.body.dataset.screen !== "app") return;
  applyLayers(entryLayers(event.state));
});

/** The user went back (or forward): show the screens of the entry they landed on. */
function applyLayers(target) {
  const current = layers();
  const dropped = (name) => current.includes(name) && !target.includes(name);
  if (els.sheet.open) closeSheet({ restoreFocus: false });
  if (els.account.open) closeAccount({ restoreFocus: false });
  if (dropped("meal")) closeSlot("today", { focus: true });
  if (dropped("result")) closeSlot("analyze", { focus: true });
  const other = otherView();
  setView(target.includes(other) ? other : state.home);
  const now = layers();
  if (now.join() !== target.join()) history.replaceState({ layers: now }, "", urlFor(now));
  // Focus that was left in a view that is now hidden goes to the view's tab.
  const active = document.activeElement;
  if (active && active !== document.body && !active.getClientRects().length) visibleTab()?.focus({ preventScroll: true });
}

// ----------------------------------------------------------------- wiring

for (const button of $$("[data-pick]")) {
  button.addEventListener("click", () => (button.dataset.pick === "camera" ? els.camera : els.library).click());
}
for (const input of [els.library, els.camera]) {
  input.addEventListener("change", () => input.files[0] && analyze(input.files[0]));
}
for (const button of $$('.dock [data-action="snap"], .topbar [data-action="snap"]')) {
  button.addEventListener("click", snapMeal);
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

// Back to the page after a while (or past midnight): today may be a new day.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && document.body.dataset.screen === "app" && ensureToday()) loadDashboard();
});

/** ?verify= and ?reset= carry one-time tokens: take them, then drop them from the address bar. */
function takeLinkTokens() {
  const url = new URL(location.href);
  const tokens = { verify: url.searchParams.get("verify"), reset: url.searchParams.get("reset") };
  url.searchParams.delete("verify");
  url.searchParams.delete("reset");
  baseUrl = url.pathname + url.search;
  if (tokens.verify !== null || tokens.reset !== null) history.replaceState(null, "", baseUrl + url.hash);
  return tokens;
}

function bootFailed(err) {
  document.body.dataset.screen = "app";
  setPickers(false);
  setStatus(err.message, { error: true });
  els.today.dataset.state = "error";
  els.dashDay.innerHTML = `<div class="card dash-error"><p class="status is-error" role="alert">${esc(err.message)}</p></div>`;
}

async function init() {
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const links = takeLinkTokens();
  const startOnToday = location.hash === "#today";
  history.replaceState({ layers: [] }, "", baseUrl);
  onScroll();
  syncTabs();
  let status;
  let nutrients;
  try {
    [status, nutrients] = await Promise.all([
      api("/api/status", { session: false }),
      api("/api/nutrients", { session: false }).catch((err) => err),
    ]);
  } catch (err) {
    return bootFailed(err);
  }
  const accounts = status.auth_required === true;
  if (nutrients instanceof Error) {
    // With accounts on, nutrient labels may need a sign-in; they load again after it.
    if (!(accounts && nutrients.status === 401)) return bootFailed(nutrients);
  } else {
    state.nutrients = nutrients;
  }
  renderSetup(status);

  if (!accounts) {
    // No accounts on this server: the app as it always was, starting on Analyze.
    enterApp();
    if (startOnToday) {
      setView("today");
      syncHistory();
    }
    return;
  }

  enableAccounts(status);
  try {
    state.user = (await api("/api/auth/me")).user;
  } catch (err) {
    if (err.status !== 401) return bootFailed(err);
    state.user = null;
  }
  if (links.reset) {
    state.resetToken = links.reset;
    return showAuth("reset");
  }
  if (links.verify) return confirmEmail(links.verify);
  if (state.user) enterApp();
  else showAuth("welcome");
}

init();
