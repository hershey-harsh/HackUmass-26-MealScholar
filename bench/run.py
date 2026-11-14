"""Run Gemini configurations over test_pictures/ through the real MealScholar pipeline.

Each run is photo -> metadata -> GeminiVision (production prompt + schema) -> USDA lookup -> totals,
exactly as `foodsmart analyze --no-log`, with latency and token usage recorded per Gemini call.

    uv run --env-file .env python bench/run.py --phase a      # model x thinking level sweep
    uv run --env-file .env python bench/run.py --phase b      # media resolution sweep
    uv run --env-file .env python bench/run.py --phase c --config 'gemini-4-hydrogen|low|default'   # answer loop
    uv run --env-file .env python bench/run.py --phase d --config 'gemini-4-hydrogen|low|default'   # prompt change
    uv run --env-file .env python bench/run.py --phase n --config 'gemini-4-hydrogen|low|default'   # non-meal photos

Phase d reruns one config after a prompt change (analyze and answer, like c, 3 reps), so the report
can compare it with the same config in phases a and c. Phase n runs bench/nonfood/: photos that must
not be logged as a meal (analyze only, 3 reps). Each run records a fingerprint of the prompts and
output schema it used.

Photos go in test_pictures/ (not committed: they carry GPS EXIF). Results append to
bench/results/runs.jsonl; runs already there for the same prompt are skipped, so it is resumable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from foodsmart.config import load_settings
from foodsmart.nutrition import FoodDatabase
from foodsmart.pipeline import Workflow
from foodsmart.schemas import MealAnalysis
from foodsmart.vision import REFINE_PROMPT, SYSTEM_PROMPT, GeminiVision, VisionError

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = sorted((ROOT / "test_pictures").glob("*.jpeg"))
NON_MEAL_PHOTOS = sorted((ROOT / "bench" / "nonfood").glob("*.jpg"))
RESULTS = ROOT / "bench" / "results" / "runs.jsonl"
_RETRY_STATUS = {429, 500, 502, 503, 504}
PROMPT = hashlib.sha256(
    (SYSTEM_PROMPT + REFINE_PROMPT + json.dumps(MealAnalysis.model_json_schema(), sort_keys=True)).encode()
).hexdigest()[:8]
"""Fingerprint of what Gemini is told. Runs recorded before prompts were fingerprinted have none."""

# Phases C and D: what the user would type after the clarifying question.
ANSWERS = {
    "IMG_0579.jpeg": "One cheese slice and one pepperoni slice from a large dining-hall pie.",
    "IMG_0580.jpeg": "Regular ginger ale, not diet. The glass holds about 12 oz.",
    "IMG_0581.jpeg": "Regular pink lemonade from the dining hall fountain, not sugar-free.",
    "IMG_0582.jpeg": "Honey mustard dressing, about 3 tablespoons.",
    "IMG_0583.jpeg": "Honey mustard dressing, about 3 tablespoons.",
}


@dataclass(frozen=True, slots=True)
class Config:
    model: str
    thinking: str
    resolution: str | None = None
    """Per-image media resolution; None leaves the API default (1120 image tokens on Gemini 3/4)."""

    @property
    def key(self) -> str:
        return f"{self.model}|{self.thinking}|{self.resolution or 'default'}"

    @classmethod
    def parse(cls, key: str) -> Config:
        model, thinking, resolution = key.split("|")
        return cls(model, thinking, None if resolution == "default" else resolution)


PHASE_A = [
    *(Config("gemini-4-hydrogen", t) for t in ("low", "medium", "high", "xhigh")),
    *(Config("gemini-4-argon", t) for t in ("low", "medium")),
    *(Config("gemini-3.8-flash", t) for t in ("low", "medium")),
    *(Config("gemini-3.6-flash", t) for t in ("minimal", "medium")),
    *(Config("gemini-3.5-flash-lite", t) for t in ("minimal", "low")),
    Config("gemini-3.1-flash-lite", "minimal"),
]
PHASE_B = [
    Config(model, thinking, resolution)
    for model, thinking in (("gemini-4-hydrogen", "low"), ("gemini-3.5-flash-lite", "minimal"))
    for resolution in ("low", "medium", "ultra_high")
]


class _Recorder:
    """Stands in for `client.interactions`: applies the media resolution to image inputs, retries
    rate limits and server errors, and records latency and usage of each successful call."""

    def __init__(self, client, resolution: str | None) -> None:
        self._client = client  # must stay referenced: a collected genai.Client closes its HTTP pool
        self._resolution = resolution
        self.calls: list[dict] = []

    def create(self, **kwargs):
        if self._resolution:
            kwargs["input"] = [
                {**c, "resolution": self._resolution} if c.get("type") == "image" else c for c in kwargs["input"]
            ]
        for attempt in range(6):
            start = time.perf_counter()
            try:
                interaction = self._client.interactions.create(**kwargs)
            except Exception as exc:
                transient = getattr(exc, "status_code", None) in _RETRY_STATUS or type(exc).__name__ in (
                    "APITimeoutError",
                    "APIConnectionError",
                )
                if transient and attempt < 5:
                    time.sleep(min(60, 2 ** (attempt + 1)) + random.random())
                    continue
                raise
            usage = interaction.usage.model_dump(exclude_none=True, mode="json") if interaction.usage else {}
            self.calls.append(
                {
                    "latency_s": round(time.perf_counter() - start, 3),
                    "retries": attempt,
                    "status": str(interaction.status),
                    "usage": usage,
                }
            )
            return interaction
        raise AssertionError("unreachable")


def _workflow(cfg: Config, api_key: str, food_db: Path) -> tuple[Workflow, _Recorder]:
    vision = GeminiVision(api_key, cfg.model, cfg.thinking)
    recorder = _Recorder(vision._client, cfg.resolution)
    vision._client = SimpleNamespace(interactions=recorder)  # production _call, instrumented transport
    return Workflow(vision=vision, foods=FoodDatabase(food_db), places=None), recorder


class _Sink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self.done: set[tuple[str, str, str, int, str | None]] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                r = json.loads(line)
                self.done.add((r["phase"], r["config"], r["photo"], r["rep"], r.get("prompt")))

    def write(self, record: dict) -> None:
        with self._lock, self._path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        status = record.get("error") or f"{record['call']['latency_s']:.1f}s"
        print(f"[{record['phase']}] {record['config']:<42} {record['photo']} rep{record['rep']}  {status}", flush=True)


def run_config(phase: str, cfg: Config, reps: int, api_key: str, food_db: Path, sink: _Sink) -> None:
    workflow, recorder = _workflow(cfg, api_key, food_db)
    for rep in range(reps):
        for photo in NON_MEAL_PHOTOS if phase == "n" else PHOTOS:
            if (phase, cfg.key, photo.name, rep, PROMPT) in sink.done:
                continue
            record = {
                "phase": phase,
                "config": cfg.key,
                "model": cfg.model,
                "thinking": cfg.thinking,
                "resolution": cfg.resolution or "default",
                "photo": photo.name,
                "rep": rep,
                "prompt": PROMPT,
                "error": None,
                "result": None,
            }
            recorder.calls.clear()
            start = time.perf_counter()
            try:
                result = workflow.analyze(photo)
                record["result"] = result.model_dump(mode="json")
                if phase in ("c", "d"):
                    record["call_analyze"] = recorder.calls[-1]
                    if result.question:
                        answered = workflow.answer(result, ANSWERS[photo.name])
                        record["answered"] = answered.model_dump(mode="json")
            except VisionError as exc:
                record["error"] = str(exc)[:500]
            record["pipeline_s"] = round(time.perf_counter() - start, 3)
            record["call"] = recorder.calls[-1] if recorder.calls else {"latency_s": None, "usage": {}}
            sink.write(record)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("a", "b", "c", "d", "n"), action="append", required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument(
        "--config", action="append", help="model|thinking|resolution (phases c, d, n, or to narrow a/b)"
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.gemini_api_key:
        raise SystemExit("Set GEMINI_API_KEY (or pass `uv run --env-file .env`).")
    jobs: list[tuple[str, Config]] = []
    for phase in args.phase:
        configs = [Config.parse(k) for k in args.config] if args.config else {"a": PHASE_A, "b": PHASE_B}.get(phase)
        if not configs:
            raise SystemExit(f"Phase {phase} needs --config.")
        jobs += [(phase, cfg) for cfg in configs]

    sink = _Sink(RESULTS)
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [
            pool.submit(run_config, phase, cfg, args.reps, settings.gemini_api_key, settings.food_db, sink)
            for phase, cfg in jobs
        ]
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
