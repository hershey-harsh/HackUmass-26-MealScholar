<div align="center">

<a href="https://mealscholar.mannava.org">
  <img src="src/mealscholar/site/icons/icon-192.png" alt="MealScholar logo: a smiling plate wearing a graduation cap, with fork and knife eyes" width="88" height="88">
</a>

# MealScholar

**Nutrition from a photo. Honest ranges, not fake precision.**

A HackUMass XIV project, piloting at UMass.

<p>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&amp;logoColor=white">
  <img alt="Google Gemini" src="https://img.shields.io/badge/Gemini-8E75B2?logo=googlegemini&amp;logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&amp;logoColor=white">
  <img alt="MongoDB Atlas" src="https://img.shields.io/badge/MongoDB_Atlas-47A248?logo=mongodb&amp;logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-003B57?logo=sqlite&amp;logoColor=white">
  <img alt="HTML5" src="https://img.shields.io/badge/HTML5-E34F26?logo=html5&amp;logoColor=white">
  <img alt="CSS" src="https://img.shields.io/badge/CSS-663399?logo=css&amp;logoColor=white">
  <img alt="JavaScript" src="https://img.shields.io/badge/JavaScript-F7DF1E?logo=javascript&amp;logoColor=black">
</p>

[Open the app](https://mealscholar.mannava.org) · [Visit the website](https://hackumass.mannava.org)

[Features](#at-a-glance) · [Quick start](#quick-start) · [Workflow](#workflow) · [Development](#development) · [Team](#team)

</div>

---

MealScholar is a photo-based food diary built for students, piloting at UMass.
Gemini identifies the foods and estimates their portions; USDA FoodData Central supplies
the nutrition facts. Review the sources, correct a match and log your meal.

> [!IMPORTANT]
> **The AI never invents nutrient numbers.** Nutrients come from USDA FoodData Central and
> arithmetic, not from the model.

<p align="center">
  <a href="https://mealscholar.mannava.org">
    <img src=".github/assets/mealscholar-app.png" alt="MealScholar's Analyze screen with photo upload, an opt-in location switch and an explanation of the nutrition workflow." width="960">
  </a>
  <br>
  <sub>The Analyze screen, captured from the local app. Choose a meal photo to get started.</sub>
</p>

## At a glance

| Feature | What you get |
|---|---|
| **Photo analysis** | JPEG, PNG and HEIC uploads up to 25 MB; foods and portion estimates with boxes on the photo. |
| **Honest uncertainty** | Low, likely and high values for portions and nutrients, with at most one clarifying question. |
| **Check the match** | Inspect the USDA source, search alternatives and correct the selected food. |
| **Your food diary** | Meals by day, calorie and nutrient totals, % Daily Value and weekly views. |
| **Phone-first app** | Today and Analyze tabs, camera entry points and Add to Home Screen. |
| **Campus dining APIs** | UMass Dining locations, menus, dish search, nearby halls and favorites alerts. |

### Current limitations

- **USDA-only photo matching.** Dining-menu browsing is implemented, but campus-menu nutrients
  are not yet part of photo analysis.
- **Account UI in progress.** Registration, sessions, per-user diaries, goals and preference
  APIs exist; the sign-in and onboarding screens are still being completed.
- **No email delivery yet.** Account emails queue to an outbox; SMTP delivery is not implemented.

See [TODO.md](TODO.md) for remaining work.

## Quick start

You'll need [Git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/).
uv installs Python 3.12 if needed. No frontend build step is required.

**1. Clone and install**

```sh
git clone https://github.com/hershey-harsh/HackUmass-26-MealScholar.git
cd HackUmass-26-MealScholar
uv sync
```

**2. Create `.env` in the project root**

Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey), then add:

```dotenv
GEMINI_API_KEY=your-key-here
MEALSCHOLAR_AUTH_REQUIRED=0
```

`MEALSCHOLAR_AUTH_REQUIRED=0` enables a local, single-user diary without signing in.
Use it only for local development, not a shared deployment. Shell variables override `.env`,
and `.env` is gitignored.

**3. Import USDA data and start the app**

```sh
uv run mealscholar fdc-import
uv run mealscholar serve
```

Open **[localhost:8000](http://127.0.0.1:8000)**, choose a photo and review the detected foods.
The one-time import downloads roughly 17 MB and creates `~/.mealscholar/fdc.sqlite`.
Interactive API documentation is at **[/api/docs](http://127.0.0.1:8000/api/docs)**.

<details>
<summary><strong>Testing from your phone</strong></summary>

```sh
uv run mealscholar serve --host 0.0.0.0
```

Open your computer's LAN address on the same Wi-Fi. Browser geolocation needs HTTPS or
`localhost`, so the location switch will not work over an ordinary LAN HTTP address.
Keep this single-user development server on a trusted network.

</details>

## Workflow

```mermaid
flowchart LR
    Photo(["Photo + context<br/>Location is opt-in"]) --> Vision["Gemini vision<br/>Foods + portions"]
    Vision --> USDA[("USDA lookup<br/>Matches + alternatives")]
    USDA --> Math["Nutrient math<br/>Low · likely · high"]
    Math --> Result["Meal result<br/>Ranges + % Daily Value"]
    Result --> Diary(["Food diary<br/>Daily + weekly views"])
    Result -.->|Optional answer| Refine["Gemini refinement<br/>Text only"]
    Refine --> USDA
```

Loose USDA matches are suggestions, not counted foods, until a match is selected.
Choosing another USDA food recomputes the result without another photo analysis.


## Command line

```sh
# Analyze a photo and print the full result.
uv run mealscholar analyze lunch.jpg --json

# Review today's meals and the weekly summary.
uv run mealscholar day
uv run mealscholar week
```

<details>
<summary><strong>Analysis options, corrections and saved meals</strong></summary>

```sh
# Opt in to the photo's GPS, or supply device context.
uv run mealscholar analyze lunch.jpg --use-photo-location
uv run mealscholar analyze lunch.jpg --lat 42.39 --lon -72.53 --taken-at 2026-11-14T12:30-05:00

# Supply the clarifying answer without an interactive prompt.
uv run mealscholar analyze lunch.jpg --answer "fried in oil" --json

# Inspect meal 3 and find a different USDA match.
uv run mealscholar show 3
uv run mealscholar search "rice, white, cooked"
uv run mealscholar fix 3 1 --food 2708404

# Optional exact-weight correction for item 1 in meal 3.
uv run mealscholar fix 3 1 --grams 300

# Remove meal 3 from the diary, e.g. one logged from the wrong photo.
uv run mealscholar delete 3
```

`--json` prints the full `MealResult`. The CLI works with the local diary; signing into the
web app keeps each account's meals separate.

</details>

## Configuration

Set these in `.env` or your shell.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Not set | Required for photo analysis. |
| `MEALSCHOLAR_MODEL` | `gemini-4-hydrogen` | Vision model; see [model benchmarks](BENCHMARK.md). |
| `MEALSCHOLAR_THINKING` | `low` | Thinking level. Gemini 4 supports `low`, `medium`, `high`, `xhigh`; `minimal` is for 3.x Flash/Flash-Lite only. |
| `MEALSCHOLAR_HOME` | `~/.mealscholar` | USDA database, downloads and SQLite diary. |
| `MEALSCHOLAR_NOMINATIM_URL` | Public OpenStreetMap Nominatim | Place lookup; the public service allows 1 request/second. |

<details>
<summary><strong>Accounts, dining data and deployment settings</strong></summary>

| Variable | Default | Purpose |
|---|---|---|
| `MONGODB_URI` | Not set | Persistent accounts/preferences and access to dining data. Without it, accounts/preferences are in memory and dining routes return 503. |
| `MEALSCHOLAR_MONGODB_DB` | `mealscholar` | Accounts and preferences database. |
| `MEALSCHOLAR_DINING_DB` | `umass_dining` | Read-only dining-menu database. |
| `MEALSCHOLAR_AUTH_REQUIRED` | `1` | Require sign-in for meal and diary routes. |
| `MEALSCHOLAR_PUBLIC_URL` | `http://127.0.0.1:8000` | Public origin for account links and cross-site checks. |
| `MEALSCHOLAR_COOKIE_SECURE` | `1` | HTTPS-only session cookies. Set to `0` only when testing accounts over local HTTP. |
| `MEALSCHOLAR_EMAIL_PROVIDER` | `outbox` | Queue account emails; SMTP delivery is not implemented yet. |
| `MEALSCHOLAR_DEMO_VERIFY_LINK` | `0` | Demo-only verification links in responses; never enable publicly. |
| `MEALSCHOLAR_DEV_OUTBOX` | `0` | Development-only outbox endpoint; never enable publicly. |

Do not deploy with in-memory accounts or expose the development outbox. Email delivery must be
configured and implemented before relying on password-reset emails.

</details>

## Rules the code enforces

- **Data, not guesses:** the model estimates foods and grams; USDA data supplies the nutrients.
- **Ranges stay visible:** photo-based amounts have low/likely/high estimates. A meal's or a day's
  range adds its foods' errors as partly correlated (`compute.SHARED_ERROR`), not every low and
  every high at once. Exact-weight corrections through the CLI or API can collapse a range.
- **Private by default:** location is opt-in; coordinates and photos are not stored in the diary.
  Images sent to Gemini are re-encoded without EXIF, using `store=False`.
- **Estimates, not diagnosis:** micronutrients are labelled as estimates; weekly averages count
  only days with logged meals.
- **Your goals, not prescriptions:** calorie goals are optional and user-selected. % Daily Value
  is a label reference, not a prescribed goal. No insulin or carb-dosing advice.

## Sites and deployment

| Site | Address | Source |
|---|---|---|
| **App** | [mealscholar.mannava.org](https://mealscholar.mannava.org) | [`src/mealscholar/site/`](src/mealscholar/site/) + [`web.py`](src/mealscholar/web.py) |
| **Landing page** | [hackumass.mannava.org](https://hackumass.mannava.org) | [`web/`](web/) |

Both sites deploy on pushes to `main` through
[`deploy-site.yml`](.github/workflows/deploy-site.yml) on Harsh's Mac mini. Both frontends use
plain HTML/CSS/JavaScript, with no build step. Coordinate deploy changes through the team board
as described in [AGENTS.md](AGENTS.md).

## Development

Start with an [issue](https://github.com/hershey-harsh/HackUmass-26-MealScholar/issues) and
read [AGENTS.md](AGENTS.md) for ownership, the team board and the PR workflow.

### Project map

| Area | Start here |
|---|---|
| Analysis pipeline and result contract | [`pipeline.py`](src/mealscholar/pipeline.py) · [`schemas.py`](src/mealscholar/schemas.py) |
| Vision prompts and nutrient arithmetic | [`vision.py`](src/mealscholar/vision.py) · [`compute.py`](src/mealscholar/compute.py) |
| App and JSON API | [`src/mealscholar/site/`](src/mealscholar/site/) · [`web.py`](src/mealscholar/web.py) |
| Public landing page | [`web/`](web/) |
| Regression tests and model evaluation | [`tests/`](tests/) · [`bench/`](bench/) |

### Checks

```sh
uvx ruff@0.17.5 check
uvx ruff@0.17.5 format --check
uv run pytest

# Replay recorded Gemini output through the USDA lookup without API calls.
uv run python bench/report.py --rematch
```

Use `uvx ruff@0.17.5 format` to apply formatting.

CI: [Lint and formatting](https://github.com/hershey-harsh/HackUmass-26-MealScholar/actions/workflows/lint.yml) · [USDA lookup regression](https://github.com/hershey-harsh/HackUmass-26-MealScholar/actions/workflows/lookup-regression.yml)

### Further reading

- [Benchmarks](BENCHMARK.md) — model comparisons, latency, cost and lookup findings.
- [Submission draft](submissions/devpost.md) — project story and judging notes.
- [Film plan](video/film-plan.md) — demo-video shots and production schedule.

## Team

Built for HackUMass XIV by three teammates.

<table>
  <tr>
    <td align="center" valign="top">
      <a href="https://github.com/killerfrost22">
        <img src="https://avatars.githubusercontent.com/u/36581618?v=4" width="80" height="80" alt="Joseph"><br>
        <strong>Joseph</strong>
      </a><br>
      <a href="https://github.com/killerfrost22">@killerfrost22</a>
    </td>
    <td align="center" valign="top">
      <a href="https://github.com/hershey-harsh">
        <img src="https://avatars.githubusercontent.com/u/77411358?v=4" width="80" height="80" alt="Harsh"><br>
        <strong>Harsh</strong>
      </a><br>
      <a href="https://github.com/hershey-harsh">@hershey-harsh</a>
    </td>
    <td align="center" valign="top">
      <a href="https://github.com/gabbyao">
        <img src="https://avatars.githubusercontent.com/u/2808097?v=4" width="80" height="80" alt="Gabby"><br>
        <strong>Gabby</strong>
      </a><br>
      <a href="https://github.com/gabbyao">@gabbyao</a>
    </td>
  </tr>
  <tr>
    <td align="center"><sub>AI food detection<br>Photo → nutrients</sub></td>
    <td align="center"><sub>UI/UX · Accounts<br>Dining · Deploy</sub></td>
    <td align="center"><sub>Pitch deck<br>Video</sub></td>
  </tr>
</table>

## Data sources

- **Nutrition:** [USDA FoodData Central](https://fdc.nal.usda.gov/) — Survey/FNDDS, Foundation
  and SR Legacy data, CC0.
- **Places:** [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL,
  via Nominatim.
- **Campus menus:** UMass Dining data in MongoDB Atlas, currently used by the dining APIs,
  not by the photo nutrient calculation.

---

<p align="center">
  <a href="#mealscholar">Back to top</a>
</p>
