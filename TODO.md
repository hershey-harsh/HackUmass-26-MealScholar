# MealScholar TODO

As of Sat Nov 14, 11:20 AM EST. Owners follow the table in `AGENTS.md`. Deadlines come from `TIMELINE.md`.
Tick a box when it's done. When a contract changes, post on the board.

## 1. Merge chain and cleanup

The chain is merged: #7 at 8:10 AM, then #11, #13 and #15 at 8:18–8:19 AM. Every PR since has merged except #41 (rename), #46 (logo suggestion, Harsh's pick) and #48 (Harsh's app UI update). That includes #44 (film plan and script PDF) and #45 (landing page dark mode).

- [x] **Joseph**: rebase #7 on `main` and fix the `AGENTS.md` conflict. Run `uv run pytest` and `uvx ruff@0.17.5 check`, then merge. Delete the branch on merge so GitHub moves #11 to `main`.
- [x] **Joseph**: review #11 (login). Done before it merged: a security review found no code bugs, and Harsh confirmed the three server settings (`MONGODB_URI`, `MEALSCHOLAR_PUBLIC_URL`, `MEALSCHOLAR_DEV_OUTBOX` unset). Its follow-ups are under "Before real users" in section 5.
- [x] Merge #11, #13 and #15 back to back, in that order.
- [ ] **Harsh**: the chain has landed, so smoke test the live app now. Sign up, onboarding, photo, answer the question, log it, then check Today. The API checks passed at 8:20 and 8:22 AM (status, dining, nearest hall, prefs, dev outbox 404). The rest of the flow is open, and sign-up waits for the sign-in screens, which stay off (`MEALSCHOLAR_AUTH_REQUIRED=0`) until email sending exists.
- [x] **Joseph**: the rest of Harsh's #7 review. Coordinates are kept out of the access log and meal notes (#30). Photos that won't decode get 413/415 instead of 500, and error replies no longer show server paths or Gemini's raw errors (#32).
- [x] **Joseph**: landing page dark mode (#16), regenerated after Harsh's landing rework. Merged as #45, with a System, Light, Dark toggle.
- [ ] **Harsh**: apply the app dark mode patch (#16) once #48 merges. If it no longer applies on top of #48, Joseph regenerates it.
- [x] **Joseph**: add `test_pictures/` to `.gitignore`. The photos carry GPS EXIF (see `BENCHMARK.md`). Done in #62. Reversed: the six photos are committed with EXIF stripped, and the gitignore line is gone.
- [ ] **Harsh, then Joseph**: land the rename to `mealscholar` (#41) after #48 and the app dark mode patch. It's ready: tests, Ruff and the lookup check pass, and main + #48 + #41 merge cleanly (git moves #48's files into `src/mealscholar/site/`). The Mac's `.env` copies and launchd switch script are in place. Waiting on Harsh himself to approve the changed `deploy-site.yml` on the deploy runner. Then Joseph merges, and Harsh runs the launchd switch once the deploy finishes (the app is down under a minute). Not during the 7:00–8:30 PM recording freeze. Until then, keep #41 up to date with `main`.

## 2. Make the app do what the landing page says (before the Sat 2 PM checkpoint)

The landing page says "Campus menu first" in the hero, feature 03, step 04 and the nutrients section. The read side is live since #15 (`/api/dining/*`, including `/api/dining/dishes`), but the nutrient path doesn't use it yet: `pipeline.py`, `nutrition.py`, `vision.py`, `compute.py` and `schemas.py` never read the dining data. Judges will try it, and our judging category is Best UMass Hack (section 4).

- [ ] **Joseph**: campus menu matching in the pipeline. The hook in the nutrient path is Joseph's (Harsh, on the board).
  - [ ] Pick the dining hall and meal from location and time. Use `dining.py` (`/nearest`, `/next`).
  - [ ] Give Gemini that meal's dish names as candidates. In the structured output it either picks a dish id or returns none.
  - [ ] Use the dish's own nutrition facts when it matches, and USDA otherwise. `serving_size` is free text ("1 slice", "4 oz"), so decide how grams map to servings. Options: parse the serving size, or have Gemini estimate servings for menu dishes.
  - [ ] Add a `source` field (`campus_menu` or `usda`) to the result schema. The app shows it: "Not on today's campus menu · USDA match".
  - [ ] Tests: menu hit, menu miss falling back to USDA, and off campus or no location.
  - [ ] Rerun the benchmark. All 5 test photos are from a dining hall, so this is a direct before/after.
- [ ] **Harsh**: fallback if it can't land before feature freeze: tone down the landing copy so it doesn't promise menu matching.
- [x] **Harsh**: the landing page says the menus are "refreshed every hour". Harsh confirmed on the board (7:53 AM) that the hourly refresh runs.
- [ ] **Harsh**: the menu item count doesn't match. The landing page says 24,191; the board post said 25,613. Pick one source.
- [ ] Calorie goals shipped in #15, but four places still say "no calorie targets" (`AGENTS.md` section 5 is fixed in `ac951d2`):
  - [x] **Joseph**: `README.md` (Rules, fixed in the README polish) and the `insights.py` docstring (#62)
  - [ ] **Harsh**: the app footer in `site/index.html` and the header comment in `site/app.js`. Both go away with the app UI update he has in flight (onboarding, goals, laptop dashboard).
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
- [ ] After every lookup change, post the before/after from `uv run python bench/report.py --rematch`. CI's Lookup regression check (#22) already fails PRs that make the lookup worse. When a change beats `REMATCH_LIMITS` in `bench/report.py`, raise the limits in the same PR.
- [ ] Meal time: the app sends only `lat`/`lon`. Have it also send the phone's local time with its UTC offset, and have `POST /api/meals` accept it, so photos without EXIF don't fall back to the server clock.
- [x] Calorie ranges too wide (#54). Meal and day totals now combine the foods' ranges as partly correlated errors instead of adding every low and every high. Meal #7 goes from 945–1,455 to 1,010–1,369 kcal. The refine prompt narrows the items an answer pins down, and the omelette no longer matches a fried egg. Defining low and high as the 10th and 90th percentile was tried and not kept. See "Range width" in `BENCHMARK.md`.
- [ ] **Harsh**: on the result card, lead with the likely kcal and show the range smaller, the way Today's `hero__num` does (#54). Today the big number is the range (`totalHtml`, `kcalRangeHtml`).

## 4. Demo and submission

| When (EST) | Task | Owner |
|---|---|---|
| Sat 2:00 PM | MVP works end to end on mealscholar.mannava.org | All |
| Sat 6:00 PM | Cut anything that can't be demoed by midnight | Harsh |
| Sat 11:00 PM | Submission draft: done early in `submissions/devpost.md` (#28). Review it before pasting it into Devpost | Harsh + Gabby |
| Sun 12:00 AM | **Initial submission** | Harsh |
| Sun 2:00 AM | Feature freeze, then bug fixes only | All |
| Sun 7:00 AM | Record the demo video (script in `video/`), take screenshots, finish the README | Gabby |
| Sun 8:30 AM | **Final submission** (30 min buffer) | Harsh |
| Sun 12:30 PM | **Judging** | All |

- [x] Prize tracks, decided in `submissions/devpost.md` (Harsh can still change them). Devpost allows only one [HACKUMASS] category, and every project is considered for the grand prizes automatically.
  - [HACKUMASS] category: **Best UMass Hack**. Best UI/UX, Most Impactful AI Hack and Best Pitch are [HACKUMASS] categories too, so we can't also enter them.
  - Sponsor opt-ins, which don't count toward that limit: **[MLH] Best Use of Gemini API** (talking points in `BENCHMARK.md`) and **Best Use of MongoDB Atlas**.
- [ ] Gabby has acquired **mealscholar.health** (registered at Porkbun, 10:51 AM EST).
  - [ ] **Harsh**: move the landing page and the app there (#55). The steps are in the issue: decide which address serves what and when, add the domain to Cloudflare, add the tunnel hostnames, set the app's public URL, and update the links and the QR code. Decide before the 7 PM recording whether the video shows the new address.
  - [ ] **Gabby**: at Porkbun, switch the nameservers to the two Cloudflare gives Harsh (#55, step 2).
  - [ ] **Harsh + Gabby**: `.health` is run by GoDaddy Registry, so in `submissions/devpost.md` the answer to "Domains registered with GoDaddy Registry during this hackathon" becomes `mealscholar.health` instead of "None".
- [ ] **Harsh**: make the repo public before submitting, or drop the code link. Judges get a 404 on a private repo. Harsh's Claude says nothing secret is in the repo.
- [ ] **Joseph or Harsh**: put the numeric Gemini project number in `submissions/devpost.md`. We only have the project ID (`gen-lang-client-…`). The draft explains where to find the number.
- [ ] **Harsh**: if campus menu matching (section 2) isn't live when you submit, submit the story as written. If it is, make the two swaps in `submissions/devpost.md`.
- [ ] **Gabby**: add the video link (YouTube, Vimeo or Youku) and the gallery images to the Devpost form.
- [x] **Joseph**: improve the Devpost thumbnail's gallery-size hierarchy (#100). Use `brand/devpost-thumbnail.png` (1800×1200, 3:2); its editable HTML keeps the real salad result and makes the calorie range the primary number.
- [ ] The final deadline is 9:00 AM on hackumass.com but 10:00 AM on Devpost. Go by 9:00 AM.
- [ ] A demo account with a few days already logged, so Today and This week aren't empty.
- [ ] Set `MEALSCHOLAR_DEMO_VERIFY_LINK` on the demo server, so sign-up works without a real email service. Only needed once the sign-in screens are on. It lets anyone confirm whatever address they type, so the favorites digest could go to someone else's email (#11 review).
- [ ] Fallbacks: a recorded video, and the faster model (`MEALSCHOLAR_MODEL=gemini-3.6-flash MEALSCHOLAR_THINKING=minimal`) if Gemini is slow on venue Wi-Fi.
- [ ] Charge phones. Test the demo on venue Wi-Fi and on cellular.
- [ ] **Everyone filming**: hard-reload mealscholar.mannava.org once on each filming phone before the 7 PM screen recordings. Older deploys left cached app files on phones; deploys stamp the commit into asset versions from the next app change on (Harsh).

## 5. Continuous development (keep doing this after the hackathon)

- [x] **Joseph**: add a test workflow (`uv run pytest` on `ubuntu-latest`). The **Tests** workflow (#62) runs on every pull request and push to `main`, next to Ruff (lint and format) and the Lookup regression check (#22). The tests use a fixture USDA database, and the Mongo tests skip without `MEALSCHOLAR_TEST_MONGODB_URI`.
- [ ] **Harsh**: branch protection on `main`: require Lint and Tests to pass. Every push to `main` is a live deploy. GitHub's rulesets API says this needs GitHub Pro while the repo is private, so it may have to wait for the public/private decision in section 4.
- [ ] Keep stacks of PRs at most two deep, and merge the base the same day. #7 sat for 7 hours and blocked three PRs.
- [ ] Every PR includes tests and a clean Ruff run, plus screenshots for UI changes. Post on the board when a contract changes (an API, a schema, an env var).
- [ ] Before real users:
  - [ ] A real email sender in place of the outbox (`SmtpSender` is a placeholder), for confirmation and password reset.
  - [ ] Rate-limit `POST /api/meals` per user, and set a spending cap on the Gemini key.
  - [ ] Rotate the Gemini key and the Mongo connection string if they were shared in chat.
  - [ ] Back up accounts and diaries in Atlas (db `mealscholar`).
  - [ ] Move hosting off a personal Mac mini.
  - [ ] From the #11 security review:
    - [ ] Require a confirmed email for anything that emails the user. Today it only gates the favorites digest.
    - [ ] Sign-up answers 409 for an email that already has an account. Forgot-password allows 3 per hour per email, so anyone can use up someone else's quota; count per (network, email) instead.
    - [ ] Raise scrypt's work factor from N=2^14 toward OWASP's 2^17. It's stored in each hash, so existing passwords keep working.
  - [ ] Photos between about 89 and 179 megapixels still decode, with a warning, and can take several hundred MB of RAM each. Set an explicit pixel limit; phone photos are 48 MP at most (#32).
- [ ] Weigh 10 real meals on a kitchen scale, so the benchmark has real error bars instead of estimates. Then check that the narrower meal ranges from #54 still contain the weighed amounts, and retune `compute.SHARED_ERROR`.
- [x] Run `bench/report.py --rematch` in CI as a regression check: the Lookup regression workflow (#22), with the USDA download cached.

## 6. Later

- [ ] Allergen and diet flags on logged meals. The dining data already has allergens and diet tags, and onboarding saves the user's own.
- [ ] Send all photos of one meal in a single Gemini request, so items at the edges are counted once.
- [ ] v2: chain restaurant menus, barcode scanning.
- [ ] v3: processing level (NOVA), backfill from the camera roll.
