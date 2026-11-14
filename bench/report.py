"""Score bench/results/runs.jsonl against bench/ground_truth.py and write bench/results/summary.md.

    uv run python bench/report.py                                  # tables
    uv run python bench/report.py --rematch                        # redo the USDA lookup with current code
    uv run python bench/report.py --overlays 'gemini-4-hydrogen|low|default'   # draw boxes, rep 0
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ground_truth import PHOTOS, Photo, classify
from PIL import Image, ImageDraw, ImageFont, ImageOps

from foodsmart.config import load_settings
from foodsmart.nutrition import FoodDatabase
from foodsmart.pipeline import Workflow
from foodsmart.schemas import MealResult

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"

# USD per 1M tokens, paid tier, standard, from ai.google.dev/gemini-api/docs/pricing on 2026-11-14.
# Output price covers thinking tokens. Argon doubles on 2026-12-01; 3.x Flash doubles on 2027-01-01.
PRICES = {
    "gemini-4-argon": (2.00, 10.00),
    "gemini-4-hydrogen": (1.50, 7.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}
# A food on a plate is never one of these; a match to one is a lookup error.
_BAD_MATCH = re.compile(r"powder|concentrate|dehydrated|dry mix|not reconstituted|instant|babyfood", re.I)


def load(phase: set[str]) -> list[dict]:
    rows = [json.loads(line) for line in (RESULTS / "runs.jsonl").read_text().splitlines()]
    return [r for r in rows if r["phase"] in phase]


def rematch(runs: list[dict]) -> list[dict]:
    """Redo the USDA lookup and arithmetic on the recorded Gemini output with the current code, so a
    lookup change is measured on identical model output, without new Gemini calls."""
    workflow = Workflow(vision=None, foods=FoodDatabase(load_settings().food_db))
    redone = []
    for run in runs:
        if run["result"] is not None:
            meal = MealResult.model_validate(run["result"])
            result = workflow._result(meal.context, meal.analysis())  # lookup + compute, no vision
            run = {**run, "result": result.model_dump(mode="json")}
        redone.append(run)
    return redone


def iou(a: list[int], b: list[int]) -> float:
    y0, x0, y1, x1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, y1 - y0) * max(0, x1 - x0)
    area = lambda r: max(0, r[2] - r[0]) * max(0, r[3] - r[1])  # noqa: E731
    union = area(a) + area(b) - inter
    return inter / union if union else 0.0


def union_box(boxes: list[list[int]]) -> list[int] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def cost(model: str, usage: dict) -> float:
    pin, pout = PRICES[model]
    out = usage.get("total_output_tokens", 0) + usage.get("total_thought_tokens", 0)
    return (usage.get("total_input_tokens", 0) * pin + out * pout) / 1e6


def image_tokens(usage: dict) -> int:
    return sum(m["tokens"] for m in usage.get("input_tokens_by_modality", []) if m["modality"] == "image")


def score(run: dict) -> dict:
    """Per-run metrics. Gemini is judged on what it outputs (items, grams, boxes, question);
    kcal additionally depends on the USDA matcher and is reported separately."""
    photo: Photo = PHOTOS[run["photo"]]
    usage = (run.get("call_analyze") or run["call"]).get("usage") or {}
    s: dict = {
        "ok": run["error"] is None,
        "latency": (run.get("call_analyze") or run["call"]).get("latency_s"),
        "in_tok": usage.get("total_input_tokens"),
        "img_tok": image_tokens(usage) if usage else None,
        "thought_tok": usage.get("total_thought_tokens"),
        "out_tok": usage.get("total_output_tokens"),
        "cost": cost(run["model"], usage) if usage else None,
    }
    if not s["ok"]:
        return s
    result = run["result"]
    items = result["items"]
    covered: set[str] = set()
    req_items, unexpected, edge = [], [], []
    for r in items:
        comps, kind = classify(photo, r["item"])
        covered |= comps
        r["_comps"] = comps
        {"required": req_items, "edge": edge, "unexpected": unexpected}[kind].append(r)
    s["recall"] = len(covered) / len(photo.required)
    s["unexpected"] = [r["item"]["name"] for r in unexpected]
    s["edge"] = len(edge) > 0
    s["n_items"] = len(items)

    if photo.identity:
        # Judged on fdc_query: it alone decides the USDA food. "Iced amber beverage" named with
        # "Soft drink, ginger ale" is right; "ginger ale or tea" sent as "Tea, iced" is not.
        queries = [r["item"]["fdc_query"].lower() for r in req_items if photo.identity.component in r["_comps"]]
        if any(re.search(photo.identity.exact, q) for q in queries):
            s["identity"] = "exact"
        elif photo.identity.close and any(re.search(photo.identity.close, q) for q in queries):
            s["identity"] = "close"
        else:
            s["identity"] = "wrong" if queries else "missing"
        s["identity_names"] = queries

    g = [sum(r["item"][f"grams_{k}"] for r in req_items) for k in ("low", "likely", "high")]
    lo, likely, hi = photo.grams
    s["grams"] = g[1]
    s["grams_in_band"] = lo <= g[1] <= hi
    s["grams_ape"] = abs(g[1] - likely) / likely
    s["grams_covers"] = g[0] <= likely <= g[2]
    for comp, (clo, chi) in photo.component_grams.items():
        cg = sum(r["item"]["grams_likely"] for r in req_items if comp in r["_comps"])
        s[f"{comp}_g_ok"] = clo <= cg <= chi

    kcal = sum(r["nutrients"].get("energy_kcal", {}).get("likely", 0) for r in req_items)
    s["kcal"] = kcal
    s["kcal_shown"] = result["totals"].get("energy_kcal", {}).get("likely", 0)
    s["kcal_in_band"] = photo.kcal[0] <= kcal <= photo.kcal[2]
    s["kcal_ape"] = abs(kcal - photo.kcal[1]) / photo.kcal[1]
    s["matched"] = sum(r["match"] is not None for r in items) / len(items) if items else None
    s["bad_match"] = sum(bool(r["match"] and _BAD_MATCH.search(r["match"]["description"])) for r in items)
    pep = [r for r in req_items if "pepperoni" in r["_comps"]]
    if pep:
        s["pepperoni_match_ok"] = all(r["match"] and "pepperoni" in r["match"]["description"].lower() for r in pep)

    if photo.question:
        s["question_hit"] = bool(result["question"] and re.search(photo.question, result["question"].lower()))

    ious = []
    if run["photo"] == "IMG_0579.jpeg":
        boxes = [r["item"]["box_2d"] for r in req_items if r["item"]["box_2d"]]
        ious = [max((iou(gt, b) for b in boxes), default=0.0) for gt in photo.boxes.values()]
    elif "drink" in photo.boxes:
        boxes = [r["item"]["box_2d"] for r in req_items if r["item"]["box_2d"]]
        ious = [max((iou(photo.boxes["drink"], b) for b in boxes), default=0.0)]
    elif "salad" in photo.boxes:
        u = union_box([r["item"]["box_2d"] for r in req_items if r["item"]["box_2d"]])
        ious = [iou(photo.boxes["salad"], u) if u else 0.0]
    s["iou"] = statistics.mean(ious) if ious else None
    s["has_boxes"] = all(r["item"]["box_2d"] for r in items) if items else None
    return s


def _mean(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def _pct(values) -> str:
    m = _mean(values)
    return "–" if m is None else f"{100 * m:.0f}%"


def _p(values, q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(q * len(vals)))]


def _num(v, fmt: str = "{:.0f}") -> str:
    return "–" if v is None else fmt.format(v)


def aggregate(runs: list[dict]) -> dict[str, dict]:
    by_cfg: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for run in runs:
        by_cfg[run["config"]].append((run, score(run)))
    out = {}
    for cfg, pairs in by_cfg.items():
        ss = [s for _, s in pairs]
        ok = [s for s in ss if s["ok"]]
        model = pairs[0][0]["model"]
        by_photo: dict[str, list[dict]] = defaultdict(list)
        for run, s in pairs:
            if s["ok"]:
                by_photo[run["photo"]].append(s)
        cvs = []
        for photo_scores in by_photo.values():
            grams = [s["grams"] for s in photo_scores]
            if len(grams) > 1 and statistics.mean(grams):
                cvs.append(statistics.stdev(grams) / statistics.mean(grams))
        salad = {p: _mean(s["grams"] for s in by_photo.get(p, [])) for p in ("IMG_0582.jpeg", "IMG_0583.jpeg")}
        ident = lambda photo, tiers, by_photo=by_photo: _mean(  # noqa: E731
            s["identity"] in tiers for s in by_photo.get(photo, [])
        )
        out[cfg] = {
            "model": model,
            "thinking": pairs[0][0]["thinking"],
            "resolution": pairs[0][0]["resolution"],
            "n": len(ss),
            "errors": len(ss) - len(ok),
            "error_sample": next((r["error"] for r, s in pairs if not s["ok"]), None),
            "p50": _p([s["latency"] for s in ok], 0.5),
            "p90": _p([s["latency"] for s in ok], 0.9),
            "in_tok": _mean(s["in_tok"] for s in ok),
            "img_tok": _mean(s["img_tok"] for s in ok),
            "thought_tok": _mean(s["thought_tok"] for s in ok),
            "out_tok": _mean(s["out_tok"] for s in ok),
            "cost": _mean(s["cost"] for s in ok),
            "recall": _mean(s["recall"] for s in ok),
            "ginger_exact": ident("IMG_0580.jpeg", {"exact"}),
            "ginger_close": ident("IMG_0580.jpeg", {"exact", "close"}),
            "lemonade_exact": ident("IMG_0581.jpeg", {"exact"}),
            "lemonade_close": ident("IMG_0581.jpeg", {"exact", "close"}),
            "dressing_exact": _mean(
                s["identity"] == "exact" for p in ("IMG_0582.jpeg", "IMG_0583.jpeg") for s in by_photo.get(p, [])
            ),
            "unexpected": _mean(len(s["unexpected"]) for s in ok),
            "unexpected_names": Counter(n.lower() for s in ok for n in s["unexpected"]),
            "edge": _mean(s["edge"] for s in ok),
            "grams_in_band": _mean(s["grams_in_band"] for s in ok),
            "grams_ape": _p([s["grams_ape"] for s in ok], 0.5),
            "grams_covers": _mean(s["grams_covers"] for s in ok),
            "egg_ok": _mean(s.get("egg_g_ok") for s in ok),
            "dressing_g_ok": _mean(s.get("dressing_g_ok") for s in ok),
            "kcal_in_band": _mean(s["kcal_in_band"] for s in ok),
            "kcal_ape": _p([s["kcal_ape"] for s in ok], 0.5),
            "matched": _mean(s["matched"] for s in ok),
            "bad_match": _mean(s["bad_match"] > 0 for s in ok),
            "pepperoni_ok": _mean(s.get("pepperoni_match_ok") for s in ok),
            "question_hit": _mean(s.get("question_hit") for s in ok),
            "iou": _mean(s["iou"] for s in ok),
            "has_boxes": _mean(s["has_boxes"] for s in ok),
            "grams_cv": _mean(cvs),
            "salad_ratio": salad["IMG_0583.jpeg"] / salad["IMG_0582.jpeg"]
            if salad["IMG_0582.jpeg"] and salad["IMG_0583.jpeg"]
            else None,
            "identity_names": {
                p: Counter(n.lower() for s in by_photo.get(p, []) for n in s.get("identity_names", []))
                for p in ("IMG_0580.jpeg", "IMG_0581.jpeg", "IMG_0582.jpeg", "IMG_0583.jpeg")
            },
        }
    return out


def _order(agg: dict[str, dict]) -> list[str]:
    models = list(PRICES)
    levels = ["minimal", "low", "medium", "high", "xhigh"]
    res = ["low", "medium", "default", "ultra_high"]
    return sorted(
        agg,
        key=lambda k: (
            models.index(agg[k]["model"]),
            levels.index(agg[k]["thinking"]),
            res.index(agg[k]["resolution"]),
        ),
    )


def _name(a: dict) -> str:
    res = "" if a["resolution"] == "default" else f" · res {a['resolution']}"
    return f"`{a['model'].removeprefix('gemini-')}` {a['thinking']}{res}"


def tables(agg: dict[str, dict]) -> str:
    keys = _order(agg)
    lines = [
        "### Speed and cost (per photo)",
        "",
        "| Config | OK | p50 s | p90 s | Input tok | Image tok | Thinking tok | Output tok | $/photo | $/1k photos |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k in keys:
        a = agg[k]
        lines.append(
            f"| {_name(a)} | {a['n'] - a['errors']}/{a['n']} | {_num(a['p50'], '{:.1f}')} | {_num(a['p90'], '{:.1f}')} "
            f"| {_num(a['in_tok'])} | {_num(a['img_tok'])} | {_num(a['thought_tok'])} | {_num(a['out_tok'])} "
            f"| {_num(a['cost'], '{:.4f}')} | {_num(a['cost'] and a['cost'] * 1000, '{:.2f}')} |"
        )
    lines += [
        "",
        "### What Gemini saw (identification, portions, boxes)",
        "",
        "| Config | Recall | Ginger ale exact / soda | Pink lemonade exact / lemonade | Honey mustard "
        "| Unexpected items/photo | Grams in band | Median grams error | Egg g ok | Dressing g ok "
        "| Useful question | Box IoU | Grams CV |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k in keys:
        a = agg[k]
        lines.append(
            f"| {_name(a)} | {_pct([a['recall']])} | {_pct([a['ginger_exact']])} / {_pct([a['ginger_close']])} "
            f"| {_pct([a['lemonade_exact']])} / {_pct([a['lemonade_close']])} | {_pct([a['dressing_exact']])} "
            f"| {_num(a['unexpected'], '{:.2f}')} | {_pct([a['grams_in_band']])} | {_pct([a['grams_ape']])} "
            f"| {_pct([a['egg_ok']])} | {_pct([a['dressing_g_ok']])} | {_pct([a['question_hit']])} "
            f"| {_num(a['iou'], '{:.2f}')} | {_pct([a['grams_cv']])} |"
        )
    lines += [
        "",
        "### End to end with the USDA lookup",
        "",
        "| Config | Items matched | Photos with a powder/concentrate match | Pepperoni matched to pepperoni pizza "
        "| kcal in band | Median kcal error | Same salad, far/near grams | Edge items logged |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k in keys:
        a = agg[k]
        lines.append(
            f"| {_name(a)} | {_pct([a['matched']])} | {_pct([a['bad_match']])} | {_pct([a['pepperoni_ok']])} "
            f"| {_pct([a['kcal_in_band']])} | {_pct([a['kcal_ape']])} | {_num(a['salad_ratio'], '{:.2f}')} "
            f"| {_pct([a['edge']])} |"
        )
    errors = [(k, agg[k]) for k in keys if agg[k]["errors"]]
    if errors:
        lines += ["", "### Errors", ""]
        lines += [f"- {_name(a)}: {a['errors']}/{a['n']} failed: `{a['error_sample'][:200]}`" for _, a in errors]
    lines += ["", "### What each config called the drinks and the dressing (all reps)", ""]
    lines += [
        "| Config | IMG_0580 (ginger ale) | IMG_0581 (pink lemonade) | IMG_0582+0583 dressing |",
        "|---|---|---|---|",
    ]
    for k in keys:
        a = agg[k]
        dressing = a["identity_names"]["IMG_0582.jpeg"] + a["identity_names"]["IMG_0583.jpeg"]
        fmt = lambda c: ", ".join(f"{n} ×{v}" for n, v in c.most_common(4)) or "–"  # noqa: E731
        lines.append(
            f"| {_name(a)} | {fmt(a['identity_names']['IMG_0580.jpeg'])} | {fmt(a['identity_names']['IMG_0581.jpeg'])} "
            f"| {fmt(dressing)} |"
        )
    lines += ["", "### Unexpected items (not in the photo's labels or allowed edge items)", ""]
    for k in keys:
        a = agg[k]
        if a["unexpected_names"]:
            names = ", ".join(f"{n} ×{v}" for n, v in a["unexpected_names"].most_common(8))
            lines.append(f"- {_name(a)}: {names}")
    return "\n".join(lines)


def usda_table(runs: list[dict]) -> str:
    """Every distinct fdc_query -> USDA match, so lookup errors are visible independent of the model."""
    seen: dict[tuple[str, str], int] = Counter()
    kcal: dict[str, float] = {}
    for run in runs:
        for r in (run["result"] or {}).get("items", []):
            match = r["match"]["description"] if r["match"] else "— no exact match (not counted)"
            seen[(r["item"]["fdc_query"], match)] += 1
            if r["match"] and r["item"]["grams_likely"]:
                e = r["nutrients"].get("energy_kcal", {}).get("likely")
                if e is not None:
                    kcal[match] = 100 * e / r["item"]["grams_likely"]
    lines = ["| fdc_query from Gemini | USDA match | kcal/100 g | Times |", "|---|---|---|---|"]
    for (query, match), n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])):
        flag = " ⚠" if _BAD_MATCH.search(match) else ""
        lines.append(f"| {query} | {match}{flag} | {_num(kcal.get(match))} | {n} |")
    return "\n".join(lines)


def refine_table(runs: list[dict]) -> str:
    lines = [
        "| Photo | Question | kcal before (low–high) | kcal after answer (low–high) | Items after |",
        "|---|---|---|---|---|",
    ]
    for run in sorted(runs, key=lambda r: (r["photo"], r["rep"])):
        if run["error"]:
            lines.append(f"| {run['photo']} | error: {run['error'][:80]} | | | |")
            continue
        before = run["result"]["totals"].get("energy_kcal", {})
        rng = lambda e: f"{e['likely']:.0f} ({e['low']:.0f}–{e['high']:.0f})" if e else "–"  # noqa: E731
        after = run.get("answered")
        items = ", ".join(f"{r['item']['name']} {r['item']['grams_likely']} g" for r in after["items"]) if after else ""
        lines.append(
            f"| {run['photo']} r{run['rep']} | {run['result']['question'] or '(none asked)'} | {rng(before)} "
            f"| {rng(after['totals'].get('energy_kcal', {})) if after else '–'} | {items} |"
        )
    return "\n".join(lines)


def overlays(config: str) -> None:
    runs = [r for r in load({"a", "b"}) if r["config"] == config and r["rep"] == 0 and not r["error"]]
    out_dir = RESULTS / "overlays" / config.replace("|", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
    except OSError:
        font = ImageFont.load_default()
    palette = [(255, 196, 0), (0, 200, 255), (255, 80, 160), (120, 255, 120), (255, 140, 40), (200, 140, 255)]
    for run in runs:
        img = ImageOps.exif_transpose(Image.open(ROOT / "test_pictures" / run["photo"])).convert("RGB")
        img.thumbnail((1200, 1200))
        w, h = img.size
        draw = ImageDraw.Draw(img)
        scale = lambda b, w=w, h=h: (b[1] * w / 1000, b[0] * h / 1000, b[3] * w / 1000, b[2] * h / 1000)  # noqa: E731
        for gt in PHOTOS[run["photo"]].boxes.values():
            draw.rectangle(scale(gt), outline=(255, 255, 255), width=2)
        for i, r in enumerate(run["result"]["items"]):
            box = r["item"]["box_2d"]
            if not box:
                continue
            color = palette[i % len(palette)]
            x0, y0, x1, y1 = scale(box)
            draw.rectangle((x0, y0, x1, y1), outline=color, width=5)
            label = f"{r['item']['name']} {r['item']['grams_low']}-{r['item']['grams_high']} g"
            tw = draw.textlength(label, font=font)
            ty = max(0, y0 - 32)
            draw.rectangle((x0, ty, x0 + tw + 12, ty + 32), fill=color)
            draw.text((x0 + 6, ty + 2), label, fill=(0, 0, 0), font=font)
        img.save(out_dir / run["photo"].replace(".jpeg", ".jpg"), quality=85)
    print(f"wrote {len(runs)} overlays to {out_dir}")


def overall(runs: list[dict]) -> str:
    scored = [score(r) for r in runs if r["error"] is None]
    pepperoni = [s["pepperoni_match_ok"] for s in scored if "pepperoni_match_ok" in s]
    return "\n".join(
        [
            f"- kcal in band: {sum(s['kcal_in_band'] for s in scored)}/{len(scored)} runs",
            f"- Runs with a powder/concentrate match: {sum(s['bad_match'] > 0 for s in scored)}/{len(scored)}",
            f"- Pepperoni slice matched to a pepperoni pizza: {sum(pepperoni)}/{len(pepperoni)} runs",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlays", metavar="CONFIG")
    parser.add_argument("--rematch", action="store_true", help="redo the USDA lookup with the current code")
    args = parser.parse_args()
    if args.overlays:
        overlays(args.overlays)
        return
    sweep = load({"a", "b"})
    if args.rematch:
        sweep = rematch(sweep)
        title = "Benchmark results, USDA lookup redone with current code (bench/report.py --rematch)"
        out = "summary-rematched.md"
    else:
        title, out = "Benchmark results (generated by bench/report.py)", "summary.md"
    parts = [f"# {title}", "", overall(sweep), "", tables(aggregate(sweep))]
    parts += ["", "### USDA lookup: every fdc_query Gemini produced", "", usda_table(sweep)]
    refine = load({"c"})
    if refine and not args.rematch:
        parts += ["", "### Clarifying question → answer → recompute (phase c)", "", refine_table(refine)]
    text = "\n".join(parts) + "\n"
    (RESULTS / out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
