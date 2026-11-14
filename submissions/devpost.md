# MealScholar: Devpost submission

These are answers for every field on the [HackUMass XIV Devpost](https://hackumass-xiv.devpost.com/) form, in the order the form asks for them. Paste each one into its field. Text inside a fenced block is meant to be pasted as is.
The facts come from `README.md`, `BENCHMARK.md`, `TODO.md`, the open PRs and the board, as of Sat Nov 14, 8:30 AM EST.

## Before you submit

- [ ] **Deadlines.** The initial submission is due Sun 12:00 AM. hackumass.com puts the final deadline at Sun 9:00 AM, while Devpost shows 10:00 AM, so go by 9:00 AM. The team target is 8:30 AM (`TODO.md` section 4).
- [ ] **The repo is private.** Judges will get a 404 on the code link. Before submitting, Harsh (the repo owner) makes it public, or you drop the link.
- [ ] **Judging category: Best UMass Hack.** The landing page says "Campus menu first", but the nutrient step doesn't use the dining data yet (`TODO.md` section 2).
  - If campus menu matching isn't live when you submit, keep the story as written. It lists menu matching under "What's next".
  - If it is live, make the two swaps in [If campus menu matching ships](#if-campus-menu-matching-ships).
- [ ] **Features that are still in PRs.** Parts of the story need PRs that haven't merged:
  - In "What it does", the "Set up once" bullet needs #11 (sign-in) and #15 (onboarding).
  - In "What it does", the "UMass Dining menus" bullet needs #15.
  - In "How we built it", step 5's sentence about accounts and sessions needs #11.

  If those aren't live on mealscholar.mannava.org when you submit, delete those parts. Most of the UMass angle depends on #15, so merge it first.
- [ ] **Gemini project number.** The value we have is a project ID, not the number. See [Gemini project number](#gemini-project-number).
- [ ] **Sponsor prizes.** Opt in to **[MLH] Best Use of Gemini API** and **Best Use of MongoDB Atlas**. These don't count toward the one [HACKUMASS] category. Skip GoDaddy, since we registered no domain with them.
- [ ] **Video link** goes in once it's uploaded (see [Also on the form](#also-on-the-form)).

## General info

### Project name

```text
MealScholar
```

### Elevator pitch (200 characters max)

This one is 166 characters:

```text
Snap your dining hall meal. MealScholar finds each food and how much is there, then shows the nutrients as honest ranges from real data, never numbers the AI made up.
```

## Project story

### About the project

Devpost renders Markdown with LaTeX. The story avoids tables in case Devpost doesn't render them.

````markdown
## Inspiration

UMass Dining posts nutrition facts for every dish it serves. Even so, logging a dining hall plate still means searching a food database and guessing how many grams you took. Photo apps skip the search, but then they show one number, like "612 kcal", that a photo can't back up. No one can see how much rice is under the sauce, so the honest answer is a range. We wanted an app that admits that: take a photo, get a range, and answer one question when the photo isn't enough.

## What it does

- **Snap.** Take or choose a photo of your meal in your phone's browser (JPEG, PNG or HEIC). There's nothing to install, and you can add it to your Home Screen.
- **Foods and amounts.** Gemini names each food and draws a box around it on the photo. It estimates each weight as a low, likely and high number of grams, sized against references like the plate.
- **One question, at most.** If the photo can't settle something, like which drink is in the glass or which dressing is on the salad, it asks once and then recomputes.
- **Nutrients from real data.** The AI never outputs a nutrient number. Each food is matched to USDA FoodData Central (13,648 foods), and the nutrients come from plain arithmetic, so you can check every number. If it picked the wrong food, swap it with one tap.
- **Today and this week.** You see 19 nutrients as ranges with % Daily Value, and meals grouped into breakfast, lunch, dinner and snacks. The weekly view averages only the days you logged. Vitamins and minerals are labeled as estimates.
- **Set up once.** Sign up, then pick your dining halls, your diet, the allergens you avoid and your favorite dishes. Goals are optional, and "no goal" is a choice.
- **UMass Dining menus.** It has every menu UMass Dining publishes, for 33 locations. You can see the next meal at your nearest dining hall, search dishes, and spot your favorites and anything that clashes with your diet or allergens.
- **Private by default.** Location stays off unless you turn it on, and even then only the place name is kept. Photos are analyzed in memory and never stored. The copy sent to Gemini has its EXIF data removed, and the request tells Gemini not to store it.

## How we built it

For each food $i$ on the plate, Gemini returns three weights in grams: $g_i^{\text{low}}$, $g_i^{\text{likely}}$ and $g_i^{\text{high}}$. Let $c_{i,n}$ be the amount of nutrient $n$ in 100 g of the matched food. Then the meal total for each of the three cases is

$$N_n^{k} = \sum_i \frac{g_i^{k}}{100}\, c_{i,n}, \qquad k \in \{\text{low}, \text{likely}, \text{high}\}$$

For example, a pizza estimated at 450 g, with 11.4 g of protein per 100 g, gives 51.3 g of protein. Its 380–540 g range gives 43.3–61.6 g. No model is involved in this step.

The pipeline:

1. **Photo and context.** The capture time comes from the photo's EXIF data or the phone's clock, and it sets the meal slot. If location is on, OpenStreetMap's Nominatim turns it into a place name and type, and the coordinates are dropped right there.
2. **Vision (Gemini).** One call to `gemini-4-hydrogen` at `low` thinking, through the google-genai SDK, with a Pydantic schema as the structured output. It returns each food with a USDA search query, a gram range, the cooking method, a bounding box, a hidden-fat flag and a scale reference, plus at most one question. The photo is resized to 1536 px and re-encoded, which removes the EXIF data. When you answer the question, a second, text-only call updates the analysis.
3. **Nutrition lookup.** We keep a local SQLite copy of USDA FoodData Central (Survey/FNDDS, Foundation and SR Legacy) with full-text search. A scorer picks the best match, and we tuned it against the benchmark below.
4. **Compute.** Grams times the per-100 g values, summed into ranges and % Daily Value.
5. **App.** A FastAPI JSON API and a phone-first UI in plain HTML, CSS and JavaScript, with no build step. You can install it as a web app. MongoDB Atlas stores accounts and sessions: passwords are hashed with scrypt, sessions are cookie-only, and requests are rate-limited. UMass Dining's menus live in a second Atlas database and refresh every hour. It covers 33 locations and over 24,000 menu items, plus 2,236 dishes with nutrition facts, allergens and diet tags.
6. **Deploy.** Every push to `main` deploys the app and the landing page. The deploy runs through a self-hosted GitHub Actions runner on a Mac mini. The app runs sandboxed under launchd, and a Cloudflare Tunnel publishes it.

**We chose the model by measuring.** We made 284 Gemini calls on 5 real dining hall photos: 6 models, each at its thinking levels, plus three media resolutions, with 3 repeats each, scored against hand labels.

- `gemini-4-hydrogen` at `low` thinking is our pick. It takes 5.9 s per photo (median) and costs $0.0097 per photo. Its portion estimates fell inside our reference ranges in every run, it found 97% of the foods, and it asked a useful question on every drink and salad photo.
- `xhigh` thinking took 99 s and cost $0.14 per photo, with no gain in accuracy.
- `gemini-4-argon` names foods best, but its median latency is 16.8 s, and 61 s at the 90th percentile.
- `gemini-3.6-flash` at `minimal` thinking is our budget fallback: 4.1 s and $0.0022 per photo, though it calls ginger ale iced tea.

**Three people, three AI agents.** Each of us worked with an AI coding agent (Claude Code, Codex or Cursor). They coordinated through a pinned GitHub issue used as a message board, and each person owned their own folders.

## Challenges we ran into

- **Most calorie errors came from the USDA lookup, not the AI.**
  - In the benchmark, 35% of runs landed outside the reference calorie range. Half of those had matched a powder or a concentrate.
  - Pink lemonade matched frozen concentrate, which has 192 kcal per 100 g, 4× too high.
  - Sweet iced tea matched instant tea powder at 401 kcal per 100 g, so a 240 g glass showed 962 kcal.
  - Gemini named pepperoni pizza correctly in 54 of 55 runs, but the lookup matched it to cheese pizza in all but one.

  We now penalize processed forms the query didn't ask for, and we fixed a naming rule. On the same recorded Gemini output, runs with calories in range went from 65% to 84%, or from 73% to 93% on our default model. Pepperoni went from 1 of 54 to 50 of 54.
- **Some things a photo can't tell you.** An amber drink with ice could be ginger ale, iced tea or whiskey. At `low` thinking, Gemini 4 named it ginger ale in 67% of runs, and no 3.x model ever did. Higher thinking levels got it right but added 2.5–3 s per photo. The app asked about the drink in every ginger ale run anyway, so we kept `low` and let the user answer.
- **Food at the edge of the frame.** Part of a pizza plate that showed at the edge of a drink photo was logged in 55 of 55 runs, adding 170–210 kcal to a drink. That's our next prompt fix.
- **Moving fast without breaking the live site.** Every push to `main` redeploys both sites, and several PRs were stacked on each other. We kept the merge order on the board. The deploy also checks that it's deploying the newest commit on `main`, so an older commit can't overwrite a newer one.

## Accomplishments that we're proud of

- All 275 Gemini responses in the benchmark matched our schema, and none invented a food. A person, a flyer and a napkin dispenser appeared in the photos, and none of them were ever logged.
- Gemini's boxes overlapped our hand-drawn ones at an IoU of 0.95–0.98 on every model we tried.
- The question loop works. "What kind of drink is this?" → "pink lemonade" took the estimate from 461 kcal (346–576) to 110 kcal (83–138).
- A replay harness reruns the USDA lookup on all 275 recorded Gemini outputs with no API calls, so every change to the lookup gets a before-and-after.

## What we learned

- **Measure before you choose a model.** The most expensive setting bought no accuracy, and higher thinking levels asked useful questions less often (75–83% of the time, against 100% at `low`).
- **Give the model the job it's good at.** Gemini is good at naming foods and judging amounts. It shouldn't produce nutrient numbers. Splitting the work made every number traceable, and most of our errors turned out to be bugs we could find in plain code.
- **A range and one question beat a precise number and a long form.**

## What's next for MealScholar

- **Campus menu first.** Find the dining hall and the meal from location and time, and give Gemini that meal's dishes to choose from. When a food matches a dish, use the dish's own nutrition facts, with USDA as the backup.
- **Prompt fixes from the benchmark.** Skip food cut off at the edge of the frame, keep the question to one sentence, and split mixed salads into their parts.
- **Let Gemini choose among the top USDA matches**, using structured output.
- **Send all photos of one meal in a single request**, so nothing is counted twice.
- **Allergen and diet flags on logged meals**, using the tags the dining data already has.
- **Real error bars.** Weigh real meals on a kitchen scale. Right now our reference ranges are visual estimates based on USDA portion weights.
````

#### If campus menu matching ships

Make these two swaps only if menu matching works on the live app when you submit.

1. In "What it does", replace the **Nutrients from real data** bullet with:

   ```markdown
   - **Campus menu first.** On campus, it works out your dining hall and meal from location and time, matches each food to today's menu and uses the dish's own nutrition facts. Anything that isn't on the menu is matched to USDA FoodData Central (13,648 foods). The AI never outputs a nutrient number. If it picked the wrong food, swap it with one tap.
   ```

2. In "What's next", delete the **Campus menu first** bullet.

### Built with (25 tags max)

These are 23 tags:

```text
python, gemini, google-genai, pydantic, fastapi, uvicorn, mongodb-atlas, pymongo, sqlite, usda-fooddata-central, openstreetmap, nominatim, pillow, httpx, javascript, html5, css3, pwa, github-actions, cloudflare-tunnel, pytest, ruff, uv
```

### "Try it out" links

```text
https://mealscholar.mannava.org
https://hackumass.mannava.org
https://github.com/hershey-harsh/HackUmass-26-MealScholar
```

The first link is the app and the second is the landing page. The third is the code, and the repo is private right now (see [Before you submit](#before-you-submit)).

## Sponsor / special prizes

### Universities or schools your team members attend

```text
University of Massachusetts Amherst
University of Maryland, College Park
```

### Domains registered with GoDaddy Registry during this hackathon

```text
None
```

Our domain, mannava.org, is a .org, and GoDaddy Registry doesn't run .org.

### Feedback about technology you used

```text
Gemini API (google-genai SDK): Structured output was the best part. We pass a Pydantic schema, and all 275 responses in our benchmark parsed, with no retries and no JSON repair. The bounding boxes were accurate enough to draw straight onto the photo (IoU 0.95–0.98 against boxes we drew by hand). Thinking levels give a real dial between speed and cost, and store=False made it easy to promise users that photos aren't kept.
Pain points:
- Each model supports different thinking levels, and you only find out from an HTTP 400. For example, minimal works on 3.x Flash but not on Gemini 4. A table of levels per model in the docs would help.
- gemini-4-argon's latency was unpredictable: 16 s median, 61 s at the 90th percentile, and one call took 228 s.
- Gemini 4 bills about 870 more input tokens than 3.x for the same prompt and schema.
- The model sometimes asks two questions in one, even though we tell it to ask one (43 of 216 questions).

MongoDB Atlas: One cluster holds two databases. One has UMass Dining's menus, with daily menus, menu items indexed on date, location, meal, calories, protein, allergens and diets, and dishes with their ingredients. The other has the app's accounts and sessions. The menus' nesting (location, day, meal, category, item) fit into documents as is. Our store tests run against Atlas on a throwaway database.

GitHub: The whole team ran on it. A pinned issue served as a message board that each person's AI agent read and posted to through the gh CLI. PRs ran a lint workflow, and a self-hosted Actions runner deployed both sites on every push to main. One wish: better support for stacked PRs. Three of our PRs waited about seven hours on one base PR, and our board was the only place that showed the chain.
```

### AI tools used this weekend

Check **Gemini**, **Anthropic** and **OpenAI**, then type `Cursor` in **Other**.

### Did you implement a generative AI model or API? How and why?

```text
Yes. The Gemini API (gemini-4-hydrogen, through the google-genai SDK) handles the vision step. Each photo gets one call, which returns structured JSON that follows a Pydantic schema. It lists each food with its weight as low, likely and high grams, the cooking method, a bounding box, a hidden-fat flag and a scale reference, plus at most one clarifying question. When the user answers, a second, text-only call updates the analysis.

Why: naming foods and judging portions from a photo needs a vision model, but nutrient numbers shouldn't come from a model. So Gemini never outputs nutrients. We match each food to USDA FoodData Central and do the arithmetic in code, so every number can be checked.

We picked the model and thinking level from a 284-call benchmark. gemini-4-hydrogen at low thinking takes 5.9 s per photo (median) and costs about $0.01 per photo. Photos are re-encoded without EXIF data and sent with store=False.
```

### Gemini project number

The value we were given is `gen-lang-client-0549117061`. That's a project **ID**. The form asks for the project **number**, which is all digits.

To find the number, go to Gemini AI Studio, click **Get API key**, then click the key's project. If AI Studio shows only the ID, open the project with that ID in the Google Cloud console. Its settings page lists the number.

If the form takes any identifier, paste the ID.

### HackUMass judging category

```text
Best UMass Hack
```

You can pick only one [HACKUMASS] category. Every project is considered for the grand prizes automatically.

## Also on the form

- **Video demo:** a YouTube, Vimeo or Youku link. The Devpost cut runs up to 2:00 (`video/film-plan.md`).
- **Image gallery:** Gemini's real boxes drawn on our photos are in `bench/results/overlays/gemini-4-hydrogen_low_default/IMG_0579.jpg` and `IMG_0582.jpg`. Add screenshots of the app's Analyze, result and Today screens. Don't use `IMG_0583`, because it shows a person.
