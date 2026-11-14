// FoodSmart web client: plain ES module over the JSON API in web.py; no build step.
// Product rules (README, AGENTS.md): every number is a range until the user weighs the food,
// micronutrients are labelled estimates, and there are no calorie targets.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const els = {
  capture: $("#capture"),
  viewfinder: $("#viewfinder"),
  input: $("#photo"),
  choose: $("#choose"),
  setup: $("#setup"),
  preview: $("#preview"),
  previewImg: $("#preview img"),
  status: $("#capture-status"),
  location: $("#use-location"),
  result: $("#result"),
  today: $("#today-body"),
  todayDate: $("#today-date"),
};

const state = {
  ready: false,
  nutrients: [],
  result: null,
  photoUrl: null,
  open: null, // { index, kind: "weigh" | "food" }
  dismissedQuestion: null,
};

const TIME_SOURCE = { exif: "photo time", device: "device time", upload: "upload time" };

// ------------------------------------------------------------------- api

async function api(path, { method = "GET", body, headers } = {}) {
  let response;
  try {
    response = await fetch(path, { method, body, headers });
  } catch {
    throw new Error("Can't reach the FoodSmart server. Is `foodsmart serve` running?");
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
function rangeHtml(range, unit, format = (v) => amount(v, unit)) {
  const [low, likely, high] = [format(range.low), format(range.likely), format(range.high)];
  const unitHtml = unit ? ` ${esc(unit)}` : "";
  if (low === high) return `<span class="value"><strong>${likely}</strong>${unitHtml}</span>`;
  return `<span class="value"><strong>${likely}</strong>${unitHtml}<span class="value__range"><span class="visually-hidden">, range </span>${low}${TO}${high}</span></span>`;
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

// ---------------------------------------------------------------- bands
// A band is a track with the low–high range and an accent tick at the likely value. Positions are
// transforms only; on re-render each band moves from where it was, so weighing a food visibly
// collapses its range onto one line.

/** `scale` is the value at the track's right end; bands that share a scale are comparable. */
function bandHtml(key, range, { index = 0, scale = range.high * 1.12 || 1 } = {}) {
  const exact = isExact(range) ? " is-exact" : "";
  return `<span class="band${exact}" style="--i:${index}" data-band="${key}" data-scale="${scale}" data-low="${range.low}" data-likely="${range.likely}" data-high="${range.high}" aria-hidden="true"><span class="band__range"></span><span class="band__tick"></span></span>`;
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

// --------------------------------------------------------------- capture

function setViewfinder(mode) {
  els.viewfinder.dataset.state = mode;
  const analyzing = mode === "analyzing";
  $(".viewfinder__empty", els.viewfinder).hidden = analyzing;
  els.preview.hidden = !analyzing;
  els.viewfinder.setAttribute("aria-busy", String(analyzing));
}

function setStatus(message, { error = false } = {}) {
  els.status.textContent = message;
  els.status.classList.toggle("is-error", error);
  els.status.setAttribute("role", error ? "alert" : "status");
}

function renderSetup(status) {
  const steps = [];
  if (!status.gemini_ready) {
    steps.push(
      "<p>Add your Gemini key to <code>.env</code> as <code>GEMINI_API_KEY=…</code>, then restart <code>uv run foodsmart serve</code>.</p>"
    );
  }
  if (!status.food_db_ready) steps.push("<p>Build the USDA database once: <code>uv run foodsmart fdc-import</code></p>");
  els.setup.innerHTML = steps.length ? `<p class="label">Setup needed</p>${steps.join("")}` : "";
  els.setup.hidden = !steps.length;
  state.ready = !steps.length;
  els.choose.disabled = !state.ready;
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

async function analyze(file) {
  if (!state.ready || els.viewfinder.dataset.state === "analyzing") return;
  if (file.type && !file.type.startsWith("image/")) {
    setStatus("That file isn't an image. Choose a JPEG, PNG or HEIC photo.", { error: true });
    return;
  }
  revokePhoto();
  state.photoUrl = URL.createObjectURL(file);
  els.previewImg.src = state.photoUrl;
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
    showResult(result);
    loadToday();
  } catch (err) {
    revokePhoto();
    setStatus(err.message, { error: true });
  } finally {
    setViewfinder("empty");
    els.input.value = "";
  }
}

function revokePhoto() {
  if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
  state.photoUrl = null;
}

function newMeal() {
  revokePhoto();
  state.result = null;
  state.open = null;
  els.result.hidden = true;
  els.result.innerHTML = "";
  els.capture.hidden = false;
  setStatus("");
}

// ---------------------------------------------------------------- result

function showResult(result, { photo = true } = {}) {
  if (!photo) revokePhoto();
  state.result = result;
  state.open = null;
  els.capture.hidden = true;
  els.result.hidden = false;
  renderResult({ fresh: true });
  window.scrollTo({ top: 0 });
  $("#result-title")?.focus({ preventScroll: true });
}

function renderResult({ fresh = false, focus } = {}) {
  const previous = fresh ? new Map() : snapshotBands(els.result);
  els.result.innerHTML = resultHtml(state.result);
  els.result.classList.toggle("result--no-photo", !state.photoUrl);
  els.result.classList.toggle("is-settled", !fresh); // entrance animations play once per meal
  animateBands(els.result, previous);
  if (focus) $(focus, els.result)?.focus();
}

function resultHtml(result) {
  const ctx = result.context;
  const when = wallClock(ctx.taken_at);
  const place = ctx.place ? ` <em>at ${esc(ctx.place.name)}</em>` : "";
  const placeKind = ctx.place
    ? ` · ${esc(ctx.place.kind.replace(/_/g, " "))}${ctx.place.cuisine ? `, ${esc(ctx.place.cuisine.replace(/[_;]/g, " "))}` : ""}`
    : "";
  const meta = [result.id != null ? `Meal #${result.id}` : null, `${dayFormat.format(when)}, ${timeFormat.format(when)}`, TIME_SOURCE[ctx.time_source]]
    .filter(Boolean)
    .join(" · ");

  return `
    <header class="result__head">
      <div class="result__titles">
        <h1 id="result-title" class="result__title" tabindex="-1">${esc(capitalize(ctx.meal_slot))}${place}</h1>
        <p class="label">${meta}${placeKind}</p>
      </div>
      ${totalHtml(result)}
    </header>
    <div class="result__grid">
      ${state.photoUrl ? photoHtml(result) : ""}
      <div class="result__body">
        ${questionHtml(result)}
        ${itemsHtml(result)}
        ${result.items.length ? nutrientsHtml(result) : ""}
        ${notesHtml(result)}
        <div class="result__foot">
          <button class="button button--primary" type="button" data-action="new">Analyze another meal</button>
          <a class="button button--text" href="#today">See today</a>
        </div>
      </div>
    </div>`;
}

/** Big serif low–high calories; one number once every counted food is weighed. */
function kcalRangeHtml(kcal) {
  const [low, high] = [amount(kcal.low, "kcal"), amount(kcal.high, "kcal")];
  const numbers =
    low === high
      ? `<span>${low}</span>`
      : `<span>${low}</span><span class="total__dash" aria-hidden="true">–</span><span class="visually-hidden"> to </span><span>${high}</span>`;
  return `${numbers}<span class="total__unit">kcal</span>`;
}

function totalHtml(result) {
  const kcal = result.totals.energy_kcal;
  if (!result.items.length) {
    return `<div class="total"><p class="total__likely">No food or drink found in this photo. Try another shot with the whole plate in view.</p></div>`;
  }
  if (!kcal) {
    return `<div class="total"><p class="label">Calories, this meal</p><p class="total__likely">Nothing is counted yet. Pick a USDA food for each item below.</p></div>`;
  }
  const [low, likely, high] = [amount(kcal.low, "kcal"), amount(kcal.likely, "kcal"), amount(kcal.high, "kcal")];
  const exact = low === high;
  const weighable = result.items.some((r) => r.match && !isExact(itemGrams(r.item)));
  const answered = result.answer ? `<p class="answered">You said: “${esc(result.answer)}”. The foods and ranges below include it.</p>` : "";
  return `
    <div class="total">
      <p class="label">Calories, this meal</p>
      <p class="total__value">${kcalRangeHtml(kcal)}</p>
      ${bandHtml("total-kcal", kcal)}
    </div>
    <div class="total__aside">
      <p class="total__likely">${
        exact
          ? "Every counted food is weighed, so this is a single number."
          : `Most likely <strong>${likely}&nbsp;kcal</strong>.${weighable ? " Weigh any food to narrow the range." : ""}`
      }</p>
      ${answered}
      ${state.photoUrl ? "" : '<p class="answered">Photos are never stored, so saved meals appear without one.</p>'}
    </div>`;
}

function photoHtml(result) {
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
          <img src="${state.photoUrl}" alt="Your meal photo, with each detected food outlined and numbered">
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
      <p class="label">One answer narrows the range</p>
      <p id="question-text" class="question__text">${esc(result.question)}</p>
      <div class="question__row">
        <label class="visually-hidden" for="answer">Your answer</label>
        <input id="answer" class="field" name="answer" type="text" required maxlength="500" autocomplete="off" placeholder="e.g. fried in oil">
        <button class="button button--primary" type="submit">Answer</button>
        <button class="button button--text" type="button" data-action="dismiss-question">Not now</button>
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
      <div class="block__head">
        <h2 id="foods-title" class="block__title">Foods</h2>
        <p class="label">${result.items.length} found</p>
      </div>
      <ol class="items">${result.items.map((r, i) => itemHtml(r, i, gramsScale)).join("")}</ol>
    </section>`;
}

function itemHtml(r, i, gramsScale) {
  const { item, match } = r;
  const range = itemGrams(item);
  const weighed = isExact(range);
  const kcal = r.nutrients.energy_kcal;
  const open = state.open?.index === i ? state.open.kind : null;
  const method = item.cooking_method === "unknown" ? "" : capitalize(item.cooking_method.replace(/_/g, " "));
  const meta = [
    method && esc(method),
    weighed ? "Weighed" : null,
    item.hidden_fat_likely ? '<span class="flag">Likely hidden oil or butter</span>' : null,
  ].filter(Boolean);
  const energy = match
    ? kcal
      ? rangeHtml(kcal, "kcal")
      : '<span class="value__range">No energy data</span>'
    : '<span class="tag">Not counted</span>';
  const matchLine = match
    ? `USDA · ${esc(match.description)} · ${esc(match.data_type)} #${match.fdc_id}`
    : "No close USDA match, so this food isn't in the totals yet. Pick one to count it.";
  const panelId = `panel-${i}`;
  return `
    <li class="item" data-index="${i}" style="--i:${i}">
      <span class="item__n" aria-hidden="true">${i + 1}</span>
      <div class="item__main">
        <div class="item__head">
          <h3 class="item__name">${esc(item.name)}</h3>
          <p>${energy}</p>
        </div>
        ${meta.length ? `<p class="item__meta">${meta.join(" · ")}</p>` : ""}
        <div class="item__grams">${bandHtml(`grams-${i}`, range, { scale: gramsScale })}${rangeHtml(range, "g", grams)}</div>
        <p class="item__match">${matchLine}</p>
        <div class="item__actions">
          <button class="button button--text" type="button" data-action="weigh" data-index="${i}" aria-expanded="${open === "weigh"}"${open === "weigh" ? ` aria-controls="${panelId}"` : ""}>${weighed ? "Change weight" : "Weigh it"}</button>
          <button class="button button--text" type="button" data-action="food" data-index="${i}" aria-expanded="${open === "food"}"${open === "food" ? ` aria-controls="${panelId}"` : ""}>${match ? "Change food" : "Pick a USDA food"}</button>
        </div>
        ${open === "weigh" ? weighPanelHtml(r, i, panelId) : ""}
        ${open === "food" ? foodPanelHtml(r, i, panelId) : ""}
      </div>
    </li>`;
}

function weighPanelHtml(r, i, id) {
  return `
    <form id="${id}" class="panel" data-form="weigh" data-index="${i}">
      <label class="panel__label" for="grams-${i}">Exact weight of ${esc(r.item.name)}</label>
      <div class="panel__row">
        <span class="field-unit">
          <input id="grams-${i}" class="field" name="grams" type="number" inputmode="numeric" min="1" max="10000" step="1" required placeholder="${r.item.grams_likely}">
          <span aria-hidden="true">g</span>
        </span>
        <button class="button button--primary button--sm" type="submit">Set weight</button>
      </div>
      <p class="panel__hint">Collapses this food's range to one number. FoodSmart remembers it as your usual portion.</p>
      <p class="panel__error" role="alert"></p>
    </form>`;
}

function foodPanelHtml(r, i, id) {
  const options = [r.match, ...r.alternatives].filter(Boolean);
  const list = options.length
    ? options.map((m) => optionHtml(m, r.match?.fdc_id === m.fdc_id)).join("")
    : '<li class="options__empty">No suggestions. Search below.</li>';
  return `
    <div id="${id}" class="panel" data-index="${i}">
      <p class="panel__label">${r.match ? "Closest USDA foods" : "Suggestions (loose matches)"}</p>
      <ul class="options">${list}</ul>
      <label class="panel__label" for="search-${i}">Search USDA FoodData Central</label>
      <input id="search-${i}" class="field" type="search" data-search="${i}" autocomplete="off" placeholder="e.g. rice, white, cooked">
      <ul class="options" data-results="${i}"></ul>
      <p class="panel__error" role="alert"></p>
    </div>`;
}

function optionHtml(food, current = false) {
  const kcal = food.energy_kcal_per_100g;
  const meta = [food.data_type, kcal != null ? `${nf0.format(kcal)} kcal/100 g` : null].filter(Boolean).join(" · ");
  return `
    <li>
      <button class="option" type="button" data-action="pick" data-fdc="${food.fdc_id}"${current ? ' aria-current="true" disabled' : ""}>
        <span class="option__name">${esc(food.description)}${current ? ' <span class="visually-hidden">(current match)</span>' : ""}</span>
        <span class="option__meta">${esc(meta)}</span>
      </button>
    </li>`;
}

function nutrientRowsHtml(totals, dv, { micro, prefix, skip = [] }) {
  return state.nutrients
    .filter((n) => n.micro === micro && totals[n.key] && !skip.includes(n.key))
    .map(
      (n, j) => `
      <li class="nutrient">
        <span class="nutrient__label">${esc(n.label)}</span>
        ${
          dv[n.key]
            ? bandHtml(`${prefix}-${n.key}`, dv[n.key], { index: j, scale: Math.max(100, dv[n.key].high * 1.05) })
            : '<span class="nutrient__nodv">No Daily Value</span>'
        }
        <span class="nutrient__value">${rangeHtml(totals[n.key], n.unit)}</span>
        <span class="nutrient__dv">${dv[n.key] ? dvHtml(dv[n.key]) : ""}</span>
      </li>`
    )
    .join("");
}

function nutrientsHtml(result) {
  const macros = nutrientRowsHtml(result.totals, result.daily_value_pct, { micro: false, prefix: "n", skip: ["energy_kcal"] });
  const micros = nutrientRowsHtml(result.totals, result.daily_value_pct, { micro: true, prefix: "n" });
  if (!macros && !micros) return "";
  const partial = result.partial_nutrients.length
    ? `<p class="block__note">Some foods have no USDA data for ${esc(
        result.partial_nutrients.map((k) => labelOf(k).toLowerCase()).join(", ")
      )}, so those totals undercount.</p>`
    : "";
  return `
    <section class="block" aria-labelledby="nutrients-title">
      <div class="block__head">
        <h2 id="nutrients-title" class="block__title">Nutrients</h2>
        <p class="label">Bars: % Daily Value</p>
      </div>
      <ul class="nutrients">${macros}</ul>
      <p class="block__note">Bars run to 100% of the FDA Daily Value, the reference on food labels. It is not a goal.</p>
      ${micros ? microDetailsHtml(micros) : ""}
      ${partial}
    </section>`;
}

function microDetailsHtml(rows) {
  return `
    <details class="micro">
      <summary><span class="micro__title">Micronutrients <span class="label">Estimates</span></span></summary>
      <p class="block__note">Photos can't show vitamins and minerals well. Treat these as rough estimates; a weekly trend says more than any single meal or day.</p>
      <ul class="nutrients">${rows}</ul>
    </details>`;
}

function notesHtml(result) {
  const notes = [...result.context.notes, ...result.notes];
  if (!notes.length) return "";
  return `<ul class="notes" aria-label="Notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
}

const labelOf = (key) => state.nutrients.find((n) => n.key === key)?.label ?? key;
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

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

async function correct(index, body, errorRoot, button, kind) {
  await withBusy(button, async () => {
    try {
      state.result = await api(`/api/meals/${state.result.id}/items/${index}`, { method: "PATCH", ...jsonBody(body) });
      state.open = null;
      renderResult({ focus: `[data-action="${kind}"][data-index="${index}"]` });
      loadToday();
    } catch (err) {
      $(".panel__error", errorRoot).textContent = err.message;
    }
  });
}

let searchTimer = 0;
let searchSeq = 0;

async function searchFoods(input) {
  const index = Number(input.dataset.search);
  const list = $(`[data-results="${index}"]`, els.result);
  const q = input.value.trim();
  const seq = ++searchSeq;
  if (q.length < 2) {
    list.innerHTML = "";
    return;
  }
  try {
    const found = await api(`/api/foods?q=${encodeURIComponent(q)}&limit=6`);
    if (seq !== searchSeq) return;
    const current = state.result.items[index].match?.fdc_id;
    list.innerHTML = found.foods.length
      ? (found.exact ? "" : '<li class="options__empty">No exact match; closest results:</li>') +
        found.foods.map((f) => optionHtml(f, f.fdc_id === current)).join("")
      : '<li class="options__empty">No USDA foods match that.</li>';
  } catch (err) {
    list.innerHTML = `<li class="options__empty">${esc(err.message)}</li>`;
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

els.result.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const index = Number(button.closest("[data-index]")?.dataset.index);
  switch (button.dataset.action) {
    case "new":
      newMeal();
      window.scrollTo({ top: 0 });
      els.choose.focus({ preventScroll: true });
      break;
    case "dismiss-question":
      state.dismissedQuestion = state.result.id;
      renderResult({ focus: "#foods-title" });
      break;
    case "weigh":
    case "food": {
      const kind = button.dataset.action;
      const closing = state.open?.index === index && state.open.kind === kind;
      state.open = closing ? null : { index, kind };
      renderResult({ focus: closing ? `[data-action="${kind}"][data-index="${index}"]` : `#panel-${index} input` });
      break;
    }
    case "pick":
      correct(index, { fdc_id: Number(button.dataset.fdc) }, button.closest(".panel"), button, "food");
      break;
  }
});

els.result.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = $('button[type="submit"]', form);
  if (form.dataset.form === "weigh") {
    const value = Number(new FormData(form).get("grams"));
    correct(Number(form.dataset.index), { grams: Math.round(value) }, form, button, "weigh");
  } else if (form.dataset.form === "answer") {
    const answer = String(new FormData(form).get("answer")).trim();
    if (!answer) return;
    await withBusy(button, async () => {
      try {
        state.result = await api(`/api/meals/${state.result.id}/answer`, { method: "POST", ...jsonBody({ answer }) });
        renderResult({ focus: "#result-title" });
        loadToday();
      } catch (err) {
        $(".question__error", form).textContent = err.message;
      }
    });
  }
});

els.result.addEventListener("input", (event) => {
  const input = event.target.closest("[data-search]");
  if (!input) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchFoods(input), 220);
});

els.result.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !state.open) return;
  const { index, kind } = state.open;
  state.open = null;
  renderResult({ focus: `[data-action="${kind}"][data-index="${index}"]` });
});

els.result.addEventListener("pointerover", (event) => highlight(event.target.closest(".item")?.dataset.index ?? null));
els.result.addEventListener("pointerleave", () => highlight(null));
els.result.addEventListener("focusin", (event) => highlight(event.target.closest(".item")?.dataset.index ?? null));

// ----------------------------------------------------------------- today

async function loadToday() {
  const day = localDate();
  els.todayDate.textContent = dayFormat.format(new Date());
  try {
    renderToday(await api(`/api/days/${day}`));
  } catch (err) {
    els.today.innerHTML = `<p class="today__empty">${esc(err.message)}</p>`;
  }
}

function renderToday(view) {
  if (!view.meals.length) {
    els.today.innerHTML = `<p class="today__empty">Nothing logged yet today. Each meal you analyze lands here with its ranges, and the day's total adds up beside it. No targets, just what you ate.</p>`;
    return;
  }
  const meals = view.meals
    .map((m) => {
      const kcal = m.totals.energy_kcal;
      return `
      <li>
        <button class="meal" type="button" data-meal="${m.id}">
          <span class="meal__time">${timeFormat.format(wallClock(m.context.taken_at))}</span>
          <span class="meal__slot">${esc(m.context.meal_slot)}</span>
          <span class="meal__kcal">${kcal ? rangeHtml(kcal, "kcal") : "Not counted"}</span>
          <span class="meal__names">${esc(m.items.map((r) => r.item.name).join(", "))}</span>
        </button>
      </li>`;
    })
    .join("");
  const kcal = view.totals.energy_kcal;
  const macros = nutrientRowsHtml(view.totals, view.daily_value_pct, { micro: false, prefix: "d", skip: ["energy_kcal"] });
  const micros = nutrientRowsHtml(view.totals, view.daily_value_pct, { micro: true, prefix: "d" });
  const kcalHtml = kcal
    ? `<p class="day-total__value">${kcalRangeHtml(kcal)}</p>
       ${bandHtml("d-kcal", kcal)}
       ${isExact(kcal) ? "" : `<p class="day-total__likely">Most likely <strong>${amount(kcal.likely, "kcal")}&nbsp;kcal</strong>.</p>`}`
    : "";
  const previous = snapshotBands(els.today);
  els.today.innerHTML = `
    <ol class="meals" aria-label="Meals logged today">${meals}</ol>
    <div class="day-total">
      <p class="label">Day total · ${view.meals.length} meal${view.meals.length === 1 ? "" : "s"}</p>
      ${kcalHtml}
      <ul class="nutrients">${macros}</ul>
      ${micros ? microDetailsHtml(micros) : ""}
    </div>`;
  animateBands(els.today, previous);
}

els.today.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-meal]");
  if (!button) return;
  await withBusy(button, async () => {
    try {
      showResult(await api(`/api/meals/${button.dataset.meal}`), { photo: false });
    } catch (err) {
      els.today.insertAdjacentHTML("afterbegin", `<p class="status is-error" role="alert">${esc(err.message)}</p>`);
    }
  });
});

// ----------------------------------------------------------------- wiring

els.choose.addEventListener("click", () => els.input.click());
els.input.addEventListener("change", () => els.input.files[0] && analyze(els.input.files[0]));

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
  if (!els.location.checked) return setStatus("");
  try {
    await devicePosition(); // ask for permission now, not mid-analysis
    setStatus("Location on. Only the place name is kept.");
  } catch (err) {
    els.location.checked = false;
    setStatus(err.message, { error: true });
  }
});

$('.site-nav [data-action="new"]').addEventListener("click", () => {
  if (!els.result.hidden) newMeal();
});

async function init() {
  try {
    const [status, nutrients] = await Promise.all([api("/api/status"), api("/api/nutrients")]);
    state.nutrients = nutrients;
    renderSetup(status);
  } catch (err) {
    els.choose.disabled = true;
    setStatus(err.message, { error: true });
    return;
  }
  loadToday();
}

init();
