// MealScholar web client: a plain ES module over the JSON API (web.py, plus auth.py when accounts
// are on, and prefs.py and dining.py when the server has them); no build step. Product rules
// (README, AGENTS.md): every number is a range until the user weighs the food, micronutrients are
// labelled estimates, and % Daily Value is information only. Goals exist only when the person sets
// one themselves (GET /api/me/prefs); without that layer there are no targets or "remaining" numbers.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const els = {
  topbar: $(".topbar"),
  skip: $(".skip"),
  main: $("#main"),
  auth: $("#auth"),
  capture: $("#capture"),
  captureTitle: $("#capture-title"),
  snapTools: $("#snap-tools"),
  snapToolsHome: $("#snap-tools-home"),
  locateAsk: $("#locate-ask"),
  menuView: $("#menu"),
  menuTitle: $("#menu-title"),
  menuSub: $("#menu-sub"),
  menuHalls: $("#menu-halls"),
  menuBody: $("#menu-body"),
  weekView: $("#week"),
  weekPageTitle: $("#week-page-title"),
  weekSub: $("#week-sub"),
  railAlerts: $("#rail-alerts"),
  railDot: $("#rail-alerts .rail__dot"),
  railSettings: $("#rail-settings"),
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
  accountButton: $("#account-button"),
  account: $("#account"),
  accountKicker: $("#account-kicker"),
  accountTitle: $("#account-title"),
  accountIntro: $("#account-intro"),
  accountBody: $("#account-body"),
  sheet: $("#sheet"),
  sheetKicker: $("#sheet-kicker"),
  sheetTitle: $("#sheet-title"),
  sheetBody: $("#sheet-body"),
  toast: $("#toast"),
  // The preferences layer: alerts, the side column, the week strip, onboarding and the panel sheet.
  bell: $("#alerts-button"),
  bellDot: $("#alerts-button .bell__dot"),
  panel: $("#panel"),
  panelKicker: $("#panel-kicker"),
  panelTitle: $("#panel-title"),
  panelIntro: $("#panel-intro"),
  menuActions: $("#menu-actions"),
  panelBody: $("#panel-body"),
  onboard: $("#onboard"),
  dashSide: $("#dash-side"),
  dashRecent: $("#dash-recent"),
  strip: $("#dash-strip"),
  plus: $("#plus-button"),
  footerNote: $("#footer-note"),
};

const media = (query) => window.matchMedia(query);
const bottomSheet = media("(max-width: 39.99em)"); // sheets slide up from the bottom
const finePointer = media("(pointer: fine)");
const reducedMotion = media("(prefers-reduced-motion: reduce)");

const state = {
  ready: false,
  analyzing: false,
  nutrients: [],
  // A new analysis, with its photo, shows on the capture screen; a logged meal opens over Today.
  // Only the meal of the view on show is on screen.
  slots: { analyze: null, today: null }, // { result, photoUrl }
  shown: null, // the slot rendered into #result
  open: null, // { index, kind, id } while the edit sheet is open
  dismissedQuestion: null,
  view: "today", // today, menu or week (the tabs), or analyze (a photo being read, then its result)
  scroll: { analyze: 0, today: 0, menu: 0, week: 0, list: 0 },
  openedMeal: null,
  // Accounts: only when GET /api/status says auth_required.
  authRequired: false,
  emailProvider: null,
  emailSending: null, // GET /api/status email_sending; null from a server that doesn't say
  user: null,
  demoVerifyUrl: null,
  resendUntil: 0, // ms timestamp: Resend link waits until then (after a send, or a 429)
  resetToken: null,
  authPath: [], // signed out: the account screens stacked on Welcome, e.g. ["signin", "forgot"]
  // Bumped whenever the account changes (sign in, sign out, session end). Work started under an
  // older epoch (an analysis, opening a meal) drops its result instead of showing it to someone else.
  epoch: 0,
  // Dashboard
  day: null, // the day on show, YYYY-MM-DD
  todayKey: null, // what "today" was when last checked
  dashSeq: 0,
  microOpen: true, // the Week tab's vitamins card
  scoreOpen: false, // the health score's "How it's scored" disclosure
  // Preferences (goals, dining halls, diet, favorites, alerts): only once GET /api/me/prefs answers
  // 200, either for the signed-in account or for the server's one "local" user when sign-in is
  // optional. Without that, nothing below exists and the app is as it was.
  prefsOn: false,
  prefs: null,
  prefsError: null, // GET /api/me/prefs failed with something other than a 404: a notice on Today
  prefsVersion: 0,
  localUser: false, // sign-in optional on the server: the local user, who has no account sheet
  dining: null, // { locations } while the campus menus are connected; false when they aren't
  alerts: [],
  nearest: null, // the nearest dining hall from the device location; looked up once, then kept
  upcoming: null, // the side column: { meal, note, locationId } or { error, note }
  onboard: null, // { steps, at } while the set-up steps are on screen
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
    this.cancelled = false; // the app called the request off (see stopAnalysis)
  }
}

/** `session`: a 401 here means the sign-in ended (meal and day routes; some account routes). */
async function api(path, { method = "GET", body, headers, signal, session = !path.startsWith("/api/auth/") } = {}) {
  let response;
  try {
    response = await fetch(path, { method, body, headers, signal, credentials: "same-origin" });
  } catch {
    const error = new ApiError(OFFLINE);
    error.cancelled = Boolean(signal?.aborted);
    throw error;
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
  return `<span class="${cls}" style="--i:${index}" data-band="${esc(String(key))}" data-scale="${scale}" data-low="${range.low}" data-likely="${range.likely}" data-high="${range.high}" aria-hidden="true"><span class="band__range"></span><span class="band__tick"></span></span>`;
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
// Three tabs, Today, Menu and Week: the dock at the bottom on phones, the sidebar on laptops. A
// photo has no tab of its own: picking one (the + button) opens the capture screen, which shows the
// photo while it's read and then its result. A logged meal opens over Today.

const TABS = ["today", "menu", "week"];
const isTab = (view) => TABS.includes(view);
/** A tab that can show right now: Menu needs the campus menus. */
const canShow = (view) => isTab(view) && (view !== "menu" || diningOn());

/** The slot #result shows: the capture screen's new meal, or the meal opened over Today. */
function shownKey() {
  if (state.view === "analyze") return state.slots.analyze ? "analyze" : null;
  if (state.view === "today") return state.slots.today ? "today" : null;
  return null;
}

const shownMeal = () => state.shown?.result ?? null;

function replayEntrance(el) {
  el.classList.remove("is-entering");
  void el.offsetWidth;
  el.classList.add("is-entering");
}

/** The photo options (pick again, open the camera, use location) sit at the top of the capture
    screen, and at the top of a new result under its back button. One set of controls, moved. */
function placeTools() {
  const nav = state.shown && state.shown === state.slots.analyze ? $(".result__nav", els.result) : null;
  if (nav) {
    if (nav.nextElementSibling !== els.snapTools) nav.after(els.snapTools);
  } else if (els.snapTools.parentElement !== els.snapToolsHome) {
    els.snapToolsHome.append(els.snapTools);
  }
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
    else {
      placeTools();
      els.result.innerHTML = "";
    }
  }
  placeTools();
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
  const leaving = state.view;
  state.scroll[leaving] = window.scrollY;
  // The capture screen and a meal opened over Today belong to where they were opened: leaving
  // closes them (an analysis still running carries on, and says when it's done).
  if (leaving === "analyze") {
    discardSlot("analyze");
    if (!state.analyzing) setStatus("");
  }
  if (leaving === "today" && state.slots.today) discardSlot("today");
  state.view = view;
  document.body.dataset.view = view;
  queueSideFit();
  syncTabs();
  syncResult();
  $$(".view").forEach(replayEntrance);
  window.scrollTo(0, isTab(view) ? state.scroll[view] ?? 0 : 0);
  if (view === "today" || view === "week") ensureDashboard();
  if (view === "menu") ensureMenus();
}

function scrollTop() {
  window.scrollTo({ top: 0, behavior: reducedMotion.matches ? "auto" : "smooth" });
}

const viewTitle = (view) => ({ today: els.todayTitle, menu: els.menuTitle, week: els.weekPageTitle })[view] ?? null;

for (const tab of els.tabs) {
  tab.addEventListener("click", (event) => {
    event.preventDefault();
    const view = tab.dataset.view;
    if (!canShow(view)) return;
    if (view !== state.view) setView(view);
    // Tapping the active tab again goes back to the start of that view, as in native apps.
    else if (state.slots[view]) closeSlot(view, { focus: view === "today" });
    else if (view === "today" && state.day !== state.todayKey) goToDay(state.todayKey);
    else scrollTop();
    syncHistory();
  });
}

/** Laptops: wherever the dashboard reaches the window's right edge, the fixed + button sits over the
    side column's corner. The upcoming-meal card then keeps its bottom edge just above the button: at
    the top of the page the column starts under the title and week strip, pinned it starts under the
    bar, and the card takes whatever height is left either way. A stylesheet can't tell the two
    states apart, so the height is set here on scroll and resize. Where the + sits clear of the
    column (very wide windows), the card still fits the window, 16px above its bottom edge. */
const laptop = matchMedia("(min-width: 64em)");
const PLUS_LANE = 104; // the 60px button, 28px from the bottom, and a 16px gap above it
let sideFitQueued = false;

// The parts that don't change while scrolling (the + lane and the pinned line) are measured once
// per render or resize; a scroll frame then reads one rectangle and writes only when the height
// changes, so Safari isn't forced through a layout on every frame.
const sideFit = { lane: 16, pinned: 0, last: "" };

function measureSideFit() {
  const card = $(".upcoming", els.dashSide);
  if (!card) return;
  const plus = els.plus && getComputedStyle(els.plus).display !== "none" ? els.plus.getBoundingClientRect() : null;
  // Clear of the +, the card still ends 16px above the window's bottom edge.
  sideFit.lane = plus && plus.left < card.getBoundingClientRect().right ? PLUS_LANE : 16;
  // Never measure from above the pinned line: once the page end pushes the column up, the card
  // keeps its pinned height and moves with it, instead of growing into the space it left.
  sideFit.pinned = parseFloat(getComputedStyle(els.dashSide).top) || 0;
  sideFit.last = "";
}

function fitSideCard() {
  sideFitQueued = false;
  const card = $(".upcoming", els.dashSide);
  if (!card) return;
  // Up to 80em the card sits under the scans instead of pinned beside them, at its own height.
  if (!laptop.matches || document.body.dataset.view !== "today" || getComputedStyle(els.dashSide).position !== "sticky") {
    if (sideFit.last !== "") card.style.maxHeight = "";
    sideFit.last = "";
    return;
  }
  const top = Math.max(card.getBoundingClientRect().top, sideFit.pinned);
  const value = `${Math.max(240, Math.round(window.innerHeight - sideFit.lane - top))}px`;
  if (value === sideFit.last) return;
  card.style.maxHeight = value;
  sideFit.last = value;
}

function queueSideFit() {
  if (sideFitQueued) return;
  sideFitQueued = true;
  requestAnimationFrame(fitSideCard);
}

function remeasureSideFit() {
  measureSideFit();
  queueSideFit();
}

const onScroll = () => {
  els.topbar.classList.toggle("is-scrolled", window.scrollY > 4);
  queueSideFit();
};
window.addEventListener("scroll", onScroll, { passive: true });
window.addEventListener("resize", remeasureSideFit);
laptop.addEventListener("change", remeasureSideFit);

/** Laptops: the pinned left column scrolls inside itself when it's taller than its slot (larger
    browser text, a short window, an open "How it's scored"). Its scrollbar is hidden, so a soft fade
    at its bottom edge (styles.css) shows while more of it sits below. */
function markColumnMore() {
  const col = els.dashDay;
  col.classList.toggle("has-more", laptop.matches && col.scrollHeight - col.scrollTop - col.clientHeight > 2);
}

const columnSizes = new ResizeObserver(markColumnMore);

/** Watches the column and each card in it, so a card that grows or shrinks updates the fade too. */
function watchColumn() {
  columnSizes.disconnect();
  columnSizes.observe(els.dashDay);
  for (const card of els.dashDay.children) columnSizes.observe(card);
}

watchColumn();
new MutationObserver(watchColumn).observe(els.dashDay, { childList: true });
els.dashDay.addEventListener("scroll", markColumnMore, { passive: true });
laptop.addEventListener("change", markColumnMore);

/** The tab that is on screen for this layout: the tab bar on phones, the top bar on wide screens. */
const visibleTab = () => els.tabs.find((tab) => tab.matches("[aria-current]") && tab.getClientRects().length);

// --------------------------------------------------------------- capture

function setViewfinder(mode) {
  els.viewfinder.dataset.state = mode;
  const analyzing = mode === "analyzing";
  els.viewfinder.hidden = !analyzing;
  els.viewfinder.setAttribute("aria-busy", String(analyzing));
  els.captureTitle.textContent = analyzing ? "Reading your plate" : "Snap a meal";
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
      "<p>Add your Gemini key to <code>.env</code> as <code>GEMINI_API_KEY=…</code>, then restart <code>uv run mealscholar serve</code>.</p>"
    );
  }
  if (!status.food_db_ready) steps.push("<p>Build the USDA database once: <code>uv run mealscholar fdc-import</code></p>");
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

/** Focus the start of the capture screen: its heading while a photo is read, else the photo button. */
function focusCapture() {
  const usable = !state.analyzing && !els.choose.disabled;
  (usable ? els.choose : els.captureTitle).focus({ preventScroll: true });
}

/** When the photo was taken, as far as the browser knows: the file's time if it's from the last
    two days, else now (a camera capture is always "now"). The server prefers the photo's own
    EXIF time when it has one. */
function photoTime(file) {
  const t = Number(file?.lastModified);
  const now = Date.now();
  return Number.isFinite(t) && t <= now + 60_000 && now - t < 2 * 86_400_000 ? new Date(t) : new Date(now);
}

/** "2026-11-14T12:30:00-05:00": local time with its offset, as POST /api/meals?taken_at= wants. */
function localIso(date) {
  const pad = (n) => String(Math.trunc(Math.abs(n))).padStart(2, "0");
  const off = -date.getTimezoneOffset();
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${day}T${time}${off >= 0 ? "+" : "-"}${pad(off / 60)}:${pad(off % 60)}`;
}

const AT_HALL_M = 400; // within this of a dining commons, the photo is taken to be from it

async function uploadHall(coords) {
  if (!diningOn()) return null;
  if (coords) {
    const near = state.nearest ?? (await fetchNearest(coords[0], coords[1]));
    if (near && Number.isInteger(near.location?.id) && near.distance_m <= AT_HALL_M) return near.location.id;
  }
  const halls = state.prefs?.dining_halls ?? [];
  const picked = menuHallIds().includes(pickedHall(menuHallIds())) ? pickedHall(menuHallIds()) : null;
  const id = picked && picked !== DEFAULT_HALL ? picked : halls[0];
  return Number.isInteger(id) ? id : null;
}

async function analyze(file) {
  if (!state.ready) return;
  if (state.analyzing) return toast("Still analyzing the last photo.");
  if (file.type && !file.type.startsWith("image/")) {
    setStatus("That file isn't an image. Choose a JPEG, PNG or HEIC photo.", { error: true });
    return;
  }
  // A new photo replaces the last analysis (or the logged meal on show) and opens the capture screen.
  const leaving = Boolean(state.shown) || state.view !== "analyze";
  if (state.shown && state.shown === state.slots.today) discardSlot("today");
  discardSlot("analyze");
  setView("analyze");
  syncResult();
  if (leaving) {
    window.scrollTo(0, 0);
    replayEntrance(els.capture);
  }
  syncHistory();
  els.captureTitle.focus({ preventScroll: true });

  // The photo's URL belongs to this request, so opening another meal meanwhile can't take it away.
  // The analysis also belongs to the account that started it: signing out or a session ending
  // (resetData) calls it off, and a reply that comes back after that is dropped, never shown.
  const photoUrl = URL.createObjectURL(file);
  const epoch = state.epoch;
  const current = () => epoch === state.epoch;
  const controller = new AbortController();
  analysis = { controller, photoUrl };
  els.previewImg.src = photoUrl;
  state.analyzing = true;
  setViewfinder("analyzing");
  setStatus("Analyzing your photo. This usually takes a few seconds.");

  const params = new URLSearchParams();
  params.set("taken_at", localIso(photoTime(file)));
  let coords = null;
  if (els.location.checked) {
    try {
      const [lat, lon] = await devicePosition();
      coords = [lat, lon];
      params.set("lat", lat);
      params.set("lon", lon);
    } catch (err) {
      if (current()) setStatus(`${err.message} Continuing without location.`);
    }
    if (!current()) return; // stopAnalysis has already cleared the screen
  }
  // The hall whose menu the server checks the photo against: the dining commons you're at, else
  // the one picked on the Menu tab, else your main hall. Without one it's USDA only.
  const hall = await uploadHall(coords);
  if (!current()) return;
  if (hall != null) params.set("location_id", hall);

  try {
    const result = await api(`/api/meals?${params}`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
      signal: controller.signal,
    });
    if (!current()) {
      URL.revokeObjectURL(photoUrl);
      return;
    }
    setStatus("");
    state.slots.analyze = { result, photoUrl, nearest: null };
    state.scroll.analyze = 0;
    // With the campus menus connected, the same coordinates also name the nearest dining hall, once.
    if (coords && placeName(result.context) && diningOn()) noteNearest(coords, state.slots.analyze);
    if (state.view === "analyze") {
      syncResult({ enter: "analyze" });
    } else {
      // The user moved on while waiting: leave them there. A logged meal is on Today now.
      discardSlot("analyze");
      toast(result.id != null ? "Your meal is ready. It's under Today." : result.items.length ? "Your photo is analyzed." : "No food found in that photo.");
    }
    syncHistory();
    if (result.id != null) refreshDays([mealDay(result)]);
  } catch (err) {
    URL.revokeObjectURL(photoUrl);
    if (current() && !err.signedOut && !err.cancelled) {
      setStatus(err.message, { error: true });
      if (state.view !== "analyze") toast(`That photo couldn't be analyzed. ${err.message}`);
    }
  } finally {
    // After a sign-out the capture screen belongs to whoever is next (stopAnalysis cleared it).
    if (current()) {
      analysis = null;
      state.analyzing = false;
      setViewfinder("empty");
      els.previewImg.removeAttribute("src");
      els.library.value = "";
      els.camera.value = "";
    }
  }
}

let analysis = null; // { controller, photoUrl } while a photo is being analyzed

/** Call off the analysis in progress and clear the capture screen: its photo, request and status. */
function stopAnalysis() {
  if (analysis) {
    analysis.controller.abort();
    URL.revokeObjectURL(analysis.photoUrl);
    analysis = null;
  }
  state.analyzing = false;
  setViewfinder("empty");
  els.previewImg.removeAttribute("src");
  els.library.value = "";
  els.camera.value = "";
  setStatus("");
  setPickers(state.ready);
}

function discardSlot(key) {
  const slot = state.slots[key];
  if (!slot) return;
  if (slot.photoUrl) URL.revokeObjectURL(slot.photoUrl);
  state.slots[key] = null;
}

/** Close a meal: a logged one goes back to the dashboard, a new one (and the capture screen) to Today. */
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
      visibleOne(`[data-meal="${CSS.escape(String(state.openedMeal))}"]`, els.today)?.focus({ preventScroll: true });
    }
    return;
  }
  leaveCapture({ focus });
}

/** Off the capture screen, back to Today (an analysis still running carries on). */
function leaveCapture({ focus = false } = {}) {
  if (state.view === "analyze") setView("today");
  window.scrollTo(0, state.scroll.today ?? 0);
  if (focus) els.todayTitle.focus({ preventScroll: true });
}

/** "Snap a meal" (the + buttons, Snap another meal, Take or choose a photo): straight to the photo
    picker, which on a phone offers the camera or the library. The capture screen opens once a
    photo is chosen, so cancelling the picker leaves you where you were. */
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
  placeTools();
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
  const { result } = slot;
  // A new result shows the photo on this device; a logged meal, its stored copy when it has one.
  const photoUrl = slot.photoUrl ?? result.photo_url ?? null;
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
  // A photo with no food in it isn't a meal: no meal title, time or place, and the message once.
  const noFood = !result.items.length;

  return `
    <div class="result__nav">
      <button class="back" type="button" data-action="back">${icon("chevron-left")}${fromToday ? esc(name) : "Today"}</button>
    </div>
    <article class="card meal-card${photoUrl ? " meal-card--photo" : ""}">
      ${photoUrl ? photoHtml(result, photoUrl) : ""}
      <div class="meal-card__body">
        ${result.id != null ? `<p class="eyebrow">Meal #${esc(result.id)}</p>` : ""}
        <h1 id="result-title" class="result__title" tabindex="-1">${noFood ? "No food found" : `${esc(capitalize(ctx.meal_slot))}${place}`}</h1>
        ${noFood ? "" : `<p class="result__meta">${meta.join(" ")}</p>`}
        ${totalHtml(result, photoUrl)}
        ${slot.nearest && !noFood ? nearestNoteHtml(slot.nearest.note, "result") : ""}
      </div>
    </article>
    ${questionHtml(result)}
    ${itemsHtml(result)}
    ${noFood ? "" : nutrientsHtml(result)}
    ${noFood ? "" : notesHtml(result)}
    <div class="result__foot">
      <button class="cta cta--block" type="button" data-action="new">
        <span class="cta__pill">Snap another meal</span>
        <span class="cta__dot" aria-hidden="true">${icon("arrow")}</span>
      </button>
      <button class="btn btn--secondary btn--block" type="button" data-action="see-today">${icon("home")}${fromToday ? `Back to ${esc(backTo)}` : "Back to today"}</button>
      ${result.id != null ? `<div class="result__delete" data-delete>${deleteLinkHtml()}</div>` : ""}
    </div>`;
}

/** The quiet "Delete this meal" link under a logged meal, and the two-step check that replaces it. */
function deleteLinkHtml() {
  return `<button class="link-btn link-btn--quiet" type="button" data-action="delete-ask">${icon("trash")}Delete this meal</button>`;
}

function deleteAskHtml(photo = false) {
  return `
    <p class="delete__ask">Delete this meal${photo ? " and its photo" : ""}? Today and Week drop its calories.</p>
    <div class="delete__row">
      <button class="btn btn--danger" type="button" data-action="delete-confirm">${icon("trash")}Delete</button>
      <button class="btn btn--secondary" type="button" data-action="delete-cancel">Keep it</button>
    </div>`;
}

function totalHtml(result, photoUrl) {
  const kcal = result.totals.energy_kcal;
  if (!result.items.length) {
    return `<p class="total__empty">No food or drink found in this photo. Try another shot with the whole plate in view.</p>`;
  }
  const noPhoto = photoUrl ? "" : '<p class="answered">No photo was saved with this meal.</p>';
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
  // The likely number leads and the range sits under it, the same shape as the Today panel.
  return `
    <div class="total">
      <p class="eyebrow">Calories, this meal</p>
      <p class="total__value"><span class="total__num">${likely}</span><span class="total__unit">kcal</span>${exact ? "" : '<span class="visually-hidden">, most likely</span>'}</p>
      <p class="total__range">${
        exact
          ? "Every counted food has a set amount, so this is one number."
          : `Most likely, in a range of <strong>${low}${TO}${high}&nbsp;kcal</strong>`
      }</p>
      ${bandHtml("total-kcal", kcal, { dark: true })}
      ${weighable ? '<p class="total__likely">Know an amount? Set it to narrow the range.</p>' : ""}
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
  const caption =
    result.scale_reference && result.items.length ? `<figcaption class="shot__caption">Size judged against: ${esc(result.scale_reference)}</figcaption>` : "";
  return `
    <figure class="shot">
      <div class="shot__frame">
        <div class="shot__img">
          <img src="${esc(photoUrl)}"${photoUrl === result.photo_url ? " data-stored" : ""} alt="Your meal photo, with each detected food outlined and numbered">
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
      ${menuSourceHtml(result)}
      <ol class="items">${result.items.map((r, i) => itemHtml(r, i, gramsScale)).join("")}</ol>
    </section>`;
}

// ---- Which UMass Dining menu the foods were matched against, and the way to correct it

const MEAL_WORD = { breakfast: "breakfast", lunch: "lunch", dinner: "dinner", late_night: "late night", grab_n_go: "Grab ’n Go", daily_offerings: "daily offerings" };
const menuDayFormat = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" });
const monthDayFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

/** "2026-11-15" as that local day (not UTC midnight, which is the evening before here). */
const isoDay = (key) => parseDay(key);

/** "Nov 10 to 28", or "Nov 28 to Dec 3" across a month. */
function dayRangeText(start, end) {
  const [a, b] = [isoDay(start), isoDay(end)];
  if (start === end) return monthDayFormat.format(a);
  return a.getMonth() === b.getMonth() ? `${monthDayFormat.format(a)} to ${b.getDate()}` : `${monthDayFormat.format(a)} to ${monthDayFormat.format(b)}`;
}

/** "lunch · Sun, Nov 15" for a matched dish; "past menus" for one the hall served before. */
function servedText(dish) {
  if (!dish.date) return "from its past menus";
  return [MEAL_WORD[dish.meal] ?? dish.meal, menuDayFormat.format(isoDay(dish.date))].filter(Boolean).join(" · ");
}

/** A logged meal with a kept photo can be matched again, while the menus are connected. */
const canRematch = (result) => result.id != null && Boolean(result.photo_url) && diningOn();

function menuSourceHtml(result) {
  const m = result.menu;
  const fix = canRematch(result);
  const button = (label) =>
    `<button class="link-btn menu-src__change" type="button" data-action="menu" aria-haspopup="dialog">${label}<span class="visually-hidden">: the dining hall and day</span></button>`;
  if (!m) {
    return fix ? `<div class="menu-src menu-src--none"><p class="menu-src__text">Ate at a dining hall?</p>${button("Match it to the menu")}</div>` : "";
  }
  const hall = `<strong>${esc(m.location_name)}</strong>`;
  let text;
  if (m.certain) {
    // The day and the range stay whole ("Sat, Nov 14", never "Sat," then "Nov 14" on the next line).
    const day = `<span class="nowrap">${esc(menuDayFormat.format(isoDay(m.date)))}</span>`;
    const parts = [hall, m.meal ? esc(capitalize(MEAL_WORD[m.meal] ?? m.meal)) : null, day].filter(Boolean);
    text = m.dishes ? `From UMass Dining: ${parts.join(" · ")}` : `No UMass Dining menu for ${hall} on ${day}`;
  } else if (m.start && m.end) {
    text = `Photo had no date: matched against ${hall} menus, <span class="nowrap">${esc(dayRangeText(m.start, m.end))}</span>`;
  } else {
    text = m.dishes ? `Photo had no date: matched against dishes ${hall} has served` : `No UMass Dining menus for ${hall} near this day`;
  }
  return `
    <div class="menu-src${m.certain ? "" : " menu-src--unsure"}">
      <span class="menu-src__icon" aria-hidden="true">${icon("fork")}</span>
      <p class="menu-src__text">${text}</p>
      ${fix ? button("Change") : ""}
    </div>`;
}

/** The halls to pick from: the meal's own, the user's, then the dining commons; each once. */
function rematchHalls(meal) {
  const locations = state.dining?.locations ?? [];
  const ids = [
    meal.menu?.location_id,
    ...(state.prefs?.dining_halls ?? []),
    ...locations.filter((l) => l.is_dining_commons === true).map((l) => l.id),
  ].filter(Number.isInteger);
  // The legend already says "Dining hall": "Worcester", not four chips that each end in "Dining Commons".
  const short = (name) => name.replace(/\s+Dining Commons$/i, "");
  return [...new Set(ids)].map((id) => ({ id, name: short(id === meal.menu?.location_id ? meal.menu.location_name : hallNameById(id) ?? `Hall ${id}`) }));
}

/** Today first, then back a week, never before the first menu the meal was matched against;
    the meal's own day and menu day stay on the list. */
function rematchDays(meal) {
  const today = localDate();
  const floor = meal.menu && !meal.menu.certain && meal.menu.start ? meal.menu.start : null;
  const days = [];
  for (let n = 0; n < 8; n++) {
    const key = shiftDay(today, -n);
    if (floor && key < floor) break;
    days.push(key);
  }
  for (const key of [mealDay(meal), meal.menu?.certain ? meal.menu.date : null]) {
    if (key && key <= today && !days.includes(key)) days.push(key);
  }
  return days;
}

function dayChipLabel(key) {
  const today = localDate();
  if (key === today) return "Today";
  if (key === shiftDay(today, -1)) return "Yesterday";
  return menuDayFormat.format(isoDay(key));
}

function choiceHtml(name, value, label, checked) {
  return `<label class="menu-pick"><input class="menu-pick__input" type="radio" name="${name}" value="${esc(value)}"${checked ? " checked" : ""}><span class="menu-pick__label">${esc(label)}</span></label>`;
}

function menuSheetHtml(meal) {
  const m = meal.menu;
  const halls = rematchHalls(meal);
  const hall = m?.location_id ?? halls[0]?.id;
  const day = m?.certain ? m.date : "";
  const mealKey = m?.certain && m.meal && ["breakfast", "lunch", "dinner", "late_night"].includes(m.meal) ? m.meal : "";
  const meals = [["", "Any meal"], ["breakfast", "Breakfast"], ["lunch", "Lunch"], ["dinner", "Dinner"], ["late_night", "Late night"]];
  return `
    <form class="rematch" data-form="menu">
      <fieldset class="rematch__group">
        <legend class="field-label">Dining hall</legend>
        <div class="rematch__choices">${halls.map((h) => choiceHtml("hall", h.id, h.name, h.id === hall)).join("")}</div>
      </fieldset>
      <fieldset class="rematch__group">
        <legend class="field-label">Day</legend>
        <div class="rematch__choices">
          ${rematchDays(meal).map((key) => choiceHtml("day", key, dayChipLabel(key), key === day)).join("")}
          ${choiceHtml("day", "", "Not sure", !day)}
        </div>
      </fieldset>
      <fieldset class="rematch__group">
        <legend class="field-label">Meal <span class="rematch__optional">optional</span></legend>
        <div class="rematch__choices">${meals.map(([key, label]) => choiceHtml("meal", key, label, key === mealKey)).join("")}</div>
      </fieldset>
      <p class="sheet-hint">MealScholar reads your photo again against that menu, which takes a few seconds. Not sure of the day? It checks the menus around the photo’s date.</p>
      <p class="sheet-error" role="alert"></p>
      <button class="btn btn--primary btn--block rematch__submit" type="submit">${icon("check")}<span data-label>Match to this menu</span></button>
    </form>`;
}

function openMenuSheet() {
  const meal = shownMeal();
  if (!meal || !canRematch(meal)) return;
  clearTimeout(closeTimer);
  els.sheet.classList.remove("is-closing");
  els.sheet.style.transform = "";
  sheetMessage = "";
  state.open = { index: null, kind: "menu", id: meal.id };
  els.sheet.dataset.kind = "menu";
  els.sheetKicker.textContent = "UMass Dining menu";
  els.sheetTitle.textContent = "Where and when was this?";
  els.sheetBody.innerHTML = menuSheetHtml(meal);
  document.documentElement.classList.add("has-sheet");
  if (!els.sheet.open) els.sheet.showModal();
  els.sheetTitle.focus();
}

/** POST /api/meals/{id}/menu: the stored photo, analyzed again against the hall and day picked. */
async function rematchMeal(form) {
  const data = new FormData(form);
  const body = { location_id: Number(data.get("hall")) };
  if (data.get("day")) body.date = String(data.get("day"));
  if (data.get("meal")) body.meal = String(data.get("meal"));
  const id = state.open?.id;
  const epoch = state.epoch;
  const button = $('button[type="submit"]', form);
  const label = $("[data-label]", button);
  const error = $(".sheet-error", form);
  if (id == null || !Number.isInteger(body.location_id)) return;
  error.textContent = "";
  await withBusy(button, async () => {
    label.textContent = "Matching…";
    try {
      const result = await api(`/api/meals/${id}/menu`, { method: "POST", ...jsonBody(body) });
      if (epoch !== state.epoch) return; // signed out meanwhile
      updateMeal(result);
      closeSheet({ message: result.menu ? `Matched against the ${result.menu.location_name} menu.` : "Matched again." });
    } catch (err) {
      if (error.isConnected && !err.signedOut) error.textContent = err.message;
    } finally {
      if (label.isConnected) label.textContent = "Match to this menu";
    }
  });
}

function itemHtml(r, i, gramsScale) {
  const { item, match } = r;
  const range = itemGrams(item);
  const weighed = isExact(range);
  const kcal = r.nutrients.energy_kcal;
  const dish = r.source === "campus" && r.dish ? r.dish : null;
  const name = esc(capitalize(dish ? dish.name : item.name));
  const method = item.cooking_method === "unknown" ? "" : capitalize(item.cooking_method.replace(/_/g, " "));
  const meta = [
    method && `<span class="chip">${esc(method)}</span>`,
    weighed ? `<span class="chip chip--ok">${icon("check")}Set amount</span>` : null,
    item.hidden_fat_likely ? `<span class="chip chip--warn">${icon("drop")}Likely hidden oil or butter</span>` : null,
  ].filter(Boolean);
  const energy = match || dish
    ? kcal
      ? rangeHtml(kcal, "kcal", undefined, { stack: true })
      : '<span class="value__none">No energy data</span>'
    : '<span class="chip chip--alert">Not counted</span>';
  const matchLine = dish
    ? `<p class="item__match"><span class="item__src">Menu<span class="visually-hidden">:</span></span> ${esc(dish.location_name)}’s ${esc(dish.name)}${dish.serving_size ? `, label per ${esc(dish.serving_size)}` : ""}<span class="item__id">${SEP}${esc(servedText(dish))}${item.name !== dish.name ? ` · what the photo shows: ${esc(item.name)}` : ""}</span></p>`
    : match
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
        <span class="field-label">${weighed ? "Amount" : "Estimated amount"}</span>
        ${bandHtml(`grams-${i}`, range, { scale: gramsScale })}${rangeHtml(range, "g", grams)}
      </div>
      ${matchLine}
      <div class="item__actions">
        <button class="btn btn--soft" type="button" data-action="weigh" data-index="${i}" aria-haspopup="dialog">${icon("scale")}${weighed ? "Change amount" : "Set amount"}<span class="visually-hidden">: ${name}</span></button>
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

/** DELETE /api/meals/{id}, then leave the meal: Today goes back to the dashboard (its day reloads
    without the meal), Analyze goes back to the capture screen. A 404 means it is already gone. */
async function deleteMeal(button) {
  const key = shownKey();
  const slot = key ? state.slots[key] : null;
  const id = slot?.result.id;
  if (id == null) return;
  const epoch = state.epoch;
  await withBusy(button, async () => {
    try {
      await api(`/api/meals/${id}`, { method: "DELETE" });
    } catch (err) {
      if (epoch !== state.epoch || err.signedOut) return;
      if (err.status !== 404) {
        toast(err.message);
        return;
      }
    }
    if (epoch !== state.epoch) return;
    const day = mealDay(slot.result);
    if (state.openedMeal === id) state.openedMeal = null;
    closeSlot(key);
    els.todayTitle.focus({ preventScroll: true });
    refreshDays([day]);
    syncHistory();
    toast("Meal deleted.");
  });
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
      snapMeal();
      break;
    case "see-today":
      if (shownKey() === "today") {
        closeSlot("today", { focus: true });
      } else {
        leaveCapture({ focus: true });
      }
      syncHistory();
      break;
    case "dismiss-question":
      state.dismissedQuestion = shownMeal().id;
      renderResult("update", { focus: "#foods-title" });
      break;
    case "delete-ask": {
      const box = button.closest("[data-delete]");
      box.innerHTML = deleteAskHtml(Boolean(shownMeal()?.photo_url));
      $('[data-action="delete-cancel"]', box).focus();
      break;
    }
    case "delete-cancel": {
      const box = button.closest("[data-delete]");
      box.innerHTML = deleteLinkHtml();
      $('[data-action="delete-ask"]', box).focus();
      break;
    }
    case "delete-confirm":
      deleteMeal(button);
      break;
    case "weigh":
    case "food":
      openSheet(button.dataset.action, index);
      break;
    case "menu":
      openMenuSheet();
      break;
    case "settings":
      openSettings(button.dataset.section, button);
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
  const epoch = state.epoch;
  if (!answer || id == null) return;
  await withBusy(button, async () => {
    try {
      const result = await api(`/api/meals/${id}/answer`, { method: "POST", ...jsonBody({ answer }) });
      if (epoch !== state.epoch) return; // signed out meanwhile
      updateMeal(result, { focus: "#result-title" });
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
  els.sheetKicker.textContent = kind === "weigh" ? (weighed ? "Change amount" : "Set amount") : r.match ? "Change food" : "Pick a USDA food";
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
  const opener = open?.index == null ? `[data-action="${open?.kind}"]` : `[data-action="${open.kind}"][data-index="${open.index}"]`;
  if (open && restoreFocus) $(opener, els.result)?.focus();
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
  if (form.dataset.form === "menu" && state.open) return rematchMeal(form);
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
      <span class="field-label">${isExact(range) ? "Current amount" : "Estimated amount"}</span>
      ${bandHtml(`sheet-grams-${i}`, range, { scale: range.high * 1.1 || 1 })}${rangeHtml(range, "g", grams)}
    </div>
    <form class="weigh" data-form="weigh">
      <label class="field-label" for="grams-${i}">Amount in grams<span class="visually-hidden"> of ${esc(r.item.name)}</span></label>
      <div class="weigh__row">
        <span class="field-unit">
          <input id="grams-${i}" class="field" name="grams" type="number" inputmode="numeric" pattern="[0-9]*" min="1" max="10000" step="1" required placeholder="${esc(r.item.grams_likely)}" enterkeyhint="done" aria-describedby="weigh-hint">
          <span aria-hidden="true">g</span>
        </span>
        <button class="btn btn--primary" type="submit">${icon("check")}Set amount</button>
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
  const epoch = state.epoch;
  if (id == null) return;
  await withBusy(button, async () => {
    try {
      const result = await api(`/api/meals/${id}/items/${index}`, { method: "PATCH", ...jsonBody(body) });
      if (epoch !== state.epoch) return; // signed out meanwhile
      updateMeal(result);
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

function greetingPart() {
  const hour = new Date().getHours();
  return `Good ${hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 18 ? "afternoon" : "evening"}`;
}

function greetingText() {
  const name = state.authRequired ? firstName(state.user) : "";
  return name ? `${greetingPart()}, ${name}` : greetingPart();
}

function renderDashHead() {
  const key = state.day;
  const { title, sub } = dayTitle(key);
  const isToday = key === state.todayKey;
  els.todayTitle.textContent = title;
  els.todayDate.textContent = sub;
  // Text, never HTML: the name comes from the server.
  els.greeting.textContent = isToday ? `${greetingText()}. Your day so far, every amount a range.` : "What you ate that day, every amount a range.";
  const active = document.activeElement;
  els.dayNext.disabled = isToday; // never a future day
  els.dayToday.hidden = isToday;
  if (isToday && (active === els.dayNext || active === els.dayToday)) els.dayPrev.focus();
  els.dayPrev.setAttribute("aria-label", `Previous day, ${longDay.format(parseDay(shiftDay(key, -1)))}`);
  els.dayNext.setAttribute("aria-label", isToday ? "Next day" : `Next day, ${longDay.format(parseDay(shiftDay(key, 1)))}`);
  renderStrip();
}

/** Laptop, with preferences: a slide of weeks, Monday to Sunday on each page, the current week last
    and on show first. Swipe, scroll sideways or use the chevrons at the ends to page a week; the day
    arrows still step one day and the slide follows the day on show. Today is ringed, the day on show
    filled in, days still ahead disabled. A past day opens like any other (the same data-day handler). */
const STRIP_WEEKS = 8; // this week and the 7 before it
const WEEK_MS = 7 * 864e5;
const strip = { key: "", day: null, page: 0, pages: 0, timer: 0 };

const mondayOf = (key) => shiftDay(key, -((parseDay(key).getDay() + 6) % 7));
const weeksBetween = (from, to) => Math.round((parseDay(to) - parseDay(from)) / WEEK_MS); // DST-safe
const stripTrack = () => $(".strip__track", els.strip);

function stripDayHtml(key, today) {
  const date = parseDay(key);
  const isToday = key === today;
  const future = key > today;
  const label = `${isToday ? "Today, " : ""}${longDay.format(date)}${future ? ", still ahead" : ""}`;
  return `
    <button class="strip__day${isToday ? " is-today" : ""}" type="button" data-day="${key}"${key === state.day ? ' aria-current="date"' : ""}${future ? " disabled" : ""} aria-label="${esc(label)}">
      <span class="strip__name" aria-hidden="true">${weekdayShort.format(date)}</span>
      <span class="strip__num" aria-hidden="true">${date.getDate()}</span>
    </button>`;
}

function renderStrip() {
  if (!state.prefsOn) {
    els.strip.hidden = true;
    els.strip.innerHTML = "";
    strip.key = "";
    return;
  }
  const today = state.todayKey;
  const thisMonday = mondayOf(today);
  // Eight weeks, or as many as it takes to reach a day picked further back with the day arrows.
  const back = Math.max(STRIP_WEEKS - 1, weeksBetween(mondayOf(state.day), thisMonday));
  const key = `${today}|${back}`;
  if (key !== strip.key) {
    const weeks = Array.from({ length: back + 1 }, (_, i) => shiftDay(thisMonday, (i - back) * 7));
    const html = `
      <button class="strip__nav" type="button" data-strip="-1" aria-label="Earlier week">${icon("chevron-left")}</button>
      <div class="strip__track">${weeks
        .map(
          (monday) => `
        <div class="strip__week" role="group" aria-label="${esc(`Week of ${monthDay.format(parseDay(monday))}`)}">${Array.from({ length: 7 }, (_, d) => stripDayHtml(shiftDay(monday, d), today)).join("")}</div>`
        )
        .join("")}</div>
      <button class="strip__nav" type="button" data-strip="1" aria-label="Later week">${icon("chevron-right")}</button>`;
    redraw(els.strip, html, "data-day");
    strip.key = key;
    strip.pages = weeks.length;
    strip.day = null;
  } else {
    for (const button of $$(".strip__day", els.strip)) {
      if (button.dataset.day === state.day) button.setAttribute("aria-current", "date");
      else button.removeAttribute("aria-current");
    }
  }
  els.strip.hidden = false;
  if (strip.day !== state.day) {
    const first = strip.day === null;
    strip.day = state.day;
    showStripPage(strip.pages - 1 - weeksBetween(mondayOf(state.day), thisMonday), !first);
  }
}

/** Page the slide to week `index` (0 is the oldest), sliding unless motion is reduced. */
function showStripPage(index, slide = true) {
  strip.page = Math.min(strip.pages - 1, Math.max(0, index));
  const track = stripTrack();
  if (track?.clientWidth) {
    track.scrollTo({ left: strip.page * track.clientWidth, behavior: slide && !reducedMotion.matches ? "smooth" : "auto" });
  }
  syncStripPage();
}

/** The chevrons say when there's no further week, and only the week on show is in the Tab order
    (the chevrons reach the others). aria-disabled rather than disabled, so a chevron that runs out
    keeps keyboard focus. */
function syncStripPage() {
  const [prev, next] = $$(".strip__nav", els.strip);
  if (!prev) return;
  prev.setAttribute("aria-disabled", String(strip.page <= 0));
  next.setAttribute("aria-disabled", String(strip.page >= strip.pages - 1));
  $$(".strip__week", els.strip).forEach((week, i) => {
    for (const button of $$(".strip__day", week)) {
      if (i === strip.page) button.removeAttribute("tabindex");
      else button.tabIndex = -1;
    }
  });
}

// A swipe or a trackpad scroll settles on a page: note which one once it stops.
els.strip.addEventListener(
  "scroll",
  () => {
    clearTimeout(strip.timer);
    strip.timer = setTimeout(() => {
      const track = stripTrack();
      if (!track?.clientWidth) return;
      strip.page = Math.round(track.scrollLeft / track.clientWidth);
      syncStripPage();
    }, 90);
  },
  { capture: true, passive: true }
);

// The strip has no width on phones; when it gets one (or a new one), put the same week back on show.
new ResizeObserver(() => {
  const track = stripTrack();
  if (track?.clientWidth) track.scrollTo({ left: strip.page * track.clientWidth, behavior: "auto" });
}).observe(els.strip);

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
  ensureUpcoming();
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
  if (value != null) visibleOne(`[${key}="${CSS.escape(value)}"]`, region)?.focus({ preventScroll: true });
}

/** The first match that is on screen; the same meal is listed twice on laptops (the phone list and
    the Recently uploaded cards), and only the visible one can take focus. */
const visibleOne = (selector, root = document) => $$(selector, root).find((el) => el.getClientRects().length) ?? $(selector, root);

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
  const sig = `${key}|${entry?.version ?? 0}|${view ? "v" : failed ? "e" : "l"}|${state.nutrients.length}|${state.prefsVersion}`;
  if (sig === rendered.day) return;
  rendered.day = sig;
  if (!view) {
    els.dashDay.innerHTML = failed ? dayErrorHtml(entry.error) : daySkeletonHtml();
    els.dashMicro.innerHTML = "";
    els.dashRecent.innerHTML = "";
    return;
  }
  const keepMicroFocus = els.dashMicro.contains(document.activeElement);
  const previous = new Map([...snapshotBands(els.dashDay), ...snapshotBands(els.dashMicro)]);
  // Keyboard focus on a meal (the phone list or a laptop card) comes back to the same meal.
  const focusedMeal = document.activeElement?.closest?.("[data-meal]")?.dataset.meal ?? null;
  // With preferences on, the day carries the health score. It follows the nutrient tiles, so on
  // phones the calories and the tiles share the first screen; laptops show it above the tiles.
  if (view.meals.length) {
    const score = state.prefsOn ? scoreHtml(view) : "";
    // Phones: the six nutrients and the score swipe sideways, three tiles at a time (swipeHtml).
    const middle = laptop.matches ? statsHtml(view) + score : swipeHtml(view, score);
    els.dashDay.innerHTML = heroHtml(view) + middle + mealsHtml(view);
    restoreSwipe();
    els.dashMicro.innerHTML = microHtml(view) || microEmptyHtml(key, true);
  } else {
    els.dashDay.innerHTML = emptyDayHtml(key);
    els.dashMicro.innerHTML = microEmptyHtml(key, false);
  }
  if (keepMicroFocus) $("summary", els.dashMicro)?.focus({ preventScroll: true });
  els.dashRecent.innerHTML = state.prefsOn ? recentHtml(view) : "";
  if (focusedMeal != null) visibleOne(`[data-meal="${CSS.escape(focusedMeal)}"]`, els.today)?.focus({ preventScroll: true });
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
  if (goalsMode()) return goalHeroHtml(kcal, head, state.prefs.calorie_goal);
  const setGoal = state.prefsOn ? ` ${settingsLinkHtml("Set a goal", "goals", { light: true })}` : "";
  return `
    <section class="card hero" aria-labelledby="hero-title">
      ${head}
      <div class="hero__main">
        <p class="hero__value"><span class="hero__num">${likely}</span><span class="hero__unit">kcal</span>${exact ? "" : '<span class="visually-hidden">, most likely</span>'}</p>
        <p class="hero__range">${
          exact
            ? "Every counted food has a set amount, so this is one number."
            : `Most likely, in a range of <strong>${low}${TO}${high}&nbsp;kcal</strong>`
        }</p>
      </div>
      <div class="hero__foot">
        ${bandHtml("dash-kcal", kcal, { dark: true })}
        <p class="hero__note">${icon("info")}<span>A range, not a target. There's no goal to hit.${setGoal}</span></p>
      </div>
    </section>`;
}

/** A link into Settings, at one of its sections. `light` is for the dark calorie panel. */
function settingsLinkHtml(label, section, { light = false } = {}) {
  return `<button class="link-btn link-btn--inline${light ? " link-btn--light" : ""}" type="button" data-action="settings" data-section="${section}" aria-haspopup="dialog">${label}</button>`;
}

/** "360 kcal left" or "over by 140 kcal": never a negative number, and never "over by 0" when the
    difference rounds away. */
function leftText(value, unit, format = (v) => amount(v, unit)) {
  const shown = format(Math.abs(value));
  if (/^0(?:\.0)?$/.test(shown)) return "at your goal";
  return value >= 0 ? `${shown} ${unit} left` : `over by ${shown} ${unit}`;
}

/** What's left across the range, low to high, each side worded on its own. */
function leftRangeText(goal, range, unit, format = (v) => amount(v, unit)) {
  const [least, most] = [goal - range.high, goal - range.low];
  if (least >= 0) return `${format(least)} to ${format(most)} ${unit} left`;
  if (most <= 0) return `over by ${format(-most)} to ${format(-least)} ${unit}`;
  return `${format(most)} ${unit} left to over by ${format(-least)} ${unit}`;
}

/** The calorie panel with a goal: likely of goal, the goal as the whole bar, and what's left. */
function goalHeroHtml(kcal, head, goal) {
  const [low, likely, high] = [amount(kcal.low, "kcal"), amount(kcal.likely, "kcal"), amount(kcal.high, "kcal")];
  const exact = low === high;
  const left = goal - kcal.likely;
  const atGoal = amount(Math.abs(left), "kcal") === "0";
  const leftLine = atGoal
    ? exact
      ? "Right at your goal"
      : "Right at your goal, most likely"
    : exact
      ? capitalize(leftText(left, "kcal"))
      : left >= 0
        ? `About ${leftText(left, "kcal")}`
        : `Over by about ${amount(-left, "kcal")} kcal`;
  return `
    <section class="card hero hero--goal" aria-labelledby="hero-title">
      ${head}
      <div class="hero__main">
        <p class="hero__value"><span class="visually-hidden">Likely </span><span class="hero__num">${likely}</span><span class="hero__unit">of ${amount(goal, "kcal")} kcal</span></p>
        <p class="hero__range">${
          exact
            ? "Every counted food has a set amount, so this is one number."
            : `Likely, in a range of <strong>${low}${TO}${high}&nbsp;kcal</strong>`
        }</p>
      </div>
      <div class="hero__foot">
        ${bandHtml("dash-kcal", kcal, { dark: true, scale: goal })}
        <p class="hero__left"><strong>${leftLine}</strong>${exact ? "" : `<span class="hero__left-range">${SEP}Range: ${leftRangeText(goal, kcal, "kcal")}</span>`}</p>
        <p class="hero__note">${icon("info")}<span>Your daily goal is ${amount(goal, "kcal")} kcal. ${settingsLinkHtml("Change goal", "goals", { light: true })}</span></p>
      </div>
    </section>`;
}

function statHtml(view, key, label, size) {
  const range = view.totals[key];
  const pct = view.daily_value_pct[key];
  const unit = unitOf(key);
  const fmt = range ? tileFormat(range) : null;
  const goal = goalsMode() ? nutrientGoal(key) : null;
  const figure = range
    ? `<p class="stat__figure">
         <span class="stat__value"><strong>${fmt(range.likely)}</strong><span class="stat__unit">${esc(unit)}</span>${isExact(range) ? "" : '<span class="visually-hidden">, most likely</span>'}</span>
         <span class="stat__range">${isExact(range) ? "Set amount" : `<span class="visually-hidden">range </span>${fmt(range.low)}${TO}${fmt(range.high)}`}</span>
       </p>`
    : '<p class="stat__figure"><span class="stat__none">No data</span></p>';
  let foot;
  if (range && goal != null) {
    // The goal is the whole bar; what's left counts from the likely amount.
    const left = goal - range.likely;
    foot = `${bandHtml(`dash-${key}`, range, { scale: goal || 1 })}<p class="stat__dv stat__left">${SEP}${esc(leftText(left, unit, (v) => fmt(v)))}</p>`;
  } else if (range && pct) {
    foot = `${bandHtml(`dash-${key}`, pct, { scale: Math.max(100, pct.high * 1.05) })}<p class="stat__dv">${SEP}${dvHtml(pct)}</p>`;
  } else {
    foot = `<p class="stat__dv stat__dv--none">${SEP}No Daily Value</p>`;
  }
  return `<li class="card stat stat--${size}"><p class="stat__label">${esc(label)}</p>${figure}${foot}</li>`;
}

const GOAL_NOTE = "Bars run to your daily goal, or to the FDA Daily Value where you have none. What's left counts from the likely amount.";

function statsHtml(view) {
  return `
    <section class="stats-block" aria-labelledby="stats-title">
      <h2 id="stats-title" class="visually-hidden">Nutrients</h2>
      <ul class="stats stats--macro">${MACROS.map(([key, label]) => statHtml(view, key, label, "macro")).join("")}</ul>
      <ul class="stats stats--mini">${MINIS.map(([key, label]) => statHtml(view, key, label, "mini")).join("")}</ul>
      <p class="note stats__note">${goalsMode() ? GOAL_NOTE : DV_NOTE}</p>
    </section>`;
}

/** Phones: protein, carbs and fat; swipe for fiber, sugars and sodium; swipe again for the health
    score. One dot per page; the page shown survives a redraw. */
function swipeHtml(view, score) {
  const pages = [
    ["Protein, carbs and fat", `<ul class="stats stats--macro">${MACROS.map(([key, label]) => statHtml(view, key, label, "macro")).join("")}</ul>`],
    ["Fiber, sugars and sodium", `<ul class="stats stats--mini">${MINIS.map(([key, label]) => statHtml(view, key, label, "mini")).join("")}</ul>`],
    ...(score ? [["Health score", score]] : []),
  ];
  const at = Math.min(state.swipePage ?? 0, pages.length - 1);
  return `
    <section class="stats-block swipe" aria-labelledby="stats-title">
      <h2 id="stats-title" class="visually-hidden">Nutrients${score ? " and health score" : ""}</h2>
      <div class="swipe__track" tabindex="0" role="group" aria-label="Swipe for more">
        ${pages.map(([label, html], i) => `<div class="swipe__page" role="group" aria-label="${i + 1} of ${pages.length}: ${label}">${html}</div>`).join("")}
      </div>
      <div class="swipe__dots">
        ${pages.map(([label], i) => `<button class="swipe__dot" type="button" data-swipe-to="${i}" aria-label="${label}"${i === at ? ' aria-current="true"' : ""}></button>`).join("")}
      </div>
      <p class="note stats__note">${goalsMode() ? GOAL_NOTE : DV_NOTE}</p>
    </section>`;
}

/** Put the swipe back on the page it showed before a redraw, without animating. */
function restoreSwipe() {
  const track = $(".swipe__track", els.dashDay);
  if (!track) return;
  const pages = track.children.length;
  const at = Math.min(state.swipePage ?? 0, pages - 1);
  track.scrollLeft = at * track.clientWidth;
  markSwipe(track, at);
}

function markSwipe(track, at) {
  $$(".swipe__dot", track.parentElement).forEach((dot, i) => {
    if (i === at) dot.setAttribute("aria-current", "true");
    else dot.removeAttribute("aria-current");
  });
}

let swipeFrame = 0;
els.dashDay.addEventListener(
  "scroll",
  (event) => {
    const track = event.target;
    if (!(track instanceof Element) || !track.classList.contains("swipe__track")) return;
    cancelAnimationFrame(swipeFrame);
    swipeFrame = requestAnimationFrame(() => {
      const at = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      state.swipePage = at;
      markSwipe(track, at);
    });
  },
  { capture: true, passive: true }
);

els.dashDay.addEventListener("click", (event) => {
  const dot = event.target.closest("[data-swipe-to]");
  if (!dot) return;
  const track = $(".swipe__track", dot.closest(".swipe"));
  const at = Number(dot.dataset.swipeTo);
  track?.scrollTo({ left: at * track.clientWidth, behavior: reducedMotion.matches ? "auto" : "smooth" });
});

// Crossing the laptop width swaps the swipe for the open grid, and back.
laptop.addEventListener("change", () => {
  if (document.body.dataset.screen === "app" && els.dashDay.childElementCount) renderDay();
});

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

/** "today", "yesterday" or "on Thu, Nov 12", for sentences about the day on show. */
function dayPhrase(key) {
  const name = dayName(key);
  return key === state.todayKey ? "today" : name === "Yesterday" ? "yesterday" : `on ${name}`;
}

/** The Week tab's vitamins card is about the day picked in the chart (today unless another is). */
function microHtml(view) {
  const rows = nutrientRowsHtml(view.totals, view.daily_value_pct, { micro: true, prefix: "dm" });
  if (!rows) return "";
  return `
    <details class="card micro-card"${state.microOpen ? " open" : ""}>
      <summary><span class="micro-card__titles"><h2 class="micro-card__title">Vitamins and minerals</h2><span class="micro-card__day">${esc(capitalize(dayName(state.day)))}</span></span><span class="chip">Estimates</span></summary>
      <div class="micro-card__body">
        <p class="note">Photos can't show vitamins and minerals well, so these are rough estimates. A weekly trend says more than one day. Pick a day in the chart to see its own.</p>
        <ul class="nutrients">${rows}</ul>
        <p class="note">${DV_NOTE}</p>
      </div>
    </details>`;
}

function microEmptyHtml(key, logged) {
  return `
    <section class="card micro-card micro-card--empty" aria-labelledby="micro-empty-title">
      <div class="micro-card__titles"><h2 id="micro-empty-title" class="micro-card__title">Vitamins and minerals</h2><span class="micro-card__day">${esc(capitalize(dayName(key)))}</span></div>
      <p class="note">${logged ? `No vitamin or mineral estimates for the meals ${esc(dayPhrase(key))}.` : `Nothing logged ${esc(dayPhrase(key))}, so there's nothing to estimate.`} Pick another day in the chart.</p>
    </section>`;
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

/** One day of the week chart. A day that failed to load is never drawn as a day with no meals, and
    a logged day with nothing counted is never drawn as an empty one. */
function weekDay(key) {
  const entry = dayCache.get(key);
  const view = entry?.value ?? null;
  const logged = Boolean(view?.meals.length);
  const pending = !view && (!entry || Boolean(entry.pending));
  return {
    key,
    view,
    logged,
    kcal: logged ? view.totals.energy_kcal ?? null : null,
    pending,
    failed: !view && !pending && Boolean(entry?.error),
    error: entry?.error ?? null,
    version: entry?.version ?? 0,
  };
}

function renderWeek() {
  renderWeekSub();
  const days = lastDays(state.todayKey, 7).map(weekDay);
  const sig = `${state.day}|${days.map((d) => `${d.key}:${d.version}:${d.view ? "v" : d.failed ? "e" : "p"}`).join(",")}`;
  if (sig === rendered.week) return;
  // Some days in: the chart. Every day failed: say so, with Try again. Otherwise the skeleton.
  const mode = days.some((d) => d.view) ? "ready" : days.every((d) => d.failed) ? "error" : "loading";
  const entering = mode === "ready" && (!rendered.week || els.dashWeek.dataset.state !== "ready");
  rendered.week = sig;
  els.dashWeek.dataset.state = mode;
  // Try again redraws the card: keyboard focus stays on the heading meanwhile, then goes back to
  // Try again if the week still won't load.
  const active = document.activeElement;
  const keep = els.dashWeek.contains(active) && (active.id === "week-title" || active.matches('[data-action="retry"]'));
  const html = mode === "ready" ? weekHtml(days) : mode === "error" ? weekErrorHtml(days) : weekSkeletonHtml();
  redraw(els.dashWeek, html, "data-day");
  if (keep) ($('[data-action="retry"]', els.dashWeek) ?? $("#week-title", els.dashWeek))?.focus({ preventScroll: true });
  els.dashWeek.classList.toggle("is-entering", entering && !reducedMotion.matches);
}

function weekHead() {
  return `
    <div class="section-head">
      <h2 id="week-title" class="section-title" tabindex="-1">Calories by day</h2>
      <p class="eyebrow">Likely kcal and range</p>
    </div>`;
}

function renderWeekSub() {
  const days = lastDays(state.todayKey, 7);
  const first = parseDay(days[0]);
  const last = parseDay(days[6]);
  els.weekSub.textContent = `Last 7 days, ${monthDay.format(first)} to ${monthDay.format(last)}`;
}

function weekErrorHtml(days) {
  const error = days.find((d) => d.error)?.error;
  // The day on show failing for the same reason already says why, right above: don't repeat it.
  const shown = dayCache.get(state.day);
  const said = shown?.error && !shown.value && shown.error.message === error?.message;
  return `
    ${weekHead()}
    <div class="card week week--error">
      <p class="status is-error">Couldn't load this week.${error && !said ? ` ${esc(error.message)}` : ""}</p>
      <button class="btn btn--secondary" type="button" data-action="retry">Try again</button>
    </div>`;
}

function weekSkeletonHtml() {
  const bars = [0.55, 0.7, 0.35, 0.8, 0.6, 0.45, 0.65]
    .map((h) => `<li class="wk__day"><span class="wk__col is-skeleton"><span class="wk__plot"><span class="sk sk--bar" style="--h:${h}"></span></span><span class="sk sk--tick"></span></span></li>`)
    .join("");
  return `${weekHead()}<div class="card week" aria-hidden="true"><ol class="wk">${bars}</ol></div>`;
}

function weekHtml(days) {
  const counted = days.filter((d) => d.kcal);
  const top = niceTop(Math.max(1, ...counted.map((d) => d.kcal.high)));
  const cols = days.map((d, i) => weekColHtml(d, top, i)).join("");
  return `
    ${weekHead()}
    <div class="card week">
      <ol class="wk">${cols}</ol>
      ${weekFootHtml(days, counted)}
      <div class="wk__tip" aria-hidden="true" hidden></div>
    </div>`;
}

/** Under the chart: the average over the days it can honestly use, or why there isn't one. */
function weekFootHtml(days, counted) {
  const failed = days.filter((d) => d.failed).length;
  if (failed) {
    // An average without the missing days would quietly be about fewer days than it looks.
    return `
      <div class="wk__avg wk__avg--error">
        <p class="wk__error">Couldn't load ${failed} of 7 days, so there's no average.</p>
        <button class="btn btn--soft" type="button" data-action="retry">Try again</button>
      </div>`;
  }
  if (days.some((d) => d.pending)) {
    return '<div class="wk__avg" aria-hidden="true"><span class="sk sk--line"></span></div>';
  }
  const logged = days.filter((d) => d.logged).length;
  if (!logged) return '<div class="wk__avg"><p class="wk__avg-label">No meals logged in the last 7 days.</p></div>';
  const uncounted = logged - counted.length;
  const key = uncounted ? `<p class="wk__key">${icon("plate")}<span>Meals logged, calories not counted yet</span></p>` : "";
  if (!counted.length) {
    const which = logged === 1 ? "the logged day" : `the ${logged} logged days`;
    return `<div class="wk__avg"><p class="wk__avg-label">No calories counted yet on ${which}.</p>${key}</div>`;
  }
  // Only days with counted calories go in; a day with no meals is missing data, not a zero.
  const mean = (part) => counted.reduce((sum, d) => sum + d.kcal[part], 0) / counted.length;
  const n = counted.length;
  const label = uncounted ? `Average over ${n} of ${logged} logged days` : `Average over ${n} logged day${n === 1 ? "" : "s"}`;
  return `
    <div class="wk__avg">
      <p class="wk__avg-label">${label}</p>
      <p class="wk__avg-value">${rangeHtml({ low: mean("low"), likely: mean("likely"), high: mean("high") }, "kcal")}</p>
      ${key}
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
  let kind = " is-empty";
  if (d.kcal) {
    kind = "";
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
    // Meals, but no food with calories yet: a plate, not an empty slot.
    kind = " is-uncounted";
    plot = `<span class="wk__plot"><span class="wk__mark">${icon("plate")}</span></span>`;
    label = `${spoken}: meals logged, calories not counted`;
    tip = "Calories not counted";
    full = "Not counted";
  } else if (d.failed) {
    kind = " is-failed";
    plot = '<span class="wk__plot"><span class="wk__mark wk__mark--error"></span></span>';
    label = `${spoken}: couldn't load`;
    tip = "Couldn't load this day";
    full = "Couldn't load";
  } else if (d.pending) {
    kind = " is-empty is-pending";
    label = `${spoken}: loading`;
    tip = "Loading";
  }
  const cls = `wk__col${isToday ? " is-today" : ""}${kind}`;
  return `
    <li class="wk__day">
      <button class="${cls}" type="button" data-day="${d.key}"${current} aria-label="${esc(label)}" data-tip-day="${esc(dayFormat.format(date))}" data-tip="${esc(tip)}">
        ${plot}
        <span class="wk__name" aria-hidden="true">${name}</span>
        <span class="wk__val${d.kcal ? "" : " wk__val--note"}" aria-hidden="true"><span class="wk__val--short">${short}</span><span class="wk__val--full">${esc(full)}</span></span>
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

function dashClick(event) {
  const target = event.target;
  const pager = target.closest("[data-strip]");
  if (pager) {
    if (pager.getAttribute("aria-disabled") !== "true") showStripPage(strip.page + Number(pager.dataset.strip));
    return;
  }
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
    case "retry": {
      const inWeek = Boolean(action.closest("#dash-week"));
      for (const entry of dayCache.values()) if (entry.error) entry.stale = true;
      rendered.day = "";
      loadDashboard();
      // The card now shows it's loading and the button is gone: hold keyboard focus on its heading.
      if (!action.isConnected) (inWeek ? $("#week-title", els.dashWeek) : els.todayTitle)?.focus({ preventScroll: true });
      return;
    }
    case "locate-allow":
      return allowLocation(action);
    case "locate-later":
      return dismissLocateAsk();
    case "resend":
      return resendLink(action);
    case "demo-verify":
      return demoVerify(action);
    case "settings":
      return openSettings(action.dataset.section, action);
    case "retry-upcoming":
      state.upcoming = null;
      return loadUpcoming();
    case "retry-prefs":
      return retryPrefs(action);
  }
  const dish = target.closest("[data-dish]");
  if (dish) return openDish(dish.dataset.dish, dish);
  if (target.closest("[data-to-menu]")) {
    event.preventDefault();
    setView("menu");
    syncHistory();
    els.menuTitle.focus({ preventScroll: true });
    return;
  }
  const meal = target.closest("[data-meal]");
  if (meal) openMealFrom(meal);
}

els.todayView.addEventListener("click", dashClick);
els.weekView.addEventListener("click", dashClick);

els.dashMicro.addEventListener("toggle", (event) => (state.microOpen = event.target.open), true);
els.dashDay.addEventListener("toggle", (event) => event.target.matches(".score__how") && (state.scoreOpen = event.target.open), true);

function clearMealError() {
  $("[data-meal-error]", els.today)?.remove();
}

async function openMealFrom(button) {
  const id = button.dataset.meal;
  const epoch = state.epoch;
  clearMealError();
  state.scroll.list = window.scrollY;
  state.openedMeal = id;
  await withBusy(button, async () => {
    try {
      const result = await api(`/api/meals/${encodeURIComponent(id)}`);
      await photoReady(result.photo_url);
      if (epoch === state.epoch) openLogged(result); // never into someone else's session
    } catch (err) {
      if (err.signedOut || epoch !== state.epoch) return;
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

/** Load a logged meal's stored photo before the meal opens, so its card doesn't jump when the photo
    arrives. After a moment it opens anyway, and the photo fills in when it comes. */
function photoReady(url) {
  if (!url) return Promise.resolve();
  const img = new Image();
  img.src = url;
  return Promise.race([img.decode().catch(() => {}), new Promise((resolve) => setTimeout(resolve, 1000))]);
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

/** Say "we sent a link" only when mail really goes out: GET /api/status email_sending (SMTP fully set
    up on the server). A server that doesn't send that field falls back to the provider name, where
    "outbox" (the default) only queues mail and "smtp" wasn't sending yet. */
const NOT_SENDING = new Set(["outbox", "smtp"]);
const emailSending = () =>
  typeof state.emailSending === "boolean" ? state.emailSending : Boolean(state.emailProvider) && !NOT_SENDING.has(state.emailProvider);
const NOT_SWITCHED_ON = "Email sending isn't switched on yet";

function enableAccounts(status) {
  state.authRequired = true;
  state.emailProvider = status.email_provider ?? null;
  state.emailSending = typeof status.email_sending === "boolean" ? status.email_sending : null;
  state.localUser = false;
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
  const settingsOnly = !user && state.prefsOn && state.localUser; // no account, but settings to change
  els.accountButton.hidden = !(user || settingsOnly);
  els.railSettings.hidden = !(user || settingsOnly);
  els.accountButton.classList.toggle("avatar--gear", settingsOnly);
  document.body.dataset.account = user ? "on" : "off";
  if (settingsOnly) {
    els.accountButton.innerHTML = icon("gear"); // fixed markup, nothing from the server
    els.accountButton.setAttribute("aria-label", "Settings");
    return;
  }
  if (!user) return;
  els.accountButton.textContent = initials(user);
  els.accountButton.setAttribute("aria-label", `${state.prefsOn ? "Settings and account" : "Account"}, ${user.name || user.email}`);
}

// What was typed on the account screens (never a password) and a reset link's token live in this
// tab's sessionStorage, so a refresh, or iOS reloading the tab after a trip to a password manager,
// keeps the screen as it was.
const DRAFT_KEY = "mealscholar:account-draft";
const RESET_KEY = "mealscholar:reset-token";

const tabStore = {
  get(key) {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value == null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch {
      // storage blocked (private mode): the screen still works, it just won't survive a refresh
    }
  },
};

let authDraft = { email: "", name: "" };

function setAuthDraft(change) {
  authDraft = { ...authDraft, ...change };
  tabStore.set(DRAFT_KEY, JSON.stringify(authDraft));
}

function clearAuthDraft() {
  authDraft = { email: "", name: "" };
  tabStore.set(DRAFT_KEY, null);
}

function loadAuthDraft() {
  try {
    const saved = JSON.parse(tabStore.get(DRAFT_KEY) ?? "null");
    if (saved && typeof saved === "object") authDraft = { email: String(saved.email ?? ""), name: String(saved.name ?? "") };
  } catch {
    clearAuthDraft();
  }
}

function forgetResetToken() {
  state.resetToken = null;
  tabStore.set(RESET_KEY, null);
}

/** Close the sheets at once, with no animation and no focus return: the screen under them is going,
    and focus can't move to the next screen while a modal sheet is still animating out over it. */
function dropSheets() {
  if (els.sheet.open) {
    restoreFocus = false;
    sheetMessage = "";
    els.sheet.close();
  }
  if (els.account.open) {
    accountRestore = false;
    accountMessage = "";
    els.account.close();
  }
  if (els.panel.open) {
    panelRestore = false;
    els.panel.close();
  }
  window.MealScholarInstall?.close?.(true);
}

/** Leave the app for one of the account screens. */
function showAuth(screen, options = {}) {
  dropSheets();
  if (screen !== "reset") forgetResetToken(); // leaving Set a new password, however it happens
  document.body.dataset.screen = "auth";
  els.auth.hidden = false;
  els.auth.dataset.screen = screen;
  els.auth.innerHTML = AUTH_SCREENS[screen]({ ...authDraft, ...options });
  placeBands(els.auth);
  window.scrollTo(0, 0);
  replayEntrance(els.auth);
  // Why you are here (a session that ended) is read first; else the first empty field, or the heading.
  const notice = $(".notice", els.auth);
  const field = finePointer.matches ? $("input:not([value]), input[value='']", els.auth) : null;
  (notice ?? field ?? $("h1", els.auth))?.focus({ preventScroll: true });
}

/** Signed-out screens with their own history entry, stacked on Welcome. Set a new password and
    Confirm email open from a link and show on the base entry. */
const STACKED = new Set(["signin", "register", "forgot"]);

/** A link between account screens. One already further down the stack is stepped back to, so the
    back button and the screen's own Back agree. */
function goAuth(screen) {
  if (screen !== "welcome" && !STACKED.has(screen)) return showAuth(screen);
  const path = state.authPath;
  const at = path.indexOf(screen);
  state.authPath = screen === "welcome" ? [] : at >= 0 ? path.slice(0, at + 1) : [...path, screen];
  showAuth(screen);
  syncHistory();
}

/** Back or forward landed on an account screen's entry. */
function showAuthPath(path) {
  state.authPath = path.filter((screen) => STACKED.has(screen));
  showAuth(state.authPath.at(-1) ?? "welcome");
}

/** Into the app: the home screen, with this account's data. */
function enterApp({ focus = false } = {}) {
  forgetResetToken();
  document.body.dataset.screen = "app";
  els.auth.hidden = true;
  els.auth.innerHTML = "";
  els.onboard.hidden = true;
  els.onboard.innerHTML = "";
  state.onboard = null;
  state.authPath = [];
  updateAccountUi();
  updateBell();
  updatePrefsUi();
  renderBanner();
  restoreLocation();
  // A link to #menu or #week (or a refresh on one) opens that tab, once, above Today.
  const route = bootRoute && canShow(bootRoute) ? bootRoute : "today";
  bootRoute = null;
  state.view = route;
  document.body.dataset.view = route;
  syncTabs();
  syncResult();
  syncHistory(); // the account screens' entries collapse into the app's home entry
  window.scrollTo(0, 0);
  if (!state.nutrients.length) {
    loadNutrients().then(() => {
      rendered.day = "";
      renderDay();
    });
  }
  loadDashboard();
  if (route === "menu") ensureMenus();
  renderLocateAsk();
  if (focus) viewTitle(route)?.focus({ preventScroll: true });
}

/** Forget everything that belongs to the account that was signed in, including work still in
    flight: an analysis is called off, and late replies are dropped (see state.epoch). */
function resetData() {
  state.epoch += 1;
  dropSheets();
  stopAnalysis();
  state.dashSeq += 1;
  dayCache.clear();
  rendered.day = "";
  rendered.week = "";
  discardSlot("analyze");
  discardSlot("today");
  state.shown = null;
  state.open = null;
  state.openedMeal = null;
  state.dismissedQuestion = null;
  state.day = null;
  state.todayKey = null;
  state.microOpen = true;
  state.scoreOpen = false;
  state.scroll = { analyze: 0, today: 0, menu: 0, week: 0, list: 0 };
  state.view = "today";
  document.body.dataset.view = "today";
  // Sharing location is each person's own choice: the next account starts with it off.
  els.location.checked = false;
  setStatus("", { target: els.locationStatus });
  placeTools();
  els.result.innerHTML = "";
  els.result.hidden = true;
  delete document.body.dataset.result;
  els.locateAsk.hidden = true;
  els.locateAsk.innerHTML = "";
  resetMenus();
  els.dashDay.innerHTML = "";
  els.dashWeek.innerHTML = "";
  els.dashMicro.innerHTML = "";
  els.dashNotice.innerHTML = "";
  els.today.dataset.state = "loading";
  els.dashWeek.dataset.state = "loading";
  // The preferences layer belongs to the account too.
  state.prefsOn = false;
  state.prefs = null;
  state.prefsError = null;
  state.prefsVersion += 1;
  state.dining = null;
  state.alerts = [];
  state.nearest = null;
  state.upcoming = null;
  state.onboard = null;
  upcomingSeq += 1;
  delete document.body.dataset.prefs;
  delete document.body.dataset.dining;
  els.dashSide.innerHTML = "";
  els.dashRecent.innerHTML = "";
  els.strip.hidden = true;
  els.strip.innerHTML = "";
  els.onboard.hidden = true;
  els.onboard.innerHTML = "";
  updateBell();
}

/** Into the app as this account, by way of the set-up steps the first time. */
async function signedIn(user, message) {
  resetData();
  state.user = user;
  clearAuthDraft();
  await setupPrefs();
  if (needsOnboarding()) startOnboarding();
  else enterApp({ focus: true });
  if (message) toast(message);
}

/** Signed out (on purpose, or the session ended): an account screen, with this account's data gone. */
function leaveAccount(screen, options = {}) {
  resetData();
  state.user = null;
  state.demoVerifyUrl = null;
  state.resendUntil = 0;
  updateAccountUi();
  renderBanner();
  state.authPath = STACKED.has(screen) ? [screen] : [];
  showAuth(screen, options);
  syncHistory();
}

/** The sign-in ended (a 401 from a meal or day call): back to the sign-in screen. */
function sessionEnded({ notice = "Your session ended. Sign in again to continue." } = {}) {
  if (document.body.dataset.screen === "auth") return;
  if (state.user?.email) setAuthDraft({ email: state.user.email });
  leaveAccount("signin", { notice });
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

const EMAIL_NOTE = `<p class="auth-note">${icon("info")}<span>${NOT_SWITCHED_ON}, so the link may not arrive.</span></p>`;

function sampleHtml() {
  return `
    <div class="card welcome__sample" aria-hidden="true">
      <p class="eyebrow">Example</p>
      <p class="welcome__sample-title">Lunch</p>
      <div class="total">
        <p class="eyebrow">Calories, this meal</p>
        <p class="total__value"><span class="total__num">520</span><span class="total__unit">kcal</span></p>
        <p class="total__range">Most likely, in a range of <strong>407–655&nbsp;kcal</strong></p>
        ${bandHtml("sample-kcal", { low: 407, likely: 520, high: 655 }, { dark: true })}
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
        <p class="lede">Photograph a meal to see its foods, how much is there and its nutrients, as honest ranges instead of one fake-precise number.</p>
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
        <p class="eyebrow">Account</p>
        <h1 class="view-title" tabindex="-1">Sign in</h1>
        <p class="lede">Welcome back. Your diary is where you left it.</p>
      </header>
      ${notice ? `<p class="notice" role="status" tabindex="-1">${icon("info")}<span>${esc(notice)}</span></p>` : ""}
      <form class="card form" data-form="signin" novalidate>
        ${fieldHtml({ id: "signin-email", name: "email", label: "Email", type: "email", autocomplete: "email", value: email })}
        ${passwordFieldHtml({ id: "signin-password", name: "password", label: "Password", autocomplete: "current-password" })}
        <p class="form__aside"><button class="link-btn" type="button" data-auth="forgot">Forgot password?</button></p>
        <p class="form-error" role="alert" hidden></p>
        <button class="btn btn--primary btn--block" type="submit">Sign in</button>
      </form>
      <p class="auth-switch">New to MealScholar? <button class="link-btn" type="button" data-auth="register">Create an account</button></p>
    </div>`,

  register: ({ email = "", name = "" } = {}) => `
    <div class="auth-screen">
      ${backHtml("welcome", "Back")}
      <header class="auth-head">
        <p class="eyebrow">Account</p>
        <h1 class="view-title" tabindex="-1">Create your account</h1>
        <p class="lede">Your meals go into your own private diary.</p>
      </header>
      <form class="card form" data-form="register" novalidate>
        ${fieldHtml({ id: "register-name", name: "name", label: "Name", autocomplete: "name", value: name, optional: true })}
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
      const address = email ? `<strong>${esc(email)}</strong>` : "that email";
      return `
        <div class="auth-screen">
          ${backHtml("signin", "Sign in")}
          <header class="auth-head"><p class="eyebrow">Password</p><h1 class="view-title" tabindex="-1">Check your email</h1></header>
          <div class="card sent" role="status">
            <span class="sent__icon" aria-hidden="true">${icon("mail")}</span>
            <p>If there's an account for ${address}, ${emailSending() ? "we sent it a link" : "we made a link"} to set a new password. It works for 1 hour.</p>
          </div>
          ${note}
          <button class="btn btn--secondary btn--block" type="button" data-auth="signin">Back to sign in</button>
        </div>`;
    }
    return `
      <div class="auth-screen">
        ${backHtml("signin", "Sign in")}
        <header class="auth-head">
          <p class="eyebrow">Password</p>
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
        <p class="eyebrow">Password</p>
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

  verify: ({ phase = "pending", message = "", confirmed = "" } = {}) => {
    if (phase === "pending") {
      return `
        <div class="auth-screen">
          <header class="auth-head">
            <p class="eyebrow">Email</p>
            <h1 class="view-title" tabindex="-1">Confirming your email</h1>
            <p class="lede auth-wait"><span class="spinner" aria-hidden="true"></span>One moment.</p>
          </header>
        </div>`;
    }
    const next = state.user ? "app" : "signin";
    const label = state.user ? "Continue" : "Sign in";
    if (phase === "done") {
      // Signed in as someone else: say whose email the link confirmed, and who is still signed in.
      const address = confirmed ? `<strong>${esc(confirmed)}</strong>` : "";
      const lede = state.user
        ? `That link confirmed ${address || "another account's email"}. You're still signed in as <strong>${esc(state.user.email)}</strong>.`
        : address
          ? `Thanks. ${address} is confirmed.`
          : "Thanks. Your account is all set.";
      return `
        <div class="auth-screen">
          <header class="auth-head">
            <p class="eyebrow">Email</p>
            <h1 class="view-title" tabindex="-1">Email confirmed</h1>
            <p class="lede">${lede}</p>
          </header>
          <button class="btn btn--primary btn--block" type="button" data-auth="${next}">${label}</button>
        </div>`;
    }
    return `
      <div class="auth-screen">
        <header class="auth-head">
          <p class="eyebrow">Email</p>
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
    error.scrollIntoView({ block: "nearest" }); // the message sits under the field: in view with it
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
  const button = $('button[type="submit"]', form);
  // A disabled button stops taps and Enter, but not a script's requestSubmit(): one request at a time.
  if (button?.getAttribute("aria-busy") === "true") return;
  const data = Object.fromEntries(new FormData(form));
  clearFormErrors(form);
  if (!checkForm(form, kind, data)) return;
  const post = (path, body, options = {}) => api(path, { method: "POST", ...jsonBody(body), ...options });
  await withBusy(button, async () => {
    try {
      if (kind === "signin") {
        const { user } = await post("/api/auth/login", { email: data.email.trim(), password: data.password });
        const name = firstName(user);
        await signedIn(user, name ? `Welcome back, ${name}.` : "Welcome back.");
      } else if (kind === "register") {
        const name = data.name.trim();
        const reply = await post("/api/auth/register", { email: data.email.trim(), password: data.password, ...(name ? { name } : {}) });
        state.demoVerifyUrl = reply.demo_verify_url ?? null;
        await signedIn(reply.user, "Account created.");
      } else if (kind === "forgot") {
        await post("/api/auth/forgot-password", { email: data.email.trim() });
        showAuth("forgot", { email: data.email.trim(), sent: true });
      } else if (kind === "reset") {
        const { user } = await post("/api/auth/reset-password", { token: state.resetToken ?? "", password: data.password });
        forgetResetToken();
        await signedIn(user, "New password saved. You're signed in.");
      } else if (kind === "change-password") {
        const body = { current_password: data.current_password, new_password: data.new_password };
        const { user } = await post("/api/auth/change-password", body, { session: true });
        if (user) state.user = user;
        closeAccount({ message: "Password changed." });
      }
    } catch (err) {
      if (kind === "reset" && err.field === "token") forgetResetToken(); // a dead link doesn't come back on refresh
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
    return goAuth(nav.dataset.auth); // the email typed so far comes along (authDraft)
  }
  const toggle = event.target.closest('[data-action="toggle-password"]');
  if (toggle) togglePassword(toggle);
});

els.auth.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAccountForm(event.target);
});

els.auth.addEventListener("input", (event) => {
  clearFieldError(event.target);
  const { name, value } = event.target;
  if (name === "email" || name === "name") setAuthDraft({ [name]: value.trim() });
});

/** ?verify=TOKEN: confirm, then carry on into the app when it was the signed-in account's own link. */
async function confirmEmail(token) {
  showAuth("verify", { phase: "pending" });
  try {
    const { user } = await api("/api/auth/verify-email", { method: "POST", ...jsonBody({ token }) });
    if (state.user && user?.id === state.user.id) {
      state.user = user;
      enterApp();
      toast("Email confirmed. Thanks.");
      return;
    }
    // Signed out, or another account's link: a screen that says whose email it was.
    showAuth("verify", { phase: "done", confirmed: user?.email ?? "" });
  } catch (err) {
    showAuth("verify", { phase: "failed", message: err.message });
  }
}

// ---- the unconfirmed-email banner and its actions

/** Seconds until Resend link works again: a minute after each send, or what a 429 said. */
const resendWait = () => Math.max(0, Math.ceil((state.resendUntil - Date.now()) / 1000));
const resendLabel = (wait = resendWait()) =>
  !wait ? "Resend link" : wait <= 60 ? `Resend in ${wait}s` : `Resend in ${Math.ceil(wait / 60)} min`;
let resendTimer = null;

/** Counts the Resend buttons down in place (aria-disabled, so focus stays on the button). */
function startResendCooldown(seconds) {
  state.resendUntil = Date.now() + seconds * 1000;
  clearInterval(resendTimer);
  const tick = () => {
    const wait = resendWait();
    for (const button of $$('[data-action="resend"]')) {
      $(".resend__label", button).textContent = resendLabel(wait);
      if (wait) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
    }
    if (!wait) {
      clearInterval(resendTimer);
      resendTimer = null;
    }
  };
  tick();
  resendTimer = setInterval(tick, 1000);
}

function confirmActionsHtml() {
  const wait = resendWait();
  return `
    <button class="btn btn--soft" type="button" data-action="resend"${wait ? ' aria-disabled="true"' : ""}>${icon("mail")}<span class="resend__label">${resendLabel(wait)}</span></button>
    ${state.demoVerifyUrl ? `<button class="btn btn--primary" type="button" data-action="demo-verify">${icon("check")}Confirm now (demo)</button>` : ""}`;
}

function renderBanner() {
  const user = state.user;
  const notice = state.prefsError ? prefsNoticeHtml(state.prefsError) : "";
  if (!state.authRequired || !user || user.email_verified) {
    els.banner.innerHTML = notice;
    return;
  }
  const email = `<strong>${esc(user.email)}</strong>`;
  els.banner.innerHTML = `
    <div class="banner" data-confirm="banner">
      <span class="banner__icon" aria-hidden="true">${icon("mail")}</span>
      <div class="banner__text">
        <p class="banner__title">Confirm your email</p>
        <p>${emailSending() ? `We sent a link to ${email}. Open it to confirm.` : `${NOT_SWITCHED_ON}, so the link for ${email} may not arrive.`}</p>
      </div>
      <div class="banner__actions">${confirmActionsHtml()}</div>
      <p class="banner__status" role="status"></p>
    </div>${notice}`;
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

const RESEND_COOLDOWN = 60;

async function resendLink(button) {
  const where = button.closest("[data-confirm]")?.dataset.confirm;
  if (resendWait()) return; // aria-disabled: still focusable, so say nothing new and send nothing
  await withBusy(button, async () => {
    try {
      const reply = await api("/api/auth/resend-verification", { method: "POST", session: true });
      if (reply?.demo_verify_url) state.demoVerifyUrl = reply.demo_verify_url;
      startResendCooldown(RESEND_COOLDOWN);
      const said = emailSending()
        ? "New link sent. It works for 24 hours."
        : state.demoVerifyUrl
          ? `New link ready. ${NOT_SWITCHED_ON}, so use Confirm now (demo).`
          : `New link made. ${NOT_SWITCHED_ON}, so it may not arrive.`;
      confirmStatus(refreshConfirmUi(where, "resend"), said);
    } catch (err) {
      if (err.signedOut) return;
      if (err.status === 409) {
        await refreshUser();
        refreshConfirmUi();
        (els.account.open ? els.accountTitle : els.todayTitle).focus({ preventScroll: true });
        toast("Your email is already confirmed.");
        return;
      }
      if (err.status === 429 && err.retryAfter) startResendCooldown(err.retryAfter);
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
// With the preferences layer it grows into Settings (see renderSettingsBody); without it, the
// account sheet is as it was.

function renderAccountBody() {
  if (state.prefsOn) return renderSettingsBody();
  const user = state.user;
  if (!user) return;
  els.accountKicker.textContent = "Account";
  els.accountTitle.textContent = user.name || "Your account";
  els.accountIntro.textContent = "Your sign-in, your password and how the app looks.";
  els.accountIntro.hidden = false;
  els.accountBody.innerHTML = `
    ${accountIdHtml(user)}
    ${appearanceSectionHtml()}
    ${passwordSectionHtml(user)}
    <p class="account__status status" role="status"></p>
    <button class="btn btn--soft btn--block" type="button" data-install${installable() ? "" : " hidden"}>${icon("home")}Add to Home Screen</button>
    <button class="btn btn--secondary btn--block" type="button" data-action="sign-out">${icon("logout")}Sign out</button>
    ${dataSectionHtml(user)}`;
}

/** Add to Home Screen (install.js) shows only where it can work: after the browser offers an
    install, or in Safari on an iPhone or iPad, and never once the app runs from the Home Screen. */
const installable = () => Boolean(window.MealScholarInstall?.available?.());

/** Opens the account sheet. `opener` is the control that asked for it (or its click event), so
    closing the sheet can hand focus back there instead of always to the avatar in the bar. */
function openAccount(opener) {
  if (!state.user && !(state.prefsOn && state.localUser)) return;
  accountOpener =
    opener instanceof Element ? opener : opener?.currentTarget instanceof Element ? opener.currentTarget : document.activeElement;
  clearTimeout(accountTimer);
  els.account.classList.remove("is-closing");
  els.account.style.transform = "";
  accountMessage = "";
  renderAccountBody();
  document.documentElement.classList.add("has-sheet");
  if (!els.account.open) els.account.showModal();
  els.accountTitle.focus();
}

/** Settings, scrolled to one of its sections (goals, dining, diet, favorites, alerts). */
function openSettings(section, opener) {
  openAccount(opener);
  if (!section || !els.account.open) return;
  const target = $(`#settings-${CSS.escape(section)}`, els.account);
  if (!target) return;
  // Scroll the sheet body only: scrollIntoView would also scroll the <dialog> itself and push
  // its header and Close button out of view.
  const body = $(".sheet__body", els.account);
  if (body) {
    const top = target.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
    body.scrollTo({ top: Math.max(0, top - 8), behavior: reducedMotion.matches ? "auto" : "smooth" });
  }
  els.account.scrollTop = 0;
  $(".account__section-title", target)?.focus({ preventScroll: true });
}

let accountTimer = 0;
let accountRestore = true;
let accountOpener = null;
let accountMessage = "";
let pressedAccountBackdrop = false;

/** Still on the page and showing, so focus can go back to it. */
const canTakeFocus = (el) =>
  el instanceof Element && el.isConnected && !el.closest("[hidden], dialog:not([open])") && (el.checkVisibility?.() ?? true);

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
  if (accountRestore) {
    const back = canTakeFocus(accountOpener) ? accountOpener : els.accountButton;
    if (!back.hidden) back.focus();
  }
  accountOpener = null;
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
    case "ask-data":
      return askData(button);
    case "cancel-data":
      return cancelData(button);
    case "clear-history":
      return clearHistory(button);
    case "erase-data":
      return eraseData(button);
  }
  prefsClick(event); // the Settings widgets (chips, favorites, nearest hall)
});

els.account.addEventListener("submit", (event) => {
  event.preventDefault();
  if (event.target.dataset.form === "goals") return saveGoalsForm(event.target);
  if (event.target.dataset.form === "delete-account") return deleteAccount(event.target);
  submitAccountForm(event.target);
});

els.account.addEventListener("input", (event) => {
  clearFieldError(event.target);
  prefsInput(event);
});
els.account.addEventListener("change", prefsChange);

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
    leaveAccount("welcome"); // the sheet closes at once, so focus can land on Welcome
    toast("You're signed out.");
  });
}

// ---- Your data: clear history, delete everything, delete the account

function askData(button) {
  const panel = document.getElementById(button.dataset.ask);
  if (!panel) return;
  for (const other of $$(".data-ask", els.account)) if (other !== panel) closeDataAsk(other);
  panel.hidden = false;
  button.setAttribute("aria-expanded", "true");
  const first = $("input:not(.visually-hidden)", panel) ?? $(".btn--danger", panel);
  first?.focus({ preventScroll: true });
  panel.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
}

function closeDataAsk(panel) {
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  $(`[data-ask="${CSS.escape(panel.id)}"]`, els.account)?.setAttribute("aria-expanded", "false");
  if (panel.tagName === "FORM") {
    clearFormErrors(panel);
    panel.reset();
  }
}

function cancelData(button) {
  const panel = button.closest(".data-ask");
  closeDataAsk(panel);
  $(`[data-ask="${CSS.escape(panel.id)}"]`, els.account)?.focus();
}

const dataStatus = () => $("[data-data-status]", els.account);

async function clearHistory(button) {
  await withBusy(button, async () => {
    let erased;
    try {
      erased = await api("/api/meals", { method: "DELETE" });
    } catch (err) {
      if (!err.signedOut) setStatus(err.message, { error: true, target: dataStatus() });
      return;
    }
    closeDataAsk(button.closest(".data-ask"));
    // Nothing logged is left: close any open meal and fetch every day again.
    for (const key of Object.keys(state.slots)) if (state.slots[key]?.result?.id != null) closeSlot(key);
    state.openedMeal = null;
    dayCache.clear();
    loadDashboard();
    syncHistory();
    $('[data-ask="data-clear"]', els.account)?.focus();
    setStatus(erased.meals === 1 ? "Deleted 1 meal." : `Deleted ${erased.meals} meals.`, { target: dataStatus() });
  });
}

async function eraseData(button) {
  await withBusy(button, async () => {
    try {
      await api("/api/me", { method: "DELETE" });
    } catch (err) {
      if (!err.signedOut) setStatus(err.message, { error: true, target: dataStatus() });
      return;
    }
    startOver("Your data is deleted. MealScholar starts fresh.");
  });
}

async function deleteAccount(form) {
  const button = $('button[type="submit"]', form);
  if (button?.getAttribute("aria-busy") === "true") return;
  clearFormErrors(form);
  const input = form.elements.namedItem("password");
  if (!input.value) {
    setFieldError(input, "Enter your password.");
    input.focus();
    return;
  }
  await withBusy(button, async () => {
    try {
      await api("/api/auth/delete-account", { method: "POST", ...jsonBody({ password: input.value }), session: true });
    } catch (err) {
      showFormError(form, err);
      return;
    }
    startOver("Your account and its data are deleted.");
  });
}

/** Reload from the top, so nothing from before stays in memory; the message shows after. */
function startOver(message) {
  try {
    sessionStorage.setItem("ms_notice", message);
  } catch {
    // private mode: the reload just has no message
  }
  location.replace(location.pathname);
}

function showStartOverNotice() {
  let message = null;
  try {
    message = sessionStorage.getItem("ms_notice");
    sessionStorage.removeItem("ms_notice");
  } catch {
    return;
  }
  if (message) toast(message);
}

// --------------------------------------------------------------- history
// Every screen above the home screen has a history entry, so the Android back gesture and the
// browser's back button step back through the app instead of leaving it. Home is Today; Menu and
// Week sit one step above it (#menu, #week), and so do a logged meal and the capture screen (a
// photo being read, then its result). Switching tabs replaces that step, so back from any tab is
// Today, and back from Today leaves the app. Signed out,
// home is Welcome and Sign in, Create account and Forgot password stack on it ({ auth: [...] }
// entries). Signing in or out folds the old stack back into one home entry, so back from the
// dashboard never returns to a sign-in form.

// "install" belongs to install.js (the Home Screen sheet); it never stays in the address bar.
const _bootParams = new URLSearchParams(location.search);
_bootParams.delete("install");
let bootRoute = null; // the tab a #hash asked for at start, taken by enterApp
let baseUrl = location.pathname + (_bootParams.toString() ? "?" + _bootParams.toString() : "");

/** The screens stacked above Today right now. */
function layers() {
  if (document.body.dataset.screen !== "app") return [];
  if (state.view === "today") return state.slots.today ? ["meal"] : [];
  return [state.view];
}

const HASH = { menu: "#menu", week: "#week" };
const urlFor = (list) => `${baseUrl}${HASH[list[0]] ?? ""}`;
/** The tab a #hash names, if any: #today, #menu or #week. */
const hashView = () => (isTab(location.hash.slice(1)) ? location.hash.slice(1) : null);
const entryLayers = (entry) => (Array.isArray(entry?.layers) ? entry.layers : []);
const isAuthEntry = (entry) => Array.isArray(entry?.auth);

/** What the screen on show needs above its home entry: app layers, or signed-out account screens.
    A signed-in person on a link screen (Set a new password, Confirm email) stays on the app's home. */
function wantedHistory() {
  if (document.body.dataset.screen === "auth" && !state.user) return { kind: "auth", path: state.authPath };
  return { kind: "app", path: layers() };
}

const entryFor = (kind, path) => (kind === "auth" ? { auth: path } : { layers: path });

let historyTask = Promise.resolve();
let ownTraversal = null;

/** After the app navigates by itself, make the history entries match the screen. */
function syncHistory() {
  historyTask = historyTask.then(stepHistory, stepHistory);
}

async function stepHistory() {
  const want = wantedHistory();
  const entry = history.state;
  const kind = isAuthEntry(entry) ? "auth" : "app";
  const have = kind === "auth" ? entry.auth : entryLayers(entry);
  let same = 0;
  if (kind === want.kind) {
    while (same < want.path.length && same < have.length && want.path[same] === have[same]) same += 1;
  }
  if (have.length > same) await traverse(same - have.length);
  // Signed in or out: the home entry changes hands.
  if (kind !== want.kind) history.replaceState(entryFor(want.kind, []), "", baseUrl);
  for (let i = same; i < want.path.length; i += 1) {
    const list = want.path.slice(0, i + 1);
    history.pushState(entryFor(want.kind, list), "", want.kind === "app" ? urlFor(list) : baseUrl);
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
  const screen = document.body.dataset.screen;
  const entry = event.state;
  if (screen === "auth") {
    if (state.user) return enterApp({ focus: true }); // back from a link screen while signed in
    if (isAuthEntry(entry)) return showAuthPath(entry.auth);
    return syncHistory(); // an entry from before signing out: stay on the account screens
  }
  if (screen !== "app") return;
  if (isAuthEntry(entry)) return syncHistory(); // a sign-in form from before signing in: stay in the app
  // A #today, #menu or #week typed into the address bar (or a link to one): a new entry with no state.
  if (!entry && hashView()) {
    const view = canShow(hashView()) ? hashView() : "today";
    applyLayers(view === "today" ? [] : [view]);
    return;
  }
  applyLayers(entryLayers(entry));
});

/** The user went back (or forward): show the screens of the entry they landed on. */
function applyLayers(target) {
  const current = layers();
  const dropped = (name) => current.includes(name) && !target.includes(name);
  if (els.sheet.open) closeSheet({ restoreFocus: false });
  if (els.account.open) closeAccount({ restoreFocus: false });
  if (els.panel.open) closePanel({ restoreFocus: false });
  if (dropped("meal")) closeSlot("today", { focus: true });
  const top = target[0];
  // The capture screen only comes back while it has something to show.
  const view = top === "analyze" ? (state.analyzing || state.slots.analyze ? "analyze" : "today") : canShow(top) ? top : "today";
  setView(view);
  const now = layers();
  if (now.join() !== target.join()) history.replaceState({ layers: now }, "", urlFor(now));
  // Focus that was left in a view that is now hidden goes to the view's tab.
  const active = document.activeElement;
  if (active && active !== document.body && !active.getClientRects().length) (visibleTab() ?? viewTitle(state.view))?.focus({ preventScroll: true });
}

// ------------------------------------------------------------ preferences
// Goals, dining halls, diet and allergens, favorite dishes and alerts (prefs.py), and the campus
// menus (dining.py). All of it exists only once GET /api/me/prefs answers 200: for the signed-in
// account, or for the server's one "local" user when sign-in is optional. A server without those
// routes (404) leaves the app as it was; one without the menus (503 from /api/dining/*) keeps the
// goals and hides the dining parts.

const DIETS = ["Halal", "Local", "Plant Based", "Vegetarian"];
const ALLERGENS = ["Corn", "Milk", "Soy", "Gluten", "Wheat", "Eggs", "Sesame", "Fish", "Tree Nuts", "Shellfish", "Peanuts"];
const GOAL_FIELDS = [
  ["protein_g", "Protein", "g", 400],
  ["carbs_g", "Carbs", "g", 1000],
  ["fat_g", "Fat", "g", 400],
  ["fiber_g", "Fiber", "g", 150],
  ["sugars_g", "Sugar", "g", 400],
  ["sodium_mg", "Sodium", "mg", 10000],
];
const REFERENCE_KCAL = 2000;
const [GOAL_MIN, GOAL_MAX] = [500, 6000];
const MAX_FAVORITES = 50;

const diningOn = () => state.prefsOn && Boolean(state.dining);
/** A calorie goal is set: the dashboard shows what's left against it. */
const goalsMode = () => state.prefsOn && Number.isFinite(state.prefs?.calorie_goal) && state.prefs.calorie_goal > 0;
const nutrientGoal = (key) => {
  const value = state.prefs?.nutrient_goals?.[key];
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
};
const needsOnboarding = () => state.prefsOn && state.prefs?.onboarded !== true;
const hallNameById = (id) => state.dining?.locations?.find((l) => l.id === id)?.name ?? null;

/** A dish name without case, width, extra spaces or curly quotes, as prefs.dish_key does. */
const dishKey = (name) =>
  String(name)
    .normalize("NFKC")
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[\u2013\u2014]/g, "-")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");

/** The preferences, or null when this server keeps none (a 404: today's live server). A server
    or network failure is tried once more; if it still fails, the app runs without them and the
    dashboard says so, with a way to try again (see prefsNoticeHtml). */
async function fetchPrefs() {
  const epoch = state.epoch;
  state.prefsError = null;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const prefs = await api("/api/me/prefs", { session: state.authRequired });
      return prefs && typeof prefs === "object" && !Array.isArray(prefs) ? normalizePrefs(prefs) : null;
    } catch (err) {
      if (epoch !== state.epoch || err.signedOut || err.status === 404 || err.status === 401) return null;
      if (attempt === 0 && (err.status === 0 || err.status >= 500)) {
        await new Promise((resolve) => setTimeout(resolve, 800));
        if (epoch !== state.epoch) return null;
        continue;
      }
      state.prefsError = err;
      return null;
    }
  }
}

/** The dashboard's notice when the preferences didn't load: the plain app is running. */
function prefsNoticeHtml(err) {
  return `
    <div class="banner" data-prefs-notice>
      <span class="banner__icon" aria-hidden="true">${icon("gear")}</span>
      <div class="banner__text">
        <p class="banner__title">Couldn't load your settings</p>
        <p>${esc(err.message)} Your goals, dining halls and alerts are off for now.</p>
      </div>
      <div class="banner__actions"><button class="btn btn--soft" type="button" data-action="retry-prefs">Try again</button></div>
    </div>`;
}

/** Try again from the notice. When the preferences arrive, the app starts over with them on,
    by way of the set-up steps if they were never finished. */
async function retryPrefs(button) {
  const epoch = state.epoch;
  await withBusy(button, async () => {
    const prefs = await fetchPrefs();
    if (epoch !== state.epoch) return;
    if (!prefs) {
      renderBanner(); // the notice again, with the new reason
      $('[data-action="retry-prefs"]', els.banner)?.focus({ preventScroll: true });
      return;
    }
    if (!state.authRequired) state.localUser = true;
    enablePrefs(prefs);
    await loadDining();
    if (epoch !== state.epoch) return;
    loadAlerts();
    renderBanner(); // the notice goes
    if (needsOnboarding()) startOnboarding();
    else enterApp({ focus: true });
  });
}

/** Only the shapes the app relies on; the server has the last word on every save. */
function normalizePrefs(prefs) {
  const list = (value) => (Array.isArray(value) ? value : []);
  const goals = prefs.nutrient_goals && typeof prefs.nutrient_goals === "object" ? prefs.nutrient_goals : {};
  return {
    onboarded: prefs.onboarded === true,
    calorie_goal: Number.isInteger(prefs.calorie_goal) ? prefs.calorie_goal : null,
    nutrient_goals: goals,
    dining_halls: list(prefs.dining_halls).filter(Number.isInteger),
    diets: list(prefs.diets).filter((d) => typeof d === "string"),
    avoid_allergens: list(prefs.avoid_allergens).filter((a) => typeof a === "string"),
    favorites: list(prefs.favorites).filter((f) => f && typeof f.name === "string"),
    notify: { in_app: prefs.notify?.in_app !== false, email: prefs.notify?.email === true },
    updated_at: typeof prefs.updated_at === "string" ? prefs.updated_at : null,
  };
}

function enablePrefs(prefs) {
  state.prefsOn = true;
  state.prefs = prefs;
  state.prefsVersion += 1;
  document.body.dataset.prefs = "on";
  updatePrefsUi();
}

/** Preferences, then the menus and alerts, for the account that just signed in. */
async function setupPrefs() {
  const prefs = await fetchPrefs();
  if (!prefs) return false;
  enablePrefs(prefs);
  await loadDining();
  loadAlerts();
  return true;
}

async function loadDining() {
  const epoch = state.epoch;
  try {
    const locations = await api("/api/dining/locations");
    if (epoch !== state.epoch) return;
    const usable = Array.isArray(locations) ? locations.filter((l) => l && Number.isInteger(l.id) && typeof l.name === "string") : [];
    state.dining = { locations: usable };
  } catch {
    if (epoch !== state.epoch) return;
    state.dining = false; // 503: the menus aren't connected; any other failure reads the same for now
  }
  updatePrefsUi();
}

/** The menus went away mid-session (a 503): the dining parts fold away. */
function diningLost() {
  if (!state.dining) return;
  state.dining = false;
  state.alerts = [];
  state.upcoming = null;
  resetMenus();
  updatePrefsUi();
  renderUpcoming();
  renderMenu();
}

const FOOTER_PLAIN = "Information, not medical advice. Any goal is one you set yourself; no insulin or carb dosing advice.";
const FOOTER_PREFS = "Information, not medical advice. A goal here is one you set yourself; no insulin or carb dosing advice.";

function updatePrefsUi() {
  if (state.prefsOn) document.body.dataset.dining = diningOn() ? "on" : "off";
  else delete document.body.dataset.dining;
  els.plus.hidden = false;
  els.footerNote.textContent = state.prefsOn ? FOOTER_PREFS : FOOTER_PLAIN;
  updateBell();
  // The menus went away (or were never there): the Menu tab folds away, and so does its page.
  if (!diningOn() && state.view === "menu" && document.body.dataset.screen === "app") {
    setView("today");
    syncHistory();
  }
}

/** PATCH a subset; the reply is the whole document. The parts of the app that use what changed
    follow: the calorie panel and tiles, the upcoming meal, the bell. */
async function savePrefs(changes) {
  const epoch = state.epoch;
  const reply = await api("/api/me/prefs", { method: "PATCH", ...jsonBody(changes) });
  if (epoch !== state.epoch || !state.prefsOn) return reply;
  const changed = Object.keys(changes);
  const touched = (...keys) => keys.some((k) => changed.includes(k));
  state.prefs = normalizePrefs(reply);
  state.prefsVersion += 1;
  if (document.body.dataset.screen === "app") {
    if (touched("calorie_goal", "nutrient_goals")) renderDay();
    if (touched("dining_halls", "favorites", "diets", "avoid_allergens")) {
      state.upcoming = null;
      loadUpcoming();
    }
    // The Menu tab marks favorites itself; halls, diet and allergens need its menus again.
    if (touched("dining_halls", "diets", "avoid_allergens")) resetMenus();
    if (state.view === "menu") ensureMenus();
  }
  if (touched("favorites", "dining_halls", "notify")) loadAlerts();
  updateBell();
  return state.prefs;
}

// ---- alerts: favorites on the menus this week

async function loadAlerts() {
  if (!diningOn() || !state.prefs?.notify?.in_app || !state.prefs.favorites.length) {
    state.alerts = [];
    updateBell();
    return;
  }
  const epoch = state.epoch;
  try {
    const alerts = await api("/api/me/alerts?days=7");
    if (epoch !== state.epoch) return;
    state.alerts = Array.isArray(alerts) ? alerts.filter((a) => a && typeof a.name === "string") : [];
  } catch (err) {
    if (epoch !== state.epoch) return;
    if (err.status === 503) return diningLost();
  }
  updateBell();
  if (els.panel.open && els.panel.dataset.kind === "alerts") renderAlertsPanel({ refocus: false });
}

function updateBell() {
  const show = document.body.dataset.screen === "app" && diningOn() && Boolean(state.prefs?.notify?.in_app);
  els.bell.hidden = !show;
  const n = state.alerts.length;
  els.bellDot.hidden = !n;
  const label = n ? `Alerts: ${n} of your favorites ${n === 1 ? "is" : "are"} on the menu this week` : "Alerts";
  els.bell.setAttribute("aria-label", label);
  els.railAlerts.hidden = !show;
  els.railDot.hidden = !n;
  els.railAlerts.setAttribute("aria-label", label);
}

// ---- the nearest dining hall

/** The nearest dining commons to the device, with the sentence to show. The coordinates go to this
    one request and nowhere else; the answer is kept for the dashboard. */
async function fetchNearest(lat, lon) {
  const epoch = state.epoch;
  try {
    const near = await api(`/api/dining/nearest?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
    if (epoch !== state.epoch || !Number.isInteger(near?.location?.id) || typeof near.note !== "string") return null;
    state.nearest = near;
    return near;
  } catch (err) {
    if (err.status === 503) diningLost();
    return null;
  }
}

/** For the dashboard: the last answer, or a fresh look-up from the device location. */
async function nearestHall() {
  if (state.nearest) return state.nearest;
  try {
    const [lat, lon] = await devicePosition();
    return await fetchNearest(lat, lon);
  } catch {
    return null;
  }
}

/** After a photo with location: the nearest hall under the result, and the side column follows it. */
async function noteNearest([lat, lon], slot) {
  const near = await fetchNearest(lat, lon);
  if (!near || state.slots.analyze !== slot) return;
  slot.nearest = near;
  if (state.shown === slot) renderResult("update");
  state.upcoming = null;
  loadUpcoming();
}

/** The server's sentence, with "Change dining hall" as the way to the picker: Settings from the
    dashboard and a result, the picker itself from inside Settings or the set-up steps. */
function nearestNoteHtml(note, where) {
  const text = String(note);
  const phrase = "Change dining hall";
  const at = text.indexOf(phrase);
  const link =
    where === "picker"
      ? `<button class="link-btn link-btn--inline" type="button" data-action="focus-picker">${phrase}</button>`
      : settingsLinkHtml(phrase, "dining");
  const body = at >= 0 ? `${esc(text.slice(0, at))}${link}${esc(text.slice(at + phrase.length))}` : `${esc(text)} ${link} if this is wrong.`;
  return `<p class="note nearest-note nearest-note--${where}">${icon("pin")}<span>${body}</span></p>`;
}

// ---- the upcoming meal (the right column on laptops, a card on phones)

let upcomingSeq = 0;

/** Draw the side column from what's known; fetch it when nothing is. */
function ensureUpcoming() {
  if (!diningOn()) {
    els.dashSide.innerHTML = "";
    return;
  }
  if (state.upcoming) renderUpcoming();
  else loadUpcoming();
}

async function loadUpcoming() {
  if (!diningOn() || document.body.dataset.screen !== "app") return;
  ensureBusy();
  const seq = ++upcomingSeq;
  const epoch = state.epoch;
  els.dashSide.innerHTML = upcomingSkeletonHtml();
  let locationId = null;
  let note = null;
  if (els.location.checked) {
    const near = await nearestHall();
    if (seq !== upcomingSeq || epoch !== state.epoch) return;
    if (near) {
      locationId = near.location.id;
      note = near.note;
    }
  }
  try {
    const meal = await api(`/api/me/dining/next${locationId ? `?location_id=${encodeURIComponent(locationId)}` : ""}`);
    if (seq !== upcomingSeq || epoch !== state.epoch) return;
    state.upcoming = { meal, note, locationId };
  } catch (err) {
    if (seq !== upcomingSeq || epoch !== state.epoch || err.signedOut) return;
    if (err.status === 503) return diningLost();
    state.upcoming = { error: err, note, locationId };
  }
  renderUpcoming();
}

const upcomingSkeletonHtml = () =>
  `<div class="card upcoming is-skeleton" aria-hidden="true"><span class="sk sk--eyebrow"></span><span class="sk sk--num"></span><span class="sk sk--line"></span>${'<span class="sk-row"><span class="sk sk--line"></span><span class="sk sk--short"></span></span>'.repeat(4)}</div>`;

function renderUpcoming() {
  if (!diningOn() || !state.upcoming) {
    els.dashSide.innerHTML = "";
    return;
  }
  const { meal, note, error, locationId } = state.upcoming;
  const hall = meal?.location?.name ?? hallNameById(locationId ?? state.prefs.dining_halls[0]) ?? "Your dining hall";
  const head = `
    <div class="upcoming__head">
      <h2 id="upcoming-title" class="eyebrow">Upcoming meal</h2>
      <p class="upcoming__hall">${esc(hall)}${SEP}${settingsLinkHtml("Change", "dining")}</p>
      ${busyBadgeHtml(meal?.location?.id ?? locationId ?? state.prefs.dining_halls[0])}
    </div>`;
  const nearest = note ? nearestNoteHtml(note, "side") : "";
  if (error) {
    els.dashSide.innerHTML = `
      <section class="card upcoming" aria-labelledby="upcoming-title">
        ${head}
        ${nearest}
        <p class="status is-error" role="alert">${esc(error.message)}</p>
        <button class="btn btn--soft" type="button" data-action="retry-upcoming">Try again</button>
      </section>`;
    return;
  }
  const items = Array.isArray(meal.items) ? meal.items.filter((item) => item && typeof item.name === "string") : [];
  const groups = new Map();
  for (const item of items) {
    const category = typeof item.category === "string" && item.category.trim() ? item.category : "Other";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(item);
  }
  const avoid = new Set(state.prefs.avoid_allergens);
  const favorites = favoriteKeys();
  let n = 0;
  const rows = [...groups]
    .map(
      ([category, list]) => `
        <li class="dishes__group">
          <p class="dishes__cat">${esc(category)}</p>
          <ul class="dishes__list">${list
            .map((item) => {
              const ref = `side:${n++}`;
              dishRefs.set(ref, { item, meal, hall });
              return dishRowHtml({
                name: item.name,
                calories: item.calories,
                fav: favorites.has(dishKey(item.name)),
                avoid: item.avoid === true,
                off: item.fits_diet === false,
                avoided: Array.isArray(item.allergens) ? item.allergens.filter((a) => avoid.has(a)) : [],
                kinds: dietKinds(item),
                ref,
              });
            })
            .join("")}</ul>
        </li>`
    )
    .join("");
  const when = typeof meal.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(meal.date) ? dayTitle(meal.date) : null;
  const whenText = when ? (when.title === "Today" ? "Today" : `${when.title}, ${when.sub}`) : "";
  const legend = dishLegendHtml(items.some((i) => favorites.has(dishKey(i.name))), items.some((i) => i.avoid === true), items.some((i) => i.fits_diet === false), items.map(dietKinds));
  // "Dinner, usually 4:30 to 9 pm": the label is already the heading, so the hint keeps the hours.
  const label = typeof meal.label === "string" && meal.label ? meal.label : capitalize(String(meal.meal ?? "Meal"));
  const hint = typeof meal.starts_hint === "string" ? meal.starts_hint.replace(new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")},\\s*`), "") : "";
  els.dashSide.innerHTML = `
    <section class="card upcoming" aria-labelledby="upcoming-title">
      ${head}
      <p class="upcoming__meal"><strong>${esc(label)}</strong>${whenText ? `<span class="upcoming__when">${SEP}${esc(whenText)}</span>` : ""}</p>
      ${hint ? `<p class="upcoming__hint">${esc(hint)}</p>` : ""}
      ${nearest}
      ${legend}
      <div class="dishes" tabindex="0" role="region" aria-label="${esc(`${meal.label ?? "Menu"} items`)}">
        <ul class="dishes__groups">${rows || '<li class="options__empty">Nothing is listed for this meal yet.</li>'}</ul>
      </div>
      <p class="upcoming__count"><span>${items.length} item${items.length === 1 ? "" : "s"} on the menu</span><a class="upcoming__more" href="#menu" data-to-menu>Every hall${icon("chevron-right")}</a></p>
    </section>`;
  measureSideFit();
  fitSideCard();
}

// ---- What a dish is. Vegetarian, plant based, halal and local are UMass Dining's own labels.
// Meat and pescatarian aren't labelled, so they come from the name and allergens, and only for a
// dish UMass doesn't already call vegetarian or plant based.

const KINDS = {
  meat: ["Meat", "meat"],
  fish: ["Pescatarian", "fish"],
  plant: ["Plant based", "sprout"],
  veg: ["Vegetarian", "leaf"],
  halal: ["Halal", "halal"],
  local: ["Local", "local"],
};
const MEAT_WORDS =
  /\b(chicken|beef|pork|turkey|bacon|ham|sausages?|pepperoni|salami|prosciutto|chorizo|lamb|veal|steak|brisket|meatballs?|hot ?dogs?|carnitas|barbacoa|bulgogi|duck|kielbasa|pastrami|ribs|burgers?|cheeseburgers?)\b/i;
const NOT_MEAT_WORDS = /\b(veggie|vegan|vegetarian|tofu|tempeh|seitan|impossible|beyond|plant|meatless|jackfruit|cauliflower)\b/i;
const FISH_WORDS =
  /\b(fish|salmon|tuna|cod|haddock|pollock|tilapia|shrimp|prawns?|crab|lobster|scallops?|clams?|mussels?|calamari|squid|anchov(?:y|ies)|sardines?|trout|halibut|swordfish|seafood|sushi|poke)\b/i;

function dietKinds(item) {
  const name = String(item?.name ?? "");
  const diets = new Set(Array.isArray(item?.diets) ? item.diets : []);
  const allergens = new Set(Array.isArray(item?.allergens) ? item.allergens : []);
  const kinds = [];
  if (diets.has("Plant Based")) kinds.push("plant");
  else if (diets.has("Vegetarian")) kinds.push("veg");
  else if (MEAT_WORDS.test(name) && !NOT_MEAT_WORDS.test(name)) kinds.push("meat");
  else if (allergens.has("Fish") || allergens.has("Shellfish") || FISH_WORDS.test(name)) kinds.push("fish");
  if (diets.has("Halal")) kinds.push("halal");
  if (diets.has("Local")) kinds.push("local");
  return kinds;
}

const kindIconHtml = (kind) => `<span class="kind kind--${kind}" title="${KINDS[kind][0]}">${icon(KINDS[kind][1])}</span>`;

function dishLegendHtml(fav, avoid, off, kinds = []) {
  const shown = new Set(kinds.flat());
  const parts = [
    ...Object.keys(KINDS)
      .filter((k) => shown.has(k))
      .map((k) => `<span class="legend__item">${kindIconHtml(k)}${KINDS[k][0].toLowerCase()}</span>`),
    fav ? `<span class="legend__item">${icon("star")}favorite</span>` : "",
    avoid ? `<span class="legend__item">${icon("avoid")}has an allergen you avoid</span>` : "",
    off ? `<span class="legend__item"><span class="legend__off" aria-hidden="true"></span>not in your diet</span>` : "",
  ].filter(Boolean);
  return parts.length ? `<p class="legend">${parts.join("")}</p>` : "";
}

/** One dish. With `ref` it's a button that opens the dish's sheet (its facts and the favorite toggle). */
function dishRowHtml({ name, calories, fav, avoid, off, avoided, kinds = [], ref = null }) {
  const kcal = typeof calories === "number" && Number.isFinite(calories) ? `${nf0.format(calories)} kcal` : "";
  const cls = `dish${fav ? " is-fav" : ""}${avoid ? " is-avoid" : ""}${off ? " is-off" : ""}${ref ? " has-btn" : ""}`;
  const inner = `
      <span class="dish__marks" aria-hidden="true">${fav ? icon("star") : ""}${avoid ? icon("avoid") : ""}</span>
      <span class="dish__text">
        <span class="dish__name">${esc(name)}${kinds.length ? `<span class="dish__kinds" aria-hidden="true">${kinds.map(kindIconHtml).join("")}</span>` : ""}</span>${kinds.length ? `<span class="visually-hidden">, ${kinds.map((k) => KINDS[k][0].toLowerCase()).join(", ")}</span>` : ""}${fav ? '<span class="visually-hidden">, a favorite</span>' : ""}
        ${avoid ? `<span class="dish__warn">${SEP}Has ${esc(avoided.length ? avoided.join(", ") : "an allergen you avoid")}</span>` : ""}
        ${off ? `<span class="dish__off">${SEP}Not in your diet</span>` : ""}
      </span>
      ${kcal ? `<span class="dish__kcal">${SEP}${esc(kcal)}</span>` : '<span class="dish__kcal"></span>'}`;
  if (!ref) return `<li class="${cls}">${inner}</li>`;
  return `<li class="${cls}"><button class="dish__btn" type="button" data-dish="${esc(ref)}" aria-haspopup="dialog">${inner}</button></li>`;
}

// ---- the Menu tab: the next meal at each dining hall the person chose
// One GET /api/me/dining/next per hall (it takes one location_id), each kept until the halls, the
// diet or the allergens change. Chips at the top pick the hall; the pick is remembered on this
// device. With location on, the nearest dining commons comes first.

const MENU_HALL_KEY = "mealscholar:menu-hall";
const DEFAULT_HALL = 0; // no hall chosen: the server's default hall (no location_id)
const menus = { data: new Map(), seq: 0 }; // hall id -> { meal } | { error } | { pending: true }
const dishRefs = new Map(); // a dish button's data-dish -> { item, meal, hall }

const favoriteKeys = () => new Set((state.prefs?.favorites ?? []).map((f) => dishKey(f.name)));

function resetMenus() {
  menus.seq += 1;
  menus.data.clear();
}

/** The halls on the Menu tab, in chip order. */
function menuHallIds() {
  const ids = [...(state.prefs?.dining_halls ?? [])];
  const near = els.location.checked ? state.nearest?.location?.id : null;
  if (Number.isInteger(near)) {
    const at = ids.indexOf(near);
    if (at >= 0) ids.splice(at, 1);
    ids.unshift(near);
  }
  return ids.length ? ids : [DEFAULT_HALL];
}

function pickedHall(ids = menuHallIds()) {
  let saved = null;
  try {
    saved = Number(localStorage.getItem(MENU_HALL_KEY));
  } catch {}
  return ids.includes(saved) ? saved : ids[0];
}

function pickHall(id) {
  try {
    localStorage.setItem(MENU_HALL_KEY, String(id));
  } catch {}
}

const hallLabel = (id) => menus.data.get(id)?.meal?.location?.name ?? (id === DEFAULT_HALL ? "Dining hall" : hallNameById(id) ?? `Hall ${id}`);

// ---- How busy each hall is, from its live cam (GET /api/dining/busy; the cams/ sampler)

const busyNow = { halls: new Map(), at: 0, pending: null, off: false, sig: "" };
const BUSY_TTL = 60_000;
const BUSY_TEXT = { quiet: "Quiet now", moderate: "Getting busy", busy: "Busy now" };

/** Fetch the readings when they are over a minute old. Redraws only when a level or count changed. */
function ensureBusy() {
  if (busyNow.off || busyNow.pending || Date.now() - busyNow.at < BUSY_TTL || !diningOn()) return;
  busyNow.pending = api("/api/dining/busy", { session: false })
    .then((reply) => {
      const halls = new Map((Array.isArray(reply?.halls) ? reply.halls : []).map((h) => [Number(h.location_id), h]));
      busyNow.halls = halls;
      busyNow.at = Date.now();
      const sig = JSON.stringify([...halls.values()].map((h) => [h.location_id, h.level, h.people, Boolean(camClip(h))]));
      if (sig === busyNow.sig) return;
      busyNow.sig = sig;
      if (document.body.dataset.screen !== "app") return;
      if (state.view === "menu") renderMenu();
      if (state.upcoming && !state.upcoming.error) renderUpcoming();
    })
    .catch((err) => {
      busyNow.at = Date.now();
      if (err.status === 503 || err.status === 404) busyNow.off = true; // no hall cams on this server
    })
    .finally(() => {
      busyNow.pending = null;
    });
}

setInterval(() => {
  if (!document.hidden && document.body.dataset.screen === "app") ensureBusy();
}, BUSY_TTL);

const busyHall = (id) => {
  const hall = busyNow.halls.get(Number(id));
  return hall && BUSY_TEXT[hall.level] ? hall : null;
};

const minsAgo = (iso) => {
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60_000);
  return Number.isFinite(mins) ? (mins < 1 ? "just now" : `${mins} min ago`) : "";
};

/** The hall's boxed clip from the cams sampler, when the server links one (it does for 15 minutes). */
const camClip = (hall) =>
  typeof hall?.clip_url === "string" && hall.clip_url.startsWith("/api/dining/cams/")
    ? {
        clip: hall.clip_url,
        poster: typeof hall.poster_url === "string" && hall.poster_url.startsWith("/api/dining/cams/") ? hall.poster_url : "",
        at: typeof hall.clip_at === "string" ? hall.clip_at : hall.updated_at,
        live: typeof hall.live_url === "string" && hall.live_url.startsWith("https://www.youtube.com/watch?v=") ? hall.live_url : "",
      }
    : null;

/** "Quiet now · 8 people in view · 2 min ago", from the hall's live cam. With a clip it's a button that
    opens the cam with the model's boxes. */
function busyBadgeHtml(id) {
  const hall = busyHall(id);
  if (!hall) return "";
  const people = Number.isFinite(hall.people) ? `${hall.people} ${hall.people === 1 ? "person" : "people"} in view` : "";
  const meta = [people, minsAgo(hall.updated_at)].filter(Boolean).join(" · ");
  const cam = camClip(hall);
  // The chevron rides inside the last words so it never wraps onto a line of its own.
  const chev = cam ? `<span class="visually-hidden">. See the hall cam</span><span class="busy__chev" aria-hidden="true">${icon("chevron-right")}</span>` : "";
  const inner = `<span class="busy__dot" aria-hidden="true"></span><span class="busy__level">${BUSY_TEXT[hall.level]}${meta ? "" : chev}</span>${meta ? `<span class="busy__meta">${SEP}<span aria-hidden="true">· </span>${meta}${chev}</span>` : ""}`;
  if (!cam) return `<p class="busy busy--${hall.level}" title="From the hall's live cam">${inner}</p>`;
  return `
    <button class="busy busy--${hall.level} busy--cam" type="button" data-cam="${esc(hall.location_id)}" aria-haspopup="dialog">${inner}</button>`;
}

/** The hall cam's last 16 seconds with a green box on each person the model counted, in the panel sheet. */
function openCam(button) {
  const hall = busyHall(button.dataset.cam);
  const cam = camClip(hall);
  if (!cam) return;
  const name = hallLabel(Number(hall.location_id)) || hall.name;
  const ago = minsAgo(cam.at);
  const still = reducedMotion.matches; // no autoplay: the poster, with controls to play it
  button.focus({ preventScroll: true }); // so closing the sheet comes back here (Safari doesn't focus on click)
  openPanel({
    kicker: "Hall cam",
    title: `${name} right now`,
    intro: `From the hall's live cam, ${ago || "a few minutes ago"}. Each green box is a person the model counted.`,
    kind: "cam",
    html: `
      <figure class="cam">
        <div class="cam__frame">
          <video class="cam__video" src="${esc(cam.clip)}"${cam.poster ? ` poster="${esc(cam.poster)}"` : ""} muted loop playsinline ${still ? "controls preload=\"none\"" : "autoplay preload=\"auto\""} aria-label="${esc(`${name}'s dining room from the hall cam, with a green box on each person counted`)}"></video>
        </div>
        <figcaption class="cam__caption"><span class="busy busy--${hall.level}"><span class="busy__dot" aria-hidden="true"></span><span class="busy__level">${BUSY_TEXT[hall.level]}</span><span class="busy__meta"><span aria-hidden="true">· </span>${esc(hall.people)} ${hall.people === 1 ? "person" : "people"} in view</span></span></figcaption>
      </figure>
      ${cam.live ? `<a class="btn btn--soft cam__live" href="${esc(cam.live)}" target="_blank" rel="noopener">Watch the live cam${icon("arrow")}<span class="visually-hidden"> (opens YouTube in a new tab)</span></a>` : ""}
      <p class="note cam__note">The model counts people and identifies no one. Only the latest 16 seconds are kept.</p>`,
  });
  els.panel.dataset.cam = String(hall.location_id);
  const video = $(".cam__video", els.panelBody);
  if (!video) return;
  video.muted = true;
  video.addEventListener("error", () => {
    video.closest(".cam")?.classList.add("is-missing");
    video.removeAttribute("autoplay");
    video.controls = false;
  });
  // A blocked autoplay (Low Power Mode) gets a play button; a clip that won't load shows its poster and a note.
  if (!still) video.play?.()?.catch?.(() => !video.error && (video.controls = true));
}

document.addEventListener("click", (event) => {
  const button = event.target.closest?.("[data-cam]");
  if (button && !button.closest("dialog")) openCam(button);
});

/** Draw the tab from what's known and fetch what isn't: every hall's next meal, side by side. */
async function ensureMenus() {
  if (document.body.dataset.screen !== "app" || !diningOn()) return;
  ensureBusy();
  const epoch = state.epoch;
  // With location on, find the nearest hall first (once), so it leads the chips.
  if (els.location.checked && !state.nearest) {
    renderMenu();
    await nearestHall();
    if (epoch !== state.epoch) return;
  }
  const seq = menus.seq;
  const ids = menuHallIds();
  const wanted = ids.filter((id) => !menus.data.has(id));
  for (const id of wanted) menus.data.set(id, { pending: true });
  renderMenu();
  await Promise.all(
    wanted.map(async (id) => {
      let entry;
      try {
        const meal = await api(`/api/me/dining/next${id === DEFAULT_HALL ? "" : `?location_id=${encodeURIComponent(id)}`}`);
        entry = meal && Array.isArray(meal.items) ? { meal } : { error: new Error("That menu came back in a shape this app can't read.") };
      } catch (err) {
        if (err.signedOut) return;
        if (err.status === 503) return diningLost();
        entry = { error: err };
      }
      if (epoch !== state.epoch || seq !== menus.seq) return;
      menus.data.set(id, entry);
      renderMenu();
    })
  );
}

function renderMenu() {
  if (!diningOn()) {
    els.menuHalls.innerHTML = "";
    els.menuActions.innerHTML = "";
    els.menuBody.innerHTML = "";
    return;
  }
  const ids = menuHallIds();
  const pick = pickedHall(ids);
  const near = els.location.checked ? state.nearest?.location?.id : null;
  const focusedHall = document.activeElement?.closest?.("[data-hall]")?.dataset.hall ?? null;
  const focusedDish = document.activeElement?.closest?.("[data-dish]")?.dataset.dish ?? null;
  const noHalls = !(state.prefs?.dining_halls ?? []).length;
  els.menuHalls.innerHTML = `
    <div class="halls-bar__track">
      ${ids
        .map(
          (id) => `
        <button class="hall-chip" type="button" data-hall="${id}" aria-pressed="${id === pick}">
          ${id === near ? `<span class="hall-chip__near">${icon("pin")}<span class="visually-hidden">Nearest: </span></span>` : ""}<span>${esc(hallLabel(id))}</span>${busyHall(id) ? `<span class="busy__dot busy--${busyHall(id).level}" aria-hidden="true"></span><span class="visually-hidden">, ${BUSY_TEXT[busyHall(id).level].toLowerCase()}</span>` : ""}
        </button>`
        )
        .join("")}
    </div>`;
  const actionLabel = noHalls ? "Choose halls" : "Edit halls";
  if (els.menuActions.textContent.trim() !== actionLabel) {
    els.menuActions.innerHTML = `<button class="btn btn--secondary intro-btn" type="button" data-action="settings" data-section="dining" aria-haspopup="dialog">${icon(noHalls ? "plus" : "gear")}<span>${actionLabel}</span></button>`;
  }
  const entry = menus.data.get(pick);
  els.menuBody.setAttribute("aria-busy", String(!entry || Boolean(entry.pending)));
  els.menuBody.innerHTML = !entry || entry.pending ? menuSkeletonHtml() : entry.error ? menuErrorHtml(pick, entry.error) : menuCardHtml(pick, entry.meal, near);
  els.menuSub.textContent = ids.length > 1 ? `The next meal at your ${ids.length} dining halls, dish by dish.` : "The next meal at your dining hall, dish by dish.";
  if (focusedHall != null) $(`[data-hall="${CSS.escape(focusedHall)}"]`, els.menuHalls)?.focus({ preventScroll: true });
  else if (focusedDish != null) $(`[data-dish="${CSS.escape(focusedDish)}"]`, els.menuBody)?.focus({ preventScroll: true });
}

const menuSkeletonHtml = () => `
  <div class="card menu-card is-skeleton" aria-hidden="true">
    <span class="sk sk--eyebrow"></span><span class="sk sk--num"></span><span class="sk sk--line"></span>
    ${'<span class="sk-row"><span class="sk sk--line"></span><span class="sk sk--short"></span></span>'.repeat(6)}
  </div>
  <p class="visually-hidden" role="status">Loading the menu</p>`;

function menuErrorHtml(id, err) {
  return `
    <div class="card menu-card menu-card--error">
      <p class="eyebrow">${esc(hallLabel(id))}</p>
      <p class="status is-error" role="alert">${esc(err.message)}</p>
      <button class="btn btn--secondary" type="button" data-action="retry-menu" data-hall-retry="${id}">Try again</button>
    </div>`;
}

function menuCardHtml(id, meal, near) {
  const items = meal.items.filter((item) => item && typeof item.name === "string");
  const favorites = favoriteKeys();
  const avoid = new Set(state.prefs.avoid_allergens);
  const groups = new Map();
  for (const item of items) {
    const category = typeof item.category === "string" && item.category.trim() ? item.category : "Other";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(item);
  }
  const hall = meal.location?.name ?? hallLabel(id);
  const label = typeof meal.label === "string" && meal.label ? meal.label : capitalize(String(meal.meal ?? "Meal"));
  const when = typeof meal.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(meal.date) ? dayTitle(meal.date) : null;
  const whenText = when ? (when.title === "Today" ? "Today" : `${when.title}, ${when.sub}`) : "";
  const hint = typeof meal.starts_hint === "string" ? meal.starts_hint.replace(new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")},\\s*`), "") : "";
  let n = 0;
  const cats = [...groups]
    .map(
      ([category, list], i) => `
      <section class="menu-cat" aria-labelledby="menu-cat-${i}">
        <h3 id="menu-cat-${i}" class="dishes__cat">${esc(category)}</h3>
        <ul class="dishes__list">${list
          .map((item) => {
            const ref = `menu:${id}:${n++}`;
            dishRefs.set(ref, { item, meal, hall });
            const allergens = Array.isArray(item.allergens) ? item.allergens : [];
            return dishRowHtml({
              name: item.name,
              calories: item.calories,
              fav: favorites.has(dishKey(item.name)),
              avoid: item.avoid === true,
              off: item.fits_diet === false,
              avoided: allergens.filter((a) => avoid.has(a)),
              kinds: dietKinds(item),
              ref,
            });
          })
          .join("")}</ul>
      </section>`
    )
    .join("");
  const legend = dishLegendHtml(items.some((i) => favorites.has(dishKey(i.name))), items.some((i) => i.avoid === true), items.some((i) => i.fits_diet === false), items.map(dietKinds));
  const nearNote = id === near && state.nearest?.note ? nearestNoteHtml(state.nearest.note, "side") : "";
  return `
    <article class="card menu-card" aria-labelledby="menu-meal-title">
      <header class="menu-card__head">
        <p class="eyebrow">${esc(hall)}</p>
        ${busyBadgeHtml(meal.location?.id ?? id)}
        <h2 id="menu-meal-title" class="menu-card__meal"><strong>${esc(label)}</strong>${whenText ? `<span class="menu-card__when">${SEP}${esc(whenText)}</span>` : ""}</h2>
        ${hint ? `<p class="menu-card__hint">${esc(hint)}</p>` : ""}
        ${nearNote}
        ${legend}
      </header>
      ${items.length ? `<div class="menu-cats">${cats}</div>` : '<p class="options__empty">Nothing is listed for this meal yet.</p>'}
      <p class="menu-card__foot">${items.length} item${items.length === 1 ? "" : "s"} on the menu. Tap a dish for its nutrition facts.</p>
    </article>`;
}

els.menuView.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-hall]");
  if (chip) {
    const id = Number(chip.dataset.hall);
    if (chip.getAttribute("aria-pressed") === "true") return;
    pickHall(id);
    renderMenu();
    const body = els.menuBody;
    replayEntrance(body);
    return;
  }
  const dish = event.target.closest("[data-dish]");
  if (dish) return openDish(dish.dataset.dish, dish);
  const action = event.target.closest("[data-action]");
  switch (action?.dataset.action) {
    case "settings":
      return openSettings(action.dataset.section, action);
    case "retry-menu":
      menus.data.delete(Number(action.dataset.hallRetry));
      ensureMenus().then(() => $('[data-action="retry-menu"]', els.menuBody)?.focus({ preventScroll: true }));
      return;
  }
});

// Left and right arrows move between the hall chips, as in a toolbar.
els.menuHalls.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const chips = $$(".hall-chip", els.menuHalls);
  const at = chips.indexOf(document.activeElement);
  if (at < 0) return;
  event.preventDefault();
  chips[(at + (event.key === "ArrowRight" ? 1 : -1) + chips.length) % chips.length].focus();
});

// ---- a dish's sheet: its facts as the dining hall lists them, and Add to favorites

const notListed = '<span class="facts__none">Not listed</span>';
const nfFact = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

function factValue(value, unit) {
  if (typeof value !== "number" || !Number.isFinite(value)) return notListed;
  return `${esc(unit === "kcal" ? nf0.format(value) : nfFact.format(value))}<span class="facts__unit"> ${unit}</span>`;
}

function dishSheetHtml({ item, meal }) {
  const fav = favoriteKeys().has(dishKey(item.name));
  const allergens = Array.isArray(item.allergens) ? item.allergens.filter((a) => typeof a === "string") : [];
  const diets = Array.isArray(item.diets) ? item.diets.filter((d) => typeof d === "string") : [];
  const avoid = new Set(state.prefs?.avoid_allergens ?? []);
  const avoided = allergens.filter((a) => avoid.has(a));
  const tags = [
    ...dietKinds(item).map((k) => `<span class="tag tag--kind">${kindIconHtml(k)}${KINDS[k][0]}</span>`),
    item.avoid === true ? `<span class="tag tag--avoid">${icon("avoid")}Has ${esc(avoided.length ? avoided.join(", ") : "an allergen you avoid")}</span>` : "",
    item.fits_diet === false ? '<span class="tag tag--off">Not in your diet</span>' : "",
  ].filter(Boolean);
  const list = (values) => (values.length ? values.map((v) => `<span class="chip">${esc(v)}</span>`).join("") : notListed);
  const serving = typeof item.serving_size === "string" && item.serving_size.trim() ? esc(item.serving_size) : notListed;
  return `
    ${tags.length ? `<p class="dish-sheet__tags">${tags.join("")}</p>` : ""}
    <div class="dish-sheet__fav">
      <button class="btn ${fav ? "btn--secondary" : "btn--primary"} btn--block" type="button" data-action="toggle-fav" aria-pressed="${fav}">
        ${icon("star")}<span>${fav ? "Remove from favorites" : "Add to favorites"}</span>
      </button>
      <p class="dish-sheet__status status" role="status" aria-live="polite"></p>
    </div>
    <section class="dish-sheet__facts" aria-labelledby="facts-title">
      <div class="section-head"><h3 id="facts-title" class="eyebrow">Nutrition facts</h3><span class="chip">Per serving</span></div>
      <dl class="facts">
        <div class="facts__row facts__row--lead"><dt>Calories</dt><dd>${factValue(item.calories, "kcal")}</dd></div>
        <div class="facts__row"><dt>Protein</dt><dd>${factValue(item.protein_g, "g")}</dd></div>
        <div class="facts__row"><dt>Carbs</dt><dd>${factValue(item.carbs_g, "g")}</dd></div>
        <div class="facts__row"><dt>Fat</dt><dd>${factValue(item.fat_g, "g")}</dd></div>
        <div class="facts__row facts__row--text"><dt>Serving size</dt><dd>${serving}</dd></div>
        <div class="facts__row facts__row--chips"><dt>Allergens</dt><dd>${list(allergens)}</dd></div>
        <div class="facts__row facts__row--chips"><dt>Diet tags</dt><dd>${list(diets)}</dd></div>
      </dl>
      <p class="note">As the dining hall lists it for one serving, not an estimate from a photo. A dish with no allergens listed isn't always free of them; ask the dining staff if it matters.</p>
    </section>`;
}

function openDish(ref, opener) {
  const dish = dishRefs.get(ref);
  if (!dish) return;
  const label = typeof dish.meal?.label === "string" ? dish.meal.label : "";
  opener?.focus?.({ preventScroll: true }); // so closing the sheet comes back to this dish
  openPanel({ kicker: [dish.hall, label].filter(Boolean).join(" · "), title: dish.item.name, html: dishSheetHtml(dish), kind: "dish" });
  els.panel.dataset.dish = ref;
}

/** Add the dish to the favorites, or take it off: the whole list goes back in one PATCH, as Settings
    saves it. Names match the way the server matches them (dish_key), so "Butter chicken" and
    "Butter Chicken" are one favorite. */
async function toggleFavorite(button) {
  const dish = dishRefs.get(els.panel.dataset.dish ?? "");
  if (!dish || !state.prefs) return;
  const status = $(".dish-sheet__status", els.panelBody);
  const key = dishKey(dish.item.name);
  const current = state.prefs.favorites.map((f) => ({ name: f.name }));
  const had = current.some((f) => dishKey(f.name) === key);
  if (!had && current.length >= MAX_FAVORITES) {
    setStatus(`You can keep up to ${MAX_FAVORITES} favorites. Remove one in Settings first.`, { error: true, target: status });
    return;
  }
  const next = had ? current.filter((f) => dishKey(f.name) !== key) : [...current, { name: dish.item.name }];
  const epoch = state.epoch;
  setStatus("", { target: status });
  await withBusy(button, async () => {
    try {
      await savePrefs({ favorites: next });
    } catch (err) {
      if (epoch !== state.epoch || err.signedOut || !button.isConnected) return;
      setStatus(err.message, { error: true, target: status });
      return;
    }
    if (epoch !== state.epoch) return;
    const fav = favoriteKeys().has(key);
    if (button.isConnected) {
      button.setAttribute("aria-pressed", String(fav));
      button.classList.toggle("btn--primary", !fav);
      button.classList.toggle("btn--secondary", fav);
      $("span", button).textContent = fav ? "Remove from favorites" : "Add to favorites";
      setStatus(fav ? "Added to your favorites." : "Removed from your favorites.", { target: status });
    }
    renderMenu();
  });
  // The busy (disabled) button dropped focus; give it back so a keyboard user can toggle again.
  const lost = !document.activeElement || document.activeElement === document.body || document.activeElement === els.panel;
  if (lost && button.isConnected && els.panel.open) button.focus({ preventScroll: true });
}

// ---- the Recently uploaded column (laptops)

function recentHtml(view) {
  const meals = [...view.meals].sort((a, b) => (a.context.taken_at < b.context.taken_at ? 1 : a.context.taken_at > b.context.taken_at ? -1 : 0));
  const title = dayName(state.day);
  const when = state.day === state.todayKey ? "today" : title === "Yesterday" ? "yesterday" : `on ${title}`;
  return `
    <section class="block recent-block" aria-labelledby="recent-title">
      <div class="section-head">
        <h2 id="recent-title" class="section-title">Recently uploaded</h2>
        <p class="eyebrow">${mealCount(meals.length)}</p>
      </div>
      ${meals.length ? `<ol class="recent">${meals.map(recentCardHtml).join("")}</ol>` : `<div class="card recent__empty"><p>Nothing logged ${esc(when)}. Meals with photos land here, newest first.</p></div>`}
    </section>`;
}

function recentCardHtml(m) {
  const kcal = m.totals.energy_kcal;
  const names = m.items.map((r) => capitalize(r.item.name)).join(", ");
  const place = placeName(m.context);
  // The meal's own photo when one was saved with it, else a plate.
  const photo = m.photo_url
    ? `<span class="recent-card__photo recent-card__photo--img" aria-hidden="true"><img src="${esc(thumbUrl(m.photo_url))}" alt="" width="44" height="44" loading="lazy" decoding="async" data-stored></span>`
    : `<span class="recent-card__photo" aria-hidden="true">${icon("fork")}</span>`;
  return `
    <li>
      <button class="card recent-card" type="button" data-meal="${esc(m.id)}">
        ${photo}
        <span class="recent-card__body">
          <span class="recent-card__when"><span class="recent-card__slot">${esc(capitalize(m.context.meal_slot))}</span>${SEP}<span class="recent-card__time">${timeFormat.format(wallClock(m.context.taken_at))}</span>${
            place ? `<span class="recent-card__place">${SEP}${esc(place)}</span>` : ""
          }</span>
          <span class="recent-card__foods">${SEP}${names ? esc(names) : "No foods found"}</span>
          <span class="recent-card__kcal">${SEP}${kcal ? rangeHtml(kcal, "kcal", undefined, { stack: true }) : '<span class="value__none">Not counted</span>'}</span>
        </span>
        <span class="meal__chev" aria-hidden="true">${icon("chevron-right")}</span>
      </button>
    </li>`;
}

/** The small copy of a stored photo (GET /api/meals/{id}/photo?size=thumb), from the meal's photo_url. */
function thumbUrl(url) {
  const thumb = new URL(url, location.href);
  thumb.searchParams.set("size", "thumb");
  return thumb.pathname + thumb.search;
}

// A stored photo that won't load (gone from the server) leaves its row the plate, and an opened
// meal no photo. Errors don't bubble, so this listens on the way down.
document.addEventListener(
  "error",
  (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.hasAttribute("data-stored")) return;
    const tile = img.closest(".recent-card__photo");
    if (tile) {
      tile.classList.remove("recent-card__photo--img");
      tile.innerHTML = icon("fork");
      return;
    }
    img.closest(".meal-card")?.classList.remove("meal-card--photo");
    img.closest(".shot")?.remove();
  },
  true
);

// ---- the health score
// A transparent "nutrient balance" number from 0 to 100: today's likely amounts against the FDA
// Daily Values. Points toward 100% of the DV for the nutrients most people get too little of, points
// off past 100% for the ones most people get too much of. A rough guide, not medical advice.

const SCORE_PLUS = [
  ["protein_g", "Protein"],
  ["fiber_g", "Fiber"],
  ["calcium_mg", "Calcium"],
  ["iron_mg", "Iron"],
  ["potassium_mg", "Potassium"],
  ["vitamin_a_ug", "Vitamin A"],
  ["vitamin_c_mg", "Vitamin C"],
  ["vitamin_d_ug", "Vitamin D"],
];
const SCORE_MINUS = [
  ["sat_fat_g", "Saturated fat"],
  ["sodium_mg", "Sodium"],
];
const ADDED_SUGARS_DV = 50; // g: the label's Daily Value for added sugars
const PLUS_POINTS = 100 / SCORE_PLUS.length;
const MINUS_POINTS = 10;

/** Likely % of the Daily Value: from the API where it gives one, else from `dv` or the nutrient list. */
function likelyPct(view, key, dv = null) {
  const given = view.daily_value_pct?.[key]?.likely;
  if (Number.isFinite(given)) return given;
  const value = view.totals?.[key]?.likely;
  const reference = dv ?? state.nutrients.find((n) => n.key === key)?.daily_value ?? null;
  return Number.isFinite(value) && Number.isFinite(reference) && reference > 0 ? (value / reference) * 100 : null;
}

function healthScore(view) {
  const parts = [];
  let total = 0;
  const name = (key, fallback) => (labelOf(key) === key ? fallback : labelOf(key));
  for (const [key, fallback] of SCORE_PLUS) {
    const pct = likelyPct(view, key);
    const points = pct == null ? 0 : PLUS_POINTS * Math.min(1, Math.max(0, pct) / 100);
    parts.push({ key, label: name(key, fallback), pct, points, plus: true });
    total += points;
  }
  // Sugars: added sugars when the API has them, else total sugars against the same 50 g value.
  const sugars = view.totals?.added_sugars_g ? ["added_sugars_g", "Added sugars"] : ["sugars_g", "Total sugars"];
  for (const [key, fallback] of [...SCORE_MINUS, sugars]) {
    const pct = likelyPct(view, key, key === sugars[0] ? ADDED_SUGARS_DV : null);
    const points = pct == null ? 0 : -MINUS_POINTS * Math.min(1, Math.max(0, pct - 100) / 100);
    parts.push({ key, label: name(key, fallback), pct, points, plus: false });
    total += points;
  }
  return { score: Math.round(Math.min(100, Math.max(0, total))), parts };
}

function scoreHtml(view) {
  const { score, parts } = healthScore(view);
  const points = (p) => (p.pct == null ? "0" : `${p.points > 0 ? "+" : p.points < 0 ? "-" : ""}${nf1.format(Math.abs(p.points))}`);
  const rows = parts
    .map(
      (p) => `
      <li class="score__part${p.plus ? "" : " score__part--minus"}">
        <span class="score__part-name">${esc(p.label)}</span>
        <span class="score__part-pct">${p.pct == null ? "No data" : `${nf0.format(p.pct)}% of DV`}</span>
        <span class="score__part-pts">${points(p)}</span>
      </li>`
    )
    .join("");
  const plusNames = SCORE_PLUS.map(([key, fallback]) => (labelOf(key) === key ? fallback : labelOf(key))); // names keep their case
  const nothingCounted = parts.every((p) => p.pct == null);
  const main = nothingCounted
    ? `<p class="score__empty note">No data yet. Pick a USDA food for each item to score the day.</p>`
    : `<p class="score__value"><span class="score__num">${score}</span><span class="score__of">of 100</span></p>
        <span class="score__bar" aria-hidden="true"><span class="score__fill" style="width:${score}%"></span></span>`;
  return `
    <section class="card score" aria-labelledby="score-title">
      <div class="score__head">
        <h2 id="score-title" class="eyebrow">Health score</h2>
        <p class="eyebrow">Nutrient balance</p>
      </div>
      <div class="score__main">
        ${main}
      </div>
      <div class="score__foot">
        <p class="score__note">A rough guide, not medical advice.</p>
        <details class="disclosure score__how"${state.scoreOpen ? " open" : ""}>
          <summary>How it's scored</summary>
          <div class="disclosure__body">
            <p class="note">Up to ${nf1.format(PLUS_POINTS)} points each for ${esc(plusNames.slice(0, -1).join(", "))} and ${esc(plusNames.at(-1))} as today's likely amount reaches 100% of its Daily Value. Up to ${MINUS_POINTS} points off each for saturated fat, sodium and sugars past 100% (${ADDED_SUGARS_DV} g for sugars, the label value for added sugars).</p>
            <ul class="score__parts">${rows}</ul>
          </div>
        </details>
      </div>
    </section>`;
}

// ---- the panel sheet: alerts, and a day's menu

let panelTimer = 0;
let panelRestore = true;
let panelOpener = null;
let pressedPanelBackdrop = false;

function setPanelContent({ kicker, title, intro = "", html, kind }) {
  els.panel.dataset.kind = kind;
  els.panelKicker.textContent = kicker;
  els.panelTitle.textContent = title;
  els.panelIntro.textContent = intro;
  els.panelIntro.hidden = !intro;
  els.panelBody.innerHTML = html;
}

function openPanel(content, { refocus = true } = {}) {
  clearTimeout(panelTimer);
  els.panel.classList.remove("is-closing");
  els.panel.style.transform = "";
  setPanelContent(content);
  document.documentElement.classList.add("has-sheet");
  if (!els.panel.open) {
    panelOpener = document.activeElement;
    els.panel.showModal();
    els.panelTitle.focus();
  } else if (refocus) {
    els.panelTitle.focus();
  }
}

function closePanel({ restoreFocus: restore = true, swiped = false } = {}) {
  if (!els.panel.open) return;
  panelRestore = restore;
  const done = () => els.panel.open && els.panel.close();
  clearTimeout(panelTimer);
  if (reducedMotion.matches) return done();
  if (swiped) {
    els.panel.style.transition = "transform 200ms ease-in";
    els.panel.style.transform = "translateY(100%)";
  } else {
    els.panel.classList.add("is-closing");
  }
  panelTimer = setTimeout(done, 190);
}

els.panel.addEventListener("close", () => {
  clearTimeout(panelTimer);
  els.panel.classList.remove("is-closing");
  els.panel.style.transition = "";
  els.panel.style.transform = "";
  if (!els.sheet.open && !els.account.open) document.documentElement.classList.remove("has-sheet");
  els.panelBody.innerHTML = "";
  delete els.panel.dataset.kind;
  const dishRef = els.panel.dataset.dish;
  const camRef = els.panel.dataset.cam;
  delete els.panel.dataset.dish;
  delete els.panel.dataset.cam;
  // A dish row redrawn while its sheet was open (a favorite added): focus goes to the new row.
  let back = panelOpener;
  if (dishRef && !back?.isConnected) back = visibleOne(`[data-dish="${CSS.escape(dishRef)}"]`);
  if (camRef && !back?.isConnected) back = visibleOne(`[data-cam="${CSS.escape(camRef)}"]`); // the busy line redrew
  panelOpener = null;
  if (panelRestore && back?.isConnected && !back.hidden && back.getClientRects().length) back.focus({ preventScroll: true });
  panelRestore = true;
});

els.panel.addEventListener("cancel", (event) => {
  event.preventDefault();
  closePanel();
});

els.panel.addEventListener("pointerdown", (event) => {
  pressedPanelBackdrop = event.target === els.panel;
});
dragToDismiss(els.panel, closePanel);

els.panel.addEventListener("click", (event) => {
  if (event.target === els.panel && pressedPanelBackdrop) return closePanel();
  const button = event.target.closest("[data-action]");
  switch (button?.dataset.action) {
    case "close-panel":
      return closePanel();
    case "toggle-fav":
      return toggleFavorite(button);
    case "open-menu":
      return openMenu(button.dataset);
    case "back-alerts":
      return renderAlertsPanel();
    case "settings":
      closePanel({ restoreFocus: false });
      return openSettings(button.dataset.section);
  }
});

function openAlerts() {
  renderAlertsPanel();
  loadAlerts(); // fresh, in case a menu changed
}

function renderAlertsPanel({ refocus = true } = {}) {
  const alerts = state.alerts;
  const html = alerts.length
    ? `<ul class="options alerts">${alerts.map(alertRowHtml).join("")}</ul><p class="note">Tap one to see that menu.</p>`
    : state.prefs.favorites.length
      ? `<div class="card sent" role="status"><span class="sent__icon" aria-hidden="true">${icon("bell")}</span><p>None of your favorites are on the menu in the next 7 days.</p></div>
         <p class="auth-switch">${settingsLinkHtml("Change favorites", "favorites")}</p>`
      : `<div class="card sent" role="status"><span class="sent__icon" aria-hidden="true">${icon("star")}</span><p>Add favorite dishes and this lists the days they're on the menu.</p></div>
         <p class="auth-switch">${settingsLinkHtml("Add favorites", "favorites")}</p>`;
  openPanel(
    {
      kicker: "Alerts",
      title: alerts.length ? "Your favorites on the menu" : "Nothing on the menu yet",
      intro: "When a dish you starred is served at your halls in the next 7 days.",
      html,
      kind: "alerts",
    },
    { refocus }
  );
}

function alertRowHtml(a) {
  const date = typeof a.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(a.date) ? a.date : null;
  const dayText = !date ? "" : date === state.todayKey ? "Today" : weekdayShort.format(parseDay(date));
  const label = String(a.label ?? a.meal ?? "").toLowerCase();
  const when = [dayText, label].filter(Boolean).join(dayText === "Today" ? ", " : " ");
  return `
    <li>
      <button class="option" type="button" data-action="open-menu" data-location="${esc(a.location_id)}" data-date="${esc(date ?? "")}" data-meal-key="${esc(a.meal ?? "")}">
        <span class="option__name">${esc(a.name)} at ${esc(a.location_name ?? "")}${SEP}</span>
        <span class="option__meta">${esc(when)}</span>
        <span class="option__end">${icon("chevron-right")}</span>
      </button>
    </li>`;
}

async function openMenu({ location, date, mealKey }) {
  const id = Number(location);
  if (!Number.isInteger(id) || id < 1 || !/^\d{4}-\d{2}-\d{2}$/.test(date ?? "")) return;
  setPanelContent({ kicker: "Menu", title: "Loading the menu", html: '<p class="loading"><span class="spinner" aria-hidden="true"></span> One moment.</p>', kind: "menu" });
  const epoch = state.epoch;
  try {
    const menu = await api(`/api/dining/menu?location_id=${id}&date=${date}`);
    if (epoch !== state.epoch || !els.panel.open) return;
    renderMenuPanel(menu, mealKey);
  } catch (err) {
    if (!els.panel.open || err.signedOut) return;
    if (err.status === 503) diningLost();
    setPanelContent({
      kicker: "Menu",
      title: "Couldn't load the menu",
      html: `<p class="status is-error" role="alert">${esc(err.message)}</p><button class="btn btn--secondary" type="button" data-action="back-alerts">${icon("chevron-left")}Back to alerts</button>`,
      kind: "menu",
    });
    els.panelTitle.focus();
  }
}

function renderMenuPanel(menu, mealKey) {
  const favorites = new Set(state.prefs.favorites.map((f) => dishKey(f.name)));
  const avoid = new Set(state.prefs.avoid_allergens);
  const diets = state.prefs.diets;
  const meals = Array.isArray(menu.meals) ? menu.meals.filter((m) => m && Array.isArray(m.categories)) : [];
  const row = (item) => {
    const allergens = Array.isArray(item.allergens) ? item.allergens : [];
    const itemDiets = Array.isArray(item.diets) ? item.diets : [];
    return dishRowHtml({
      name: item.name,
      calories: item.calories,
      fav: favorites.has(dishKey(item.name)),
      avoid: allergens.some((a) => avoid.has(a)),
      off: diets.length > 0 && !diets.every((d) => itemDiets.includes(d)),
      avoided: allergens.filter((a) => avoid.has(a)),
      kinds: dietKinds(item),
    });
  };
  const sections = meals
    .map((meal, i) => {
      const categories = meal.categories.filter((c) => c && Array.isArray(c.items));
      const count = categories.reduce((n, c) => n + c.items.filter((item) => item && typeof item.name === "string").length, 0);
      const open = meal.meal === mealKey || (meals.length === 1 && i === 0);
      return `
        <details class="disclosure menu-meal"${open ? " open" : ""}>
          <summary><span>${esc(meal.label ?? meal.meal ?? "Meal")}</span><span class="chip">${count} item${count === 1 ? "" : "s"}</span></summary>
          <div class="disclosure__body">
            <ul class="dishes__groups">${categories
              .map(
                (c) => `
                <li class="dishes__group">
                  <p class="dishes__cat">${esc(c.category ?? "Other")}</p>
                  <ul class="dishes__list">${c.items.filter((item) => item && typeof item.name === "string").map(row).join("")}</ul>
                </li>`
              )
              .join("")}</ul>
          </div>
        </details>`;
    })
    .join("");
  const date = typeof menu.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(menu.date) ? parseDay(menu.date) : null;
  const legend = dishLegendHtml(favorites.size > 0, avoid.size > 0, diets.length > 0);
  setPanelContent({
    kicker: date ? dayFormat.format(date) : "Menu",
    title: menu.location?.name ?? "Menu",
    html: `
      <div class="result__nav"><button class="back" type="button" data-action="back-alerts">${icon("chevron-left")}Alerts</button></div>
      ${legend}
      ${sections || '<p class="options__empty">No menu for that day.</p>'}`,
    kind: "menu",
  });
  els.panelTitle.focus();
}

// ---- the settings widgets, shared by the set-up steps and the Settings sheet

function goalFieldsHtml(prefs) {
  const goal = prefs.calorie_goal;
  const mode = goal == null ? "none" : goal === REFERENCE_KCAL ? "reference" : "own";
  const choice = (value, title, hint) => `
    <label class="choice">
      <input class="visually-hidden" type="radio" name="goal_mode" value="${value}"${mode === value ? " checked" : ""}>
      <span class="choice__ring" aria-hidden="true"></span>
      <span class="choice__text"><span class="choice__title">${title}</span><span class="choice__hint">${hint}</span></span>
    </label>`;
  const goals = GOAL_FIELDS.map(([key, label, unit, max]) => {
    const value = prefs.nutrient_goals?.[key];
    return `
      <div class="form-field goal-field">
        <label class="form-field__label" for="goal-${key}">${label}</label>
        <span class="field-unit"><input id="goal-${key}" class="field" name="${key}" type="number" inputmode="decimal" min="0" max="${max}" step="any" value="${typeof value === "number" ? esc(value) : ""}" aria-describedby="goal-${key}-error"><span aria-hidden="true">${unit}</span></span>
        <p id="goal-${key}-error" class="field-error" hidden></p>
      </div>`;
  }).join("");
  return `
    <fieldset class="choices" data-goal>
      <legend class="form-field__label">Calories a day</legend>
      ${choice("own", "Set my own", "A daily number that suits you.")}
      <div class="choice__field" data-goal-field${mode === "own" ? "" : " hidden"}>
        <label class="form-field__label" for="goal-kcal">Your goal</label>
        <span class="field-unit"><input id="goal-kcal" class="field" name="calorie_goal" type="number" inputmode="numeric" min="${GOAL_MIN}" max="${GOAL_MAX}" step="1" value="${mode === "own" ? esc(goal) : ""}" placeholder="e.g. 2200" aria-describedby="goal-kcal-hint goal-kcal-error"><span aria-hidden="true">kcal</span></span>
        <p id="goal-kcal-hint" class="form-field__hint">From ${nf0.format(GOAL_MIN)} to ${nf0.format(GOAL_MAX)}.</p>
        <p id="goal-kcal-error" class="field-error" hidden></p>
      </div>
      ${choice("reference", `Use the ${nf0.format(REFERENCE_KCAL)} kcal label reference`, "The number food labels use for % Daily Value. Not advice for you in particular.")}
      ${choice("none", "No goal", "Just the ranges of what you ate.")}
    </fieldset>
    <details class="disclosure" data-nutrient-goals>
      <summary>Adjust nutrient goals</summary>
      <div class="disclosure__body">
        <p class="note">Prefilled with the FDA Daily Values. They show on the dashboard once you set a calorie goal. Leave one empty for no goal.</p>
        <div class="goal-grid">${goals}</div>
      </div>
    </details>`;
}

/** { calorie_goal, nutrient_goals } from the fields, or null with an error shown at the field. */
function readGoalFields(root) {
  const mode = $('input[name="goal_mode"]:checked', root)?.value ?? "none";
  const kcalInput = $('input[name="calorie_goal"]', root);
  let calorie_goal = null;
  if (mode === "own") {
    const text = kcalInput.value.trim();
    const n = Number(text);
    if (!text || !Number.isInteger(n) || n < GOAL_MIN || n > GOAL_MAX) {
      setFieldError(kcalInput, `Choose a calorie goal from ${nf0.format(GOAL_MIN)} to ${nf0.format(GOAL_MAX)}.`);
      kcalInput.focus();
      return null;
    }
    calorie_goal = n;
  } else if (mode === "reference") {
    calorie_goal = REFERENCE_KCAL;
  }
  const nutrient_goals = {};
  for (const [key, label, unit, max] of GOAL_FIELDS) {
    const input = $(`input[name="${key}"]`, root);
    if (!input) continue;
    const text = input.value.trim();
    if (!text) {
      nutrient_goals[key] = null;
      continue;
    }
    const n = Number(text);
    if (!Number.isFinite(n) || n < 0 || n > max) {
      revealField(input);
      setFieldError(input, `Choose a ${label.toLowerCase()} goal from 0 to ${nf0.format(max)} ${unit}, or leave it empty.`);
      input.focus();
      return null;
    }
    nutrient_goals[key] = Math.round(n * 10) / 10;
  }
  return { calorie_goal, nutrient_goals };
}

/** A field behind a closed disclosure ("Adjust nutrient goals") can't show its error or take
    focus: open every disclosure around it first. */
function revealField(input) {
  for (let box = input.closest("details"); box; box = box.parentElement?.closest("details")) box.open = true;
}

function hallsPickerHtml(prefs, { note = null } = {}) {
  const locations = state.dining?.locations ?? [];
  const chosen = prefs.dining_halls.filter((id) => locations.some((l) => l.id === id));
  const row = (l) => `
    <li>
      <label class="pick">
        <input class="pick__box" type="checkbox" data-hall value="${l.id}"${chosen.includes(l.id) ? " checked" : ""}>
        <span class="pick__check" aria-hidden="true">${icon("check")}</span>
        <span class="pick__text"><span class="pick__name">${esc(l.name)}</span>${typeof l.area === "string" && l.area ? `<span class="pick__area">${SEP}${esc(l.area)}</span>` : ""}</span>
        <span class="chip pick__main"${chosen[0] === l.id ? "" : " hidden"}>Main</span>
      </label>
    </li>`;
  const commons = locations.filter((l) => l.is_dining_commons === true);
  const others = locations.filter((l) => l.is_dining_commons !== true);
  return `
    <div class="halls" data-halls="${esc(chosen.join(","))}">
      <div class="halls__find">
        <button class="btn btn--soft" type="button" data-action="nearest">${icon("pin")}Find my nearest hall</button>
        <p class="halls__hint">Uses your location once, only to pick a hall. It isn't kept or sent anywhere else.</p>
      </div>
      <div class="halls__note" data-nearest-note>${note ? nearestNoteHtml(note, "picker") : ""}</div>
      ${commons.length ? `<p class="form-field__label">Dining commons</p><ul class="picks">${commons.map(row).join("")}</ul>` : ""}
      ${others.length ? `<details class="disclosure"><summary>More places (${others.length})</summary><div class="disclosure__body"><ul class="picks">${others.map(row).join("")}</ul></div></details>` : ""}
      ${locations.length ? "" : '<p class="note">No places have a menu right now.</p>'}
    </div>`;
}

/** The chosen halls in the order they were chosen; the first is the main one. */
function readHalls(root) {
  const box = $("[data-halls]", root);
  if (!box) return [];
  const checked = new Set($$("[data-hall]:checked", root).map((input) => Number(input.value)));
  const order = box.dataset.halls ? box.dataset.halls.split(",").map(Number) : [];
  const kept = order.filter((id) => checked.has(id));
  for (const id of checked) if (!kept.includes(id)) kept.push(id);
  return kept;
}

function syncHalls(root, input) {
  const box = $("[data-halls]", root);
  const id = Number(input.value);
  let order = (box.dataset.halls ? box.dataset.halls.split(",").map(Number) : []).filter((v) => v !== id);
  if (input.checked) order.push(id);
  box.dataset.halls = order.join(",");
  markMainHall(root, order[0]);
}

function markMainHall(root, mainId) {
  for (const chip of $$(".pick__main", root)) {
    chip.hidden = Number($("[data-hall]", chip.closest(".pick")).value) !== mainId;
  }
}

async function findNearest(root, button) {
  const note = $("[data-nearest-note]", root);
  await withBusy(button, async () => {
    try {
      const [lat, lon] = await devicePosition();
      const near = await fetchNearest(lat, lon);
      if (!near) throw new Error("Couldn't find a dining hall near you right now.");
      if (!root.isConnected) return;
      const input = $(`[data-hall][value="${CSS.escape(String(near.location.id))}"]`, root);
      const box = $("[data-halls]", root);
      if (input) {
        input.checked = true;
        box.dataset.halls = [near.location.id, ...readHalls(root).filter((id) => id !== near.location.id)].join(",");
        markMainHall(root, near.location.id);
      }
      note.innerHTML = nearestNoteHtml(near.note, "picker") + (input ? "" : '<p class="note">That hall has no menu listed right now, so it can\'t be chosen here.</p>');
      if (state.onboard) state.onboard.nearest = near.note;
      autosave(root, "dining");
    } catch (err) {
      if (!root.isConnected || err.signedOut) return;
      note.innerHTML = `<p class="status is-error" role="alert">${esc(err.message)}</p>`;
    }
  });
}

function focusPicker(root) {
  const first = $("[data-hall]", root);
  if (!first) return;
  first.closest(".picks")?.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
  first.focus({ preventScroll: true });
}

function dietChipsHtml(prefs) {
  const chip = (group, name, on) =>
    `<li><button class="chip-btn" type="button" data-chip="${group}" data-value="${esc(name)}" aria-pressed="${on}">${icon("check")}${esc(name)}</button></li>`;
  return `
    <div class="form-field">
      <p class="form-field__label" id="diets-label">Diet</p>
      <ul class="chips" aria-labelledby="diets-label">${DIETS.map((d) => chip("diets", d, prefs.diets.includes(d))).join("")}</ul>
      <p class="form-field__hint">Menu items carry these labels. Dishes without all of yours are marked Not in your diet.</p>
    </div>
    <div class="form-field">
      <p class="form-field__label" id="allergens-label">Allergens to avoid</p>
      <ul class="chips" aria-labelledby="allergens-label">${ALLERGENS.map((a) => chip("avoid_allergens", a, prefs.avoid_allergens.includes(a))).join("")}</ul>
      <p class="form-field__hint">Dishes labelled with these get a mark. A dish without labels isn't necessarily free of them.</p>
    </div>`;
}

function readDiet(root) {
  const pressed = (group) => $$(`[data-chip="${group}"][aria-pressed="true"]`, root).map((chip) => chip.dataset.value);
  return { diets: pressed("diets"), avoid_allergens: pressed("avoid_allergens") };
}

function favoritesHtml(prefs) {
  const n = prefs.favorites.length;
  return `
    <div class="favorites" data-favorites>
      <label class="form-field__label" for="fav-search">Search the campus menus</label>
      <span class="search">${icon("search")}<input id="fav-search" class="field" type="search" data-fav-search autocomplete="off" autocapitalize="none" spellcheck="false" enterkeyhint="search" placeholder="e.g. butter chicken" aria-describedby="fav-hint"></span>
      <p id="fav-hint" class="form-field__hint">Checked against the campus menu.</p>
      <p class="visually-hidden" data-fav-count aria-live="polite"></p>
      <ul class="options" data-dish-results aria-label="Dishes found"></ul>
      <p class="form-field__label">Your favorites <span data-fav-n>(${n})</span></p>
      <ul class="favs" data-fav-list>${prefs.favorites.map((f) => favChipHtml(f.name)).join("")}</ul>
      <p class="favorites__empty" data-fav-empty${n ? " hidden" : ""}>None yet. Search above and tap a dish to add it.</p>
      <p class="favorites__status status" data-fav-status></p>
    </div>`;
}

const favChipHtml = (name) =>
  `<li class="fav" data-fav-name="${esc(name)}"><span class="fav__name">${esc(name)}</span><button class="fav__x" type="button" data-fav-remove aria-label="Remove ${esc(name)}">${icon("close")}</button></li>`;

const readFavorites = (root) => $$("[data-fav-name]", root).map((el) => ({ name: el.dataset.favName }));

function syncFavCount(root) {
  const n = $$("[data-fav-name]", root).length;
  const count = $("[data-fav-n]", root);
  if (count) count.textContent = `(${n})`;
  const empty = $("[data-fav-empty]", root);
  if (empty) empty.hidden = n > 0;
}

function addFavorite(root, button) {
  const name = button.dataset.favAdd;
  const list = $("[data-fav-list]", root);
  const status = $("[data-fav-status]", root);
  if (!name || !list) return;
  const key = dishKey(name);
  if (readFavorites(root).some((f) => dishKey(f.name) === key)) return;
  if (readFavorites(root).length >= MAX_FAVORITES) {
    if (status) status.textContent = `Keep your favorites to ${MAX_FAVORITES} or fewer.`;
    return;
  }
  if (status) status.textContent = "";
  list.insertAdjacentHTML("beforeend", favChipHtml(name));
  button.setAttribute("aria-current", "true");
  button.disabled = true;
  $(".option__end", button).innerHTML = `${icon("check")}${SEP}Added`;
  syncFavCount(root);
  autosave(root, "favorites");
}

function removeFavorite(root, button) {
  const chip = button.closest("[data-fav-name]");
  if (!chip) return;
  const key = dishKey(chip.dataset.favName);
  const next = chip.nextElementSibling?.querySelector("[data-fav-remove]") ?? chip.previousElementSibling?.querySelector("[data-fav-remove]") ?? $("[data-fav-search]", root);
  chip.remove();
  next?.focus();
  syncFavCount(root);
  for (const option of $$("[data-fav-add]", root)) {
    if (dishKey(option.dataset.favAdd) !== key) continue;
    option.disabled = false;
    option.removeAttribute("aria-current");
    $(".option__end", option).innerHTML = icon("plus");
  }
  autosave(root, "favorites");
}

let dishTimer = 0;
let dishSeq = 0;

async function searchDishes(input) {
  const root = input.closest("[data-prefs-root]");
  const list = root && $("[data-dish-results]", root);
  const count = root && $("[data-fav-count]", root);
  if (!list) return;
  const q = input.value.trim();
  const seq = ++dishSeq;
  if (q.length < 2) {
    list.innerHTML = "";
    count.textContent = "";
    return;
  }
  try {
    const found = await api(`/api/dining/dishes?q=${encodeURIComponent(q)}&limit=8`);
    if (seq !== dishSeq || !list.isConnected) return;
    const dishes = Array.isArray(found) ? found.filter((d) => d && typeof d.name === "string") : [];
    const chosen = new Set(readFavorites(root).map((f) => dishKey(f.name)));
    list.innerHTML = dishes.length
      ? dishes.map((d) => dishOptionHtml(d, chosen.has(dishKey(d.name)))).join("")
      : '<li class="options__empty">Nothing on the campus menus matches that.</li>';
    count.textContent = dishes.length ? `${dishes.length} dish${dishes.length === 1 ? "" : "es"} found.` : "Nothing on the campus menus matches that.";
  } catch (err) {
    if (seq !== dishSeq || !list.isConnected || err.signedOut) return;
    if (err.status === 503) diningLost();
    list.innerHTML = `<li class="options__empty is-error">${esc(err.message)}</li>`;
    count.textContent = err.message;
  }
}

function dishOptionHtml(d, chosen) {
  const places = Array.isArray(d.locations) ? d.locations.filter((p) => typeof p === "string").slice(0, 3) : [];
  const meta = [typeof d.calories === "number" ? `${nf0.format(d.calories)} kcal` : null, places.length ? places.join(", ") : null].filter(Boolean).join(" · ");
  return `
    <li>
      <button class="option" type="button" data-fav-add="${esc(d.name)}"${chosen ? ' aria-current="true" disabled' : ""}>
        <span class="option__name">${esc(d.name)}${SEP}</span>
        <span class="option__meta">${esc(meta)}</span>
        <span class="option__end">${chosen ? `${icon("check")}${SEP}Added` : icon("plus")}</span>
      </button>
    </li>`;
}

function alertsFieldsHtml(prefs) {
  const row = (key, title, hint, on) => `
    <label class="switch-row switch-row--plain">
      <span class="switch-row__text">
        <span class="switch-row__title" id="notify-${key}-title">${title}</span>
        <span class="switch-row__hint" id="notify-${key}-hint">${hint}</span>
      </span>
      <input id="notify-${key}" class="switch" type="checkbox" role="switch" data-notify="${key}"${on ? " checked" : ""} aria-labelledby="notify-${key}-title" aria-describedby="notify-${key}-hint">
    </label>`;
  return `
    <div class="switches">
      ${row("in_app", "Tell me when my favorites are on the menu", "A bell in the top bar lists what's coming up this week.", prefs.notify.in_app)}
      ${row("email", "Also email me", emailSending() ? "One short email when they're on, sent only to a confirmed address." : "Email isn't switched on yet, so nothing is sent for now.", prefs.notify.email)}
    </div>`;
}

const readNotify = (root) => ({
  in_app: Boolean($('[data-notify="in_app"]', root)?.checked),
  email: Boolean($('[data-notify="email"]', root)?.checked),
});

function summaryHtml(prefs) {
  const halls = prefs.dining_halls.map(hallNameById).filter(Boolean);
  const n = prefs.favorites.length;
  const rows = [
    ["Daily goal", prefs.calorie_goal == null ? "No calorie goal" : `${amount(prefs.calorie_goal, "kcal")} kcal a day`],
    diningOn() ? ["Dining halls", halls.length ? halls.join(", ") : "None chosen"] : null,
    ["Diet", prefs.diets.length ? prefs.diets.join(", ") : "No diet labels"],
    ["Allergens to avoid", prefs.avoid_allergens.length ? prefs.avoid_allergens.join(", ") : "None"],
    diningOn() ? ["Favorites", n ? `${n} dish${n === 1 ? "" : "es"}` : "None yet"] : null,
    diningOn() ? ["Alerts", prefs.notify.in_app ? `In the app${prefs.notify.email ? " and by email" : ""}` : "Off"] : null,
  ].filter(Boolean);
  return `
    <dl class="summary">${rows.map(([k, v]) => `<div class="summary__row"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    <p class="note">Change any of this later in Settings.</p>`;
}

// ---- what the widgets do, in the set-up steps and in Settings alike

const saveTimers = new Map();
const statusTimers = new WeakMap();

function flashStatus(el, text) {
  if (!el) return;
  el.textContent = text;
  el.classList.remove("is-error");
  clearTimeout(statusTimers.get(el));
  statusTimers.set(el, setTimeout(() => el.textContent === text && (el.textContent = ""), 2200));
}

/** Settings saves as you go, a moment after the last change; the set-up steps save on Next. */
function autosave(root, section) {
  if (root.dataset.mode !== "settings") return;
  clearTimeout(saveTimers.get(section));
  saveTimers.set(section, setTimeout(() => saveSection(root, section), 350));
}

function readSection(section, root) {
  switch (section) {
    case "goals":
      return readGoalFields(root);
    case "dining":
      return { dining_halls: readHalls(root) };
    case "diet":
      return readDiet(root);
    case "favorites":
      return { favorites: readFavorites(root) };
    case "alerts":
      return { notify: readNotify(root) };
    default:
      return {};
  }
}

const retrySaves = new Map(); // section -> the changes its last save failed on, for "Try again"

async function saveSection(root, section) {
  if (!root.isConnected) return;
  const changes = readSection(section, root);
  if (!changes) return;
  const status = $("[data-status]", root);
  try {
    await savePrefs(changes);
    retrySaves.delete(section);
    if (status?.isConnected) flashStatus(status, "Saved.");
  } catch (err) {
    if (err.signedOut || !root.isConnected) return;
    // The server still has what it had, so the widgets go back to that, with the reason and a
    // way to send the change again. Focus that was here (or lost: a disabled dish option drops
    // it) lands on Try again when its control is gone.
    const hadFocus = root.contains(document.activeElement) || document.activeElement === document.body;
    retrySaves.set(section, changes);
    rerenderSection(root, section);
    showSaveFailed(root, err.message);
    if (hadFocus && !root.contains(document.activeElement)) $('[data-action="retry-save"]', root)?.focus({ preventScroll: true });
  }
}

/** A Settings section's widgets, from the saved preferences. */
function sectionBodyHtml(section, prefs) {
  switch (section) {
    case "dining":
      return hallsPickerHtml(prefs);
    case "diet":
      return dietChipsHtml(prefs);
    case "favorites":
      return favoritesHtml(prefs);
    case "alerts":
      return alertsFieldsHtml(prefs);
    default:
      return null;
  }
}

/** A selector for the control that has focus, so the same one can take focus after a redraw. */
function controlSelector(el) {
  if (!(el instanceof Element)) return null;
  const quote = (value) => `"${String(value).replace(/["\\]/g, "\\$&")}"`;
  if (el.matches("[data-chip]")) return `[data-chip=${quote(el.dataset.chip)}][data-value=${quote(el.dataset.value)}]`;
  if (el.matches("[data-hall]")) return `[data-hall][value=${quote(el.value)}]`;
  if (el.matches("[data-notify]")) return `[data-notify=${quote(el.dataset.notify)}]`;
  if (el.matches("[data-fav-add]")) return `[data-fav-add=${quote(el.dataset.favAdd)}]`;
  if (el.closest("[data-favorites]")) return "[data-fav-search]";
  if (el.matches('[data-action="nearest"]')) return '[data-action="nearest"]';
  return null;
}

/** Draw a Settings section again from the saved preferences, keeping focus on the same control.
    The nearest-hall note and the dish search (its words and results) stay as they were. */
function rerenderSection(root, section) {
  const prefs = state.prefs;
  const body = prefs && root.dataset.mode === "settings" ? sectionBodyHtml(section, prefs) : null;
  if (body == null) return;
  const status = $("[data-status]", root);
  const title = $(".account__section-title", root);
  const focus = root.contains(document.activeElement) ? controlSelector(document.activeElement) : null;
  const keep = {
    note: $("[data-nearest-note]", root)?.innerHTML ?? "",
    query: $("[data-fav-search]", root)?.value ?? "",
    results: $("[data-dish-results]", root)?.innerHTML ?? "",
  };
  for (const node of [...root.children]) if (node !== title && node !== status) node.remove();
  if (status) status.insertAdjacentHTML("beforebegin", body);
  else root.insertAdjacentHTML("beforeend", body);
  const note = $("[data-nearest-note]", root);
  if (note && keep.note) note.innerHTML = keep.note; // the app's own markup from a moment ago
  const search = $("[data-fav-search]", root);
  if (search && keep.query) {
    search.value = keep.query;
    $("[data-dish-results]", root).innerHTML = keep.results;
    syncFavOptions(root);
  }
  if (focus) root.querySelector(focus)?.focus({ preventScroll: true });
}

/** The "Added" marks in the dish results follow the favorites list. */
function syncFavOptions(root) {
  const chosen = new Set(readFavorites(root).map((f) => dishKey(f.name)));
  for (const option of $$("[data-fav-add]", root)) {
    const on = chosen.has(dishKey(option.dataset.favAdd));
    option.disabled = on;
    if (on) option.setAttribute("aria-current", "true");
    else option.removeAttribute("aria-current");
    $(".option__end", option).innerHTML = on ? `${icon("check")}${SEP}Added` : icon("plus");
  }
}

/** The section's status line: why the save failed, and Try again. */
function showSaveFailed(root, message) {
  const status = $("[data-status]", root);
  if (!status) return;
  clearTimeout(statusTimers.get(status));
  const text = document.createElement("span");
  text.textContent = `Couldn't save that. ${message}`;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "link-btn";
  retry.dataset.action = "retry-save";
  retry.textContent = "Try again";
  status.replaceChildren(text, retry);
  status.classList.add("is-error");
}

async function retrySave(root, button) {
  const section = root.dataset.section;
  const changes = retrySaves.get(section);
  if (!changes) return;
  await withBusy(button, async () => {
    try {
      await savePrefs(changes);
      retrySaves.delete(section);
      if (!root.isConnected) return;
      rerenderSection(root, section);
      flashStatus($("[data-status]", root), "Saved.");
      $(".account__section-title", root)?.focus({ preventScroll: true });
    } catch (err) {
      if (err.signedOut || !root.isConnected) return;
      showSaveFailed(root, err.message);
      $('[data-action="retry-save"]', root)?.focus({ preventScroll: true });
    }
  });
}

async function saveGoalsForm(form) {
  clearFormErrors(form);
  const changes = readGoalFields(form);
  if (!changes) return;
  const button = $('button[type="submit"]', form);
  const root = form.closest("[data-prefs-root]") ?? form;
  await withBusy(button, async () => {
    try {
      await savePrefs(changes);
      flashStatus($("[data-status]", root), "Saved.");
    } catch (err) {
      showPrefsError(form, err);
    }
  });
}

/** A 400 names its field (nutrient_goals.protein_g, notify.email, favorites); the message goes
    next to that field when there is one, else above the button. */
function showPrefsError(form, err) {
  if (err.signedOut || !form.isConnected) return;
  let name = err.field ?? "";
  if (name.startsWith("nutrient_goals.")) name = name.slice("nutrient_goals.".length);
  const input = name ? form.querySelector(`input[name="${CSS.escape(name)}"]`) : null;
  if (input && !input.classList.contains("visually-hidden")) {
    revealField(input);
    // The own-number calorie field stays hidden behind the other two choices; then the message
    // goes above the button instead of into a field nobody can see.
    if (input.checkVisibility?.() ?? true) {
      setFieldError(input, err.message);
      input.focus();
      return;
    }
  }
  const alert = $(".form-error", form);
  if (!alert) return toast(err.message);
  setFormAlert(form, err.message);
  alert.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
}

function prefsClick(event) {
  const root = event.target.closest("[data-prefs-root]");
  if (!root) return;
  const chip = event.target.closest("[data-chip]");
  if (chip) {
    chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
    return autosave(root, "diet");
  }
  const add = event.target.closest("[data-fav-add]");
  if (add) return addFavorite(root, add);
  const remove = event.target.closest("[data-fav-remove]");
  if (remove) return removeFavorite(root, remove);
  const action = event.target.closest("[data-action]");
  switch (action?.dataset.action) {
    case "nearest":
      return findNearest(root, action);
    case "focus-picker":
      return focusPicker(root);
    case "retry-save":
      return retrySave(root, action);
  }
}

function prefsChange(event) {
  const root = event.target.closest("[data-prefs-root]");
  const input = event.target;
  if (!root || !(input instanceof HTMLInputElement)) return;
  if (input.name === "goal_mode") {
    const field = $("[data-goal-field]", root);
    const own = input.value === "own";
    if (field) field.hidden = !own;
    if (own) $('input[name="calorie_goal"]', root)?.focus();
    return;
  }
  if (input.matches("[data-hall]")) {
    syncHalls(root, input);
    return autosave(root, "dining");
  }
  if (input.matches("[data-notify]")) return autosave(root, "alerts");
}

function prefsInput(event) {
  const input = event.target.closest?.("[data-fav-search]");
  if (!input) return;
  clearTimeout(dishTimer);
  dishTimer = setTimeout(() => searchDishes(input), 250);
}

/** Enter in the dish search searches now instead of submitting the step. */
function prefsKeydown(event) {
  const input = event.target.closest?.("[data-fav-search]");
  if (!input || event.key !== "Enter") return;
  event.preventDefault();
  clearTimeout(dishTimer);
  searchDishes(input);
  if (!finePointer.matches) input.blur();
}

els.account.addEventListener("keydown", prefsKeydown);

// ---- Settings: the account sheet with the preferences in it

function accountIdHtml(user) {
  const created = new Date(user.created_at);
  const since = Number.isNaN(created.getTime()) ? "" : `<p class="account__since">Member since ${monthYear.format(created)}</p>`;
  return `
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
             <p>${emailSending() ? `We sent a link to ${esc(user.email)}. Open it to confirm your email.` : `${NOT_SWITCHED_ON}, so the confirmation link may not arrive.`}</p>
             <div class="banner__actions">${confirmActionsHtml()}</div>
             <p class="banner__status" role="status"></p>
           </div>`
    }
    ${since}`;
}

function passwordSectionHtml(user) {
  return `
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
    </section>`;
}

/** System, Light or Dark, saved on this device. The theme script in index.html applies the choice
    when a data-theme-input control changes. */
function appearanceSectionHtml() {
  const choice = window.MealScholarTheme?.choice() ?? "system";
  const option = (value, label, glyph) => `
    <input id="settings-theme-${value}" class="seg__input visually-hidden" type="radio" name="settings-theme" value="${value}" data-theme-input${choice === value ? " checked" : ""}>
    <label class="seg__opt" for="settings-theme-${value}">${icon(glyph)}${label}</label>`;
  return `
    <section class="account__section" id="settings-appearance" aria-labelledby="settings-appearance-title">
      <h3 id="settings-appearance-title" class="account__section-title" tabindex="-1">Appearance</h3>
      <div class="seg" role="radiogroup" aria-labelledby="settings-appearance-title">
        ${option("system", "System", "monitor")}${option("light", "Light", "sun")}${option("dark", "Dark", "moon")}
      </div>
    </section>`;
}

/** Settings → Your data: clear history, and delete everything (no account) or the account itself.
    Each asks once more, in place, before anything is deleted. */
function dataSectionHtml(user) {
  const row = (id, title, note, label, danger) => `
    <div class="data-row">
      <div class="data-row__text">
        <p class="data-row__title">${title}</p>
        <p class="data-row__note">${note}</p>
      </div>
      <button class="btn ${danger ? "btn--danger" : "btn--secondary"} data-row__btn" type="button" data-action="ask-data" data-ask="${id}" aria-expanded="false" aria-controls="${id}">${label}</button>
    </div>`;
  const buttons = (action, label) => `
    <div class="delete__row">
      <button class="btn btn--danger" type="${action ? "button" : "submit"}"${action ? ` data-action="${action}"` : ""}>${icon("trash")}${label}</button>
      <button class="btn btn--secondary" type="button" data-action="cancel-data">Cancel</button>
    </div>`;
  const ask = (id, text, action, label) => `
    <div class="data-ask" id="${id}" hidden>
      <p class="delete__ask">${text}</p>
      ${buttons(action, label)}
    </div>`;
  const last = user
    ? `${row("data-delete-account", "Delete account", "Deletes your account and everything in it: meals and their photos, goal, dining halls, favorites and settings.", "Delete account", true)}
      <form class="data-ask form form--plain" id="data-delete-account" data-form="delete-account" novalidate hidden>
        <p class="delete__ask">This can't be undone. Enter your password to delete your account.</p>
        <input class="visually-hidden" type="email" name="username" autocomplete="username" value="${esc(user.email)}" tabindex="-1" aria-hidden="true" readonly>
        ${passwordFieldHtml({ id: "delete-password", name: "password", label: "Password", autocomplete: "current-password" })}
        <p class="form-error" role="alert" hidden></p>
        ${buttons(null, "Delete account")}
      </form>`
    : `${row("data-erase", "Delete all my data", "Deletes every meal and its photo, your goal, dining halls, favorites and settings from the server, then starts over.", "Delete all", true)}
      ${ask("data-erase", "Delete everything MealScholar keeps for you? This can't be undone.", "erase-data", "Delete everything")}`;
  return `
    <section class="account__section data-zone" id="settings-data" aria-labelledby="settings-data-title">
      <h3 id="settings-data-title" class="account__section-title" tabindex="-1">Your data</h3>
      ${row("data-clear", "Clear history", "Deletes every meal you've logged and its photo, from the server too. Your goal and settings stay.", "Clear history", false)}
      ${ask("data-clear", "Delete every meal you've logged and its photo? This can't be undone.", "clear-history", "Delete all meals")}
      ${last}
      <p class="settings__status status" role="status" data-data-status></p>
    </section>`;
}

function renderSettingsBody() {
  const user = state.localUser ? null : state.user;
  const prefs = state.prefs;
  if (!prefs) return;
  els.accountKicker.textContent = user ? "Settings" : "MealScholar";
  els.accountTitle.textContent = user ? user.name || "Your account" : "Settings";
  els.accountIntro.textContent = diningOn() ? "Your goal, your halls and diet, your alerts and how the app looks." : "Your goal, your diet and how the app looks.";
  els.accountIntro.hidden = false;
  const section = (id, title, body) => `
    <section class="account__section settings" id="settings-${id}" aria-labelledby="settings-${id}-title" data-prefs-root data-mode="settings" data-section="${id}">
      <h3 id="settings-${id}-title" class="account__section-title" tabindex="-1">${title}</h3>
      ${body}
      <p class="settings__status status" role="status" data-status></p>
    </section>`;
  const goals = section(
    "goals",
    "Daily goal",
    `<form class="form form--plain" data-form="goals" novalidate>
       ${goalFieldsHtml(prefs)}
       <p class="form-error" role="alert" hidden></p>
       <button class="btn btn--primary btn--block" type="submit">Save goal</button>
     </form>`
  );
  const dining = diningOn();
  const halls = dining ? section("dining", "Dining halls", hallsPickerHtml(prefs)) : "";
  const diet = section("diet", "Diet and allergies", dietChipsHtml(prefs));
  const favorites = dining ? section("favorites", "Favorite foods", favoritesHtml(prefs)) : "";
  const alerts = dining ? section("alerts", "Alerts", alertsFieldsHtml(prefs)) : "";
  const noDining = dining ? "" : `<p class="note settings__nodining">Campus menus aren't connected on this server, so dining halls, favorites and alerts are off.</p>`;
  els.accountBody.innerHTML = `
    ${user ? accountIdHtml(user) : '<p class="note">You\'re using MealScholar on this device without an account. These settings stay on this server.</p>'}
    ${goals}${halls}${diet}${favorites}${alerts}${noDining}
    ${appearanceSectionHtml()}
    ${user ? passwordSectionHtml(user) : ""}
    <p class="account__status status" role="status"></p>
    <button class="btn btn--soft btn--block" type="button" data-install${installable() ? "" : " hidden"}>${icon("home")}Add to Home Screen</button>
    ${user ? `<button class="btn btn--secondary btn--block" type="button" data-action="sign-out">${icon("logout")}Sign out</button>` : ""}
    ${dataSectionHtml(user)}`;
}

// ----------------------------------------------------------- onboarding
// Right after creating an account, and on the first sign-in while prefs.onboarded is false: one
// screen per step, every step skippable, one PATCH per step as the person moves on, and
// onboarded: true at the end or at "Skip for now".

const STEP_TITLES = {
  name: "Your name",
  goal: "Daily goal",
  halls: "Where you eat",
  diet: "Diet and allergies",
  favorites: "Favorite foods",
  alerts: "Alerts",
  done: "All set",
};

const STEP_LEDES = {
  name: "This is how the dashboard greets you.",
  goal: "Optional. With a goal, the dashboard shows what's left for the day. Without one, it shows the ranges of what you ate.",
  halls: "Pick the dining halls you eat at most. The first one is your main hall.",
  diet: "Dishes that don't fit your diet are marked on menus, and allergens you avoid get a mark.",
  favorites: "We'll tell you when these are on the menu.",
  alerts: "How to hear about your favorites.",
  done: "Here's what you chose. You can change any of it in Settings.",
};

function onboardingSteps() {
  const dining = diningOn();
  return ["name", "goal", ...(dining ? ["halls"] : []), "diet", ...(dining ? ["favorites", "alerts"] : []), "done"];
}

function startOnboarding() {
  dropSheets();
  state.onboard = { steps: onboardingSteps(), at: 0, nearest: null };
  document.body.dataset.screen = "onboard";
  els.auth.hidden = true;
  els.auth.innerHTML = "";
  els.onboard.hidden = false;
  updateAccountUi();
  updateBell();
  renderOnboarding();
  syncHistory();
}

function stepBodyHtml(step) {
  const prefs = state.prefs;
  switch (step) {
    case "name": {
      const user = state.localUser ? null : state.user;
      const name = (user?.name ?? "").trim();
      return `
        <div class="form-field">
          <p class="form-field__label">Name</p>
          <p class="onboard__name">${name ? esc(name) : "No name yet"}</p>
          <p class="form-field__hint">${
            user
              ? name
                ? "From your account."
                : "Your account has no name, so the dashboard greets you without one."
              : "You're using MealScholar on this device without an account."
          }</p>
        </div>`;
    }
    case "goal":
      return goalFieldsHtml(prefs);
    case "halls":
      return hallsPickerHtml(prefs, { note: state.onboard?.nearest ?? null });
    case "diet":
      return dietChipsHtml(prefs);
    case "favorites":
      return favoritesHtml(prefs);
    case "alerts":
      return alertsFieldsHtml(prefs);
    case "done":
      return summaryHtml(prefs);
    default:
      return "";
  }
}

function renderOnboarding() {
  const ob = state.onboard;
  if (!ob) return;
  const step = ob.steps[ob.at];
  const n = ob.steps.length;
  const last = step === "done";
  const dots = ob.steps.map((_, i) => `<li class="dots__dot${i < ob.at ? " is-done" : i === ob.at ? " is-current" : ""}"></li>`).join("");
  els.onboard.innerHTML = `
    <div class="onboard__screen" data-step="${step}">
      <div class="onboard__nav">
        ${ob.at > 0 ? `<button class="back" type="button" data-onboard="back">${icon("chevron-left")}Back</button>` : '<span class="onboard__spacer"></span>'}
        <ol class="dots" aria-hidden="true">${dots}</ol>
        ${last ? '<span class="onboard__spacer"></span>' : '<button class="link-btn" type="button" data-onboard="skip">Skip for now</button>'}
      </div>
      <header class="auth-head">
        <p class="eyebrow">Step ${ob.at + 1} of ${n}</p>
        <h1 id="onboard-title" class="view-title" tabindex="-1">${esc(STEP_TITLES[step])}</h1>
        <p class="lede">${STEP_LEDES[step]}</p>
      </header>
      <form class="card form onboard__form" data-prefs-root data-mode="onboard" data-step="${step}" novalidate>
        ${stepBodyHtml(step)}
        <p class="form-error" role="alert" hidden></p>
        <button class="visually-hidden" type="submit" tabindex="-1">${last ? "Go to my dashboard" : "Next"}</button>
      </form>
      <div class="onboard__actions">
        <button class="cta cta--block" type="button" data-onboard="${last ? "finish" : "next"}"><span class="cta__pill">${last ? "Go to my dashboard" : "Next"}</span><span class="cta__dot" aria-hidden="true">${icon("arrow")}</span></button>
      </div>
    </div>`;
  window.scrollTo(0, 0);
  replayEntrance(els.onboard);
  $("#onboard-title", els.onboard).focus({ preventScroll: true });
}

function finishOnboarding(message) {
  state.onboard = null;
  enterApp({ focus: true });
  toast(message);
}

async function onboardNav(action, button) {
  const ob = state.onboard;
  if (!ob) return;
  if (action === "back") {
    ob.at = Math.max(0, ob.at - 1);
    return renderOnboarding();
  }
  const form = $("[data-prefs-root]", els.onboard);
  const step = ob.steps[ob.at];
  if (action === "skip") {
    clearFormErrors(form);
    await withBusy(button, async () => {
      try {
        await savePrefs({ onboarded: true });
      } catch (err) {
        // Not saved, so the steps would come back next time: say why and stay, like Next does.
        if (!err.signedOut && state.onboard) showPrefsError(form, err);
        return;
      }
      if (state.onboard) finishOnboarding("Set this up any time in Settings.");
    });
    return;
  }
  clearFormErrors(form);
  const changes = step === "done" ? { onboarded: true } : readSection(step === "halls" ? "dining" : step === "goal" ? "goals" : step, form);
  if (!changes) return; // an error is showing at a field
  await withBusy(button, async () => {
    let saved = true;
    try {
      if (Object.keys(changes).length) await savePrefs(changes);
    } catch (err) {
      saved = false;
      showPrefsError(form, err);
    }
    if (!saved || !state.onboard) return;
    if (action === "finish") return finishOnboarding("You're all set.");
    ob.at += 1;
    renderOnboarding();
  });
}

els.onboard.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-onboard]");
  if (nav) return onboardNav(nav.dataset.onboard, nav);
  prefsClick(event);
});

els.onboard.addEventListener("submit", (event) => {
  event.preventDefault();
  const next = $('[data-onboard="next"], [data-onboard="finish"]', els.onboard);
  if (next) onboardNav(next.dataset.onboard, next);
});

els.onboard.addEventListener("input", (event) => {
  clearFieldError(event.target);
  prefsInput(event);
});
els.onboard.addEventListener("change", prefsChange);
els.onboard.addEventListener("keydown", prefsKeydown);

// ----------------------------------------------------------------- wiring

for (const button of $$("[data-pick]")) {
  button.addEventListener("click", () => (button.dataset.pick === "camera" ? els.camera : els.library).click());
}
for (const input of [els.library, els.camera]) {
  input.addEventListener("change", () => input.files[0] && analyze(input.files[0]));
}
for (const button of $$('.dock [data-action="snap"]')) {
  button.addEventListener("click", snapMeal);
}

// A photo dragged onto the app (laptops) is analyzed like a picked one, from any tab.
let dragDepth = 0;
const dragsFiles = (event) =>
  document.body.dataset.screen === "app" && state.ready && !els.sheet.open && !els.account.open && !els.panel.open && Boolean(event.dataTransfer?.types.includes("Files"));
document.addEventListener("dragenter", (event) => {
  if (!dragsFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  document.body.dataset.drop = "on";
});
document.addEventListener("dragover", (event) => {
  if (dragsFiles(event)) event.preventDefault();
});
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) delete document.body.dataset.drop;
});
document.addEventListener("drop", (event) => {
  if (!document.body.dataset.drop) return;
  event.preventDefault();
  dragDepth = 0;
  delete document.body.dataset.drop;
  const file = event.dataTransfer?.files[0];
  if (file) analyze(file);
});

// The location switch is remembered for each person on this device: the next account starts with
// it off, and a person who switched it on finds it on next time.
const LOCATION_KEY = "mealscholar:location";
const ASKED_KEY = "mealscholar:location-asked";
const locationOwner = () => (state.user?.email ? `user:${state.user.email}` : "local");

function rememberLocation(on) {
  try {
    const saved = JSON.parse(localStorage.getItem(LOCATION_KEY) || "{}");
    if (on) saved[locationOwner()] = true;
    else delete saved[locationOwner()];
    localStorage.setItem(LOCATION_KEY, JSON.stringify(saved));
  } catch {}
}

function rememberedLocation() {
  try {
    return JSON.parse(localStorage.getItem(LOCATION_KEY) || "{}")[locationOwner()] === true;
  } catch {
    return false;
  }
}

/** What the browser says about location: "granted", "denied", "prompt", or null when it won't say. */
async function locationPermission() {
  try {
    return (await navigator.permissions?.query({ name: "geolocation" }))?.state ?? null;
  } catch {
    return null;
  }
}

function restoreLocation() {
  if (!rememberedLocation() || !navigator.geolocation) return;
  els.location.checked = true;
  // Permission taken back in the browser's settings since: the switch goes off again.
  const epoch = state.epoch;
  locationPermission().then((permission) => {
    if (permission !== "denied" || epoch !== state.epoch) return;
    els.location.checked = false;
    rememberLocation(false);
  });
}

/** Location just came on: the menus follow the nearest hall now. */
function locationChanged() {
  if (!diningOn()) return;
  state.upcoming = null; // the upcoming meal follows the nearest hall, or the hall chosen in Settings
  loadUpcoming();
  if (state.view === "menu") renderMenu();
  ensureMenus();
}

els.location.addEventListener("change", async () => {
  const target = els.locationStatus;
  if (!els.location.checked) {
    setStatus("", { target });
    rememberLocation(false);
    locationChanged();
    return;
  }
  try {
    await devicePosition(); // ask for permission now, not mid-analysis
    setStatus("Location is on.", { target });
    rememberLocation(true);
    hideLocateAsk();
    locationChanged();
  } catch (err) {
    els.location.checked = false;
    setStatus(err.message, { error: true, target });
  }
});

// ---- the first-time question about location, at the top of Today
// Asked once, in the app's own words, before the browser's prompt. Not now (or any answer) means
// it never asks again on this device; a browser that already allowed or blocked location skips it.

function askedAboutLocation() {
  try {
    return localStorage.getItem(ASKED_KEY) === "1";
  } catch {
    return true; // storage blocked: never nag on every visit
  }
}

async function renderLocateAsk() {
  if (!navigator.geolocation || els.location.checked || askedAboutLocation()) return hideLocateAsk();
  const epoch = state.epoch;
  const permission = await locationPermission();
  if (epoch !== state.epoch || document.body.dataset.screen !== "app") return;
  if (permission === "granted" || permission === "denied" || els.location.checked) return hideLocateAsk();
  els.locateAsk.innerHTML = `
    <section class="card locate-card" aria-labelledby="locate-title">
      <span class="locate-card__icon" aria-hidden="true">${icon("pin")}</span>
      <div class="locate-card__text">
        <h2 id="locate-title" class="locate-card__title">Name the place on your meals</h2>
        <p>Allow location to name the dining hall on your meals; nothing else is stored.</p>
      </div>
      <div class="locate-card__actions">
        <button class="btn btn--primary" type="button" data-action="locate-allow">Allow</button>
        <button class="btn btn--secondary" type="button" data-action="locate-later">Not now</button>
      </div>
    </section>`;
  els.locateAsk.hidden = false;
}

function hideLocateAsk() {
  els.locateAsk.hidden = true;
  els.locateAsk.innerHTML = "";
}

function markAsked() {
  try {
    localStorage.setItem(ASKED_KEY, "1");
  } catch {}
}

function dismissLocateAsk(hadFocus = els.locateAsk.contains(document.activeElement)) {
  markAsked();
  hideLocateAsk();
  if (hadFocus) els.todayTitle.focus({ preventScroll: true });
}

async function allowLocation(button) {
  markAsked();
  // The busy button gives up focus while the browser asks; remember it to land on Today's heading.
  const hadFocus = els.locateAsk.contains(document.activeElement);
  await withBusy(button, async () => {
    try {
      await devicePosition(); // the browser's own prompt
      els.location.checked = true;
      rememberLocation(true);
      toast("Location is on. Meals get the place name.");
      locationChanged();
    } catch (err) {
      toast(`${err.message} You can switch it on later from a photo's options.`);
    }
  });
  dismissLocateAsk(hadFocus);
}

// The laptop dashboard's round "+" button: straight to the photo picker, like the phone's.
els.plus.addEventListener("click", snapMeal);

// The sidebar's Alerts and Settings do what the bell and the avatar do.
els.railAlerts.addEventListener("click", openAlerts);
els.railSettings.addEventListener("click", openAccount);

els.bell.addEventListener("click", openAlerts);

els.capture.addEventListener("click", (event) => {
  if (!event.target.closest('[data-action="leave-capture"]')) return;
  leaveCapture({ focus: true });
  syncHistory();
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
  showStartOverNotice();
  const links = takeLinkTokens();
  bootRoute = hashView();
  // A refresh keeps the account screen that was open: its entry says which one.
  const saved = history.state;
  if (!isAuthEntry(saved)) history.replaceState({ layers: [] }, "", baseUrl);
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
    // No accounts on this server. It may still keep preferences for one local user (sign-in
    // optional); if not, the app is as it always was, starting on Analyze.
    const prefs = await fetchPrefs();
    if (prefs) {
      state.localUser = true;
      enablePrefs(prefs);
      await loadDining();
      loadAlerts();
      if (needsOnboarding()) return startOnboarding();
      return enterApp();
    }
    return enterApp();
  }

  enableAccounts(status);
  loadAuthDraft();
  // A reset link's token stays in this tab until the new password is saved, so a refresh keeps the form.
  if (links.reset) tabStore.set(RESET_KEY, links.reset);
  try {
    state.user = (await api("/api/auth/me")).user;
  } catch (err) {
    if (err.status !== 401) return bootFailed(err);
    state.user = null;
  }
  if (state.user) await setupPrefs();
  const resetToken = links.reset ?? (links.verify ? null : tabStore.get(RESET_KEY));
  if (resetToken) {
    state.resetToken = resetToken;
    showAuth("reset");
    return syncHistory();
  }
  if (links.verify) {
    const confirming = confirmEmail(links.verify); // shows "Confirming your email" right away
    syncHistory();
    return confirming;
  }
  if (state.user) return needsOnboarding() ? startOnboarding() : enterApp();
  showAuthPath(isAuthEntry(saved) ? saved.auth : []);
  syncHistory();
}

init();
