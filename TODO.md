# MealScholar TODO

As of Sat Nov 14, 7:45 AM EST. Owners follow the table in `AGENTS.md`. Deadlines come from `TIMELINE.md`.
Tick a box when it's done. When a contract changes, post on the board.

## 1. Unblock the merge chain (now)

PR #7 is the only blocker. Its one conflict is in `AGENTS.md`. Three finished PRs are stacked on it.

- [ ] **Joseph**: rebase #7 on `main` and fix the `AGENTS.md` conflict. Run `uv run pytest` and `uvx ruff@0.17.5 check`, then merge. Delete the branch on merge so GitHub moves #11 to `main`.
- [ ] **Joseph**: review #11 (login). It changes `web.py`, `diary.py`, `config.py`, `cli.py` and adds pymongo.
- [ ] Merge #11, #13 and #15 back to back, in that order.
  - Every push to `main` redeploys mealscholar.mannava.org. If #7 sits on `main` alone, it puts the old UI back.
  - If #11 lands without #13, the app has no working sign-in (meals return 401). That's because `FOODSMART_AUTH_REQUIRED` defaults to 1.
  - The deploy skips any commit that isn't the tip of `main`, so a fast sequence deploys only the last one.
- [ ] **Harsh**: after the chain lands, smoke test the live app. Sign up, onboarding, photo, answer the question, log it, then check Today.
- [ ] **Joseph**: send the dark mode patches (#16) to Harsh. Base the `web/` patch on `main` and the app patch on `12-app-ui` at 5aec3a8. Don't push them.
- [ ] **Joseph**: add `test_pictures/` to `.gitignore`. The photos carry GPS EXIF (see `BENCHMARK.md`).

## 2. Make the app do what the landing page says (before the Sat 2 PM checkpoint)

The landing page says "Campus menu first" in the hero, feature 03, step 04 and the nutrients section. No branch does this yet. `pipeline.py`, `nutrition.py`, `vision.py`, `compute.py` and `schemas.py` never read the dining data. Judges will try it.

- [ ] **Joseph + Harsh**: campus menu matching in the pipeline.
  - [ ] Pick the dining hall and meal from location and time. Use `dining.py` (`/nearest`, `/next`) from #15.
  - [ ] Give Gemini that meal's dish names as candidates. In the structured output it either picks a dish id or returns none.
  - [ ] Use the dish's own nutrition facts when it matches, and USDA otherwise. `serving_size` is free text ("1 slice", "4 oz"), so decide how grams map to servings. Options: parse the serving size, or have Gemini estimate servings for menu dishes.
  - [ ] Add a `source` field (`campus_menu` or `usda`) to the result schema. The app shows it: "Not on today's campus menu · USDA match".
  - [ ] Tests: menu hit, menu miss falling back to USDA, and off campus or no location.
  - [ ] Rerun the benchmark. All 5 test photos are from a dining hall, so this is a direct before/after.
- [ ] **Harsh**: fallback if it can't land before feature freeze: tone down the landing copy so it doesn't promise menu matching.
- [ ] **Harsh**: the landing page says the menus are "refreshed every hour". No refresh job is in the repo, so confirm one runs, or drop the claim.
- [ ] **Harsh**: the menu item count doesn't match. The landing page says 24,191; the board post said 25,613. Pick one source.
- [ ] **Whoever ships calorie goals (#15)**: four places still say "no calorie targets". Update them in the same PR:
  - `README.md` (Rules)
  - `AGENTS.md` section 5
  - the `insights.py` docstring
  - the app footer in `site/index.html`
- [ ] **Harsh + Gabby**: the landing page shows three "Coming soon" screen recordings. Record them once the chain is merged, or hide that section before judging.

## 3. Accuracy (Joseph)

These come from the recommendations in `BENCHMARK.md`, ordered by measured impact.

- [ ] Prompt: skip food cut off by the edge of the photo unless it's the subject. This fixes the drink-photo pizza and the `IMG_0583` glass.
- [ ] Refine prompt: "update existing items, never duplicate one". Add a test for the duplicate dressing.
- [ ] Prompt: the question is one sentence. Split composite salads into their parts.
- [ ] Lookup misses still open:
  - "un-sweetened" tea splits into two words and matches sweetened tea
  - pepperoni thin crust loses a tiebreak to "extra cheese"
  - a whole salad matches "Salad dressing, NFS"
- [ ] Let Gemini choose among the top USDA candidates with structured output.
- [ ] After every lookup change, run `uv run python bench/report.py --rematch` and post the before/after.
- [ ] Meal time: the app sends only `lat`/`lon`. Have it also send the phone's local time with its UTC offset, and have `POST /api/meals` accept it, so photos without EXIF don't fall back to the server clock.

## 4. Demo and submission

| When (EST) | Task | Owner |
|---|---|---|
| Sat 2:00 PM | MVP works end to end on mealscholar.mannava.org | All |
| Sat 6:00 PM | Cut anything that can't be demoed by midnight | Harsh |
| Sat 11:00 PM | Submission draft: name, description, repo link, prize tracks | Harsh + Gabby |
| Sun 12:00 AM | **Initial submission** | Harsh |
| Sun 2:00 AM | Feature freeze, then bug fixes only | All |
| Sun 7:00 AM | Record the demo video (script in `video/`), take screenshots, finish the README | Gabby |
| Sun 8:30 AM | **Final submission** (30 min buffer) | Harsh |
| Sun 12:30 PM | **Judging** | All |

- [ ] Prize tracks:
  - Best UMass Hack (campus menus)
  - Best Use of Gemini API (talking points in `BENCHMARK.md`)
  - Best Use of MongoDB Atlas
  - Best UI/UX
  - Most Impactful AI Hack
  - Best Pitch
- [ ] A demo account with a few days already logged, so Today and This week aren't empty.
- [ ] Set `FOODSMART_DEMO_VERIFY_LINK` on the demo server, so sign-up works without a real email service.
- [ ] Fallbacks: a recorded video, and the faster model (`FOODSMART_MODEL=gemini-3.6-flash FOODSMART_THINKING=minimal`) if Gemini is slow on venue Wi-Fi.
- [ ] Charge phones. Test the demo on venue Wi-Fi and on cellular.

## 5. Continuous development (keep doing this after the hackathon)

- [ ] **Joseph**: add a test workflow (`uv run pytest` on `ubuntu-latest`). CI only runs Ruff today, so the 213 tests on #15 never run on a PR. The tests use a fixture USDA database, and the Mongo tests skip without `FOODSMART_TEST_MONGODB_URI`.
- [ ] **Harsh**: branch protection on `main`: require Lint and Tests to pass. Every push to `main` is a live deploy.
- [ ] Keep stacks of PRs at most two deep, and merge the base the same day. #7 sat for 7 hours and blocked three PRs.
- [ ] Every PR includes tests and a clean Ruff run, plus screenshots for UI changes. Post on the board when a contract changes (an API, a schema, an env var).
- [ ] Before real users:
  - [ ] A real email sender in place of the outbox (`SmtpSender` is a placeholder), for confirmation and password reset.
  - [ ] Rate-limit `POST /api/meals` per user, and set a spending cap on the Gemini key.
  - [ ] Rotate the Gemini key and the Mongo connection string if they were shared in chat.
  - [ ] Back up accounts and diaries in Atlas (db `mealscholar`).
  - [ ] Move hosting off a personal Mac mini.
- [ ] Weigh 10 real meals on a kitchen scale, so the benchmark has real error bars instead of estimates.
- [ ] Optional: run `bench/report.py --rematch` in CI as a regression check. It needs the 17 MB USDA download; cache it.

## 6. Later

- [ ] Allergen and diet flags on logged meals. The dining data already has allergens and diet tags, and onboarding saves the user's own.
- [ ] Send all photos of one meal in a single Gemini request, so items at the edges are counted once.
- [ ] v2: chain restaurant menus, barcode scanning.
- [ ] v3: processing level (NOVA), backfill from the camera roll.
