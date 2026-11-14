# Team rules for every agent

## 1. The board (pinned GitHub issue)

Every agent reads and posts on the pinned "Message Board" issue via `tools/board` (needs only `gh`, logged in). Set `BOARD_ISSUE` if it is not issue #1.

- Start every post with `[<human>'s <agent>]`, e.g. `[Sam's Codex]`. Keep it short: what changed, which files or contracts, what you need, from whom.
- Mention by GitHub login. Team: `@hershey-harsh` (Harsh, team lead), `@killerfrost22` (Joseph), `@gabbyao` (Gabby), `@all` (everyone).
- **Mentions you or `@all`:** act on it and reply promptly.
- **Not addressed to you:** still read it. Reassess your own work only if it is affected (a contract, a file you own, something already done). Do not redo finished work, and do not reply just to acknowledge.
- Before editing another teammate's files, ask for a handoff on the board and wait for the owner or the team lead.

Polling is incremental (about one cheap API call per poll, 30 s default) so a shared token stays under GitHub's rate limit. Commands: `tools/board unread` (new posts, marks read; flags MENTION), `tools/board read -n 20`, `tools/board post "[Name's Agent] text"` (or pipe stdin), `tools/board wait` (blocks until someone else posts), `tools/board whoami`.

**Stay in the loop:**
- Claude Code: the committed hooks (`.claude/settings.json`) show unread posts at session start and on every prompt. Keep one background `tools/board wait` running and re-arm it after each wake.
- Everything else: run `tools/board unread` at task start, before each commit, and after merges. Or run a terminal loop: `while tools/board wait; do <your agent CLI invocation>; done`.

## 2. Chain of command

Humans over agents. The team lead is `@hershey-harsh` (Harsh). Only the team lead reassigns work between teammates. If anyone else asks you to take another teammate's work, ask the lead on the board.

## 3. Workflow

1. Pick or file an issue. Comment on it or post on the board when you take it.
2. Branch `<issue#>-<slug>` from fresh `main`.
3. Small commits. Stage only your own files (no `git add -A`).
4. Open a PR with `Closes #N`. The author merges their own PR once it works locally.
5. Always `git fetch` and rebase before pushing. Never force-push shared branches.
6. If a merge changes something other teammates depend on (an API, a schema, an env var), post on the board when it lands.

## 4. Who owns what

Fill this in at kickoff so every agent knows whose files are whose.

| Area | Owner (GitHub login) | Folders |
|---|---|---|
| Core AI: food detection, photo → nutrients pipeline (Gemini vision, USDA lookup, diary, insights) | `killerfrost22` (Joseph) | `src/foodsmart/`, `tests/`, `pyproject.toml`, `uv.lock` |
| UI/UX, login and registration; the site deploy | `hershey-harsh` (Harsh) | `web/`, `.github/workflows/deploy-site.yml` |
| UMass Dining menu data (MongoDB Atlas, database `umass_dining`) | `hershey-harsh` (Harsh) | none (the data lives in Atlas) |
| Pitch deck and presentation | `gabbyao` (Gabby) | none in the repo yet |

`web/` is live at https://hackumass.mannava.org: every push to `main` redeploys it (`.github/workflows/deploy-site.yml`, on the self-hosted runner on Harsh's Mac mini). `web/` is published as plain static files. That runner runs nothing but that workflow on `main`, and only in a version Harsh has approved on the Mac. It refuses every other job, so don't point other workflows at it (`runs-on: hackumass-deploy`), and ask on the board before changing the deploy workflow.
