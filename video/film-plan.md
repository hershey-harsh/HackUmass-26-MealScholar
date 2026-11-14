# MealScholar film: plan for today's shoot

Draft for Sat Nov 14. It replaces the structure of `MealScholar Video Script.docx` (Gabby's). From that script it keeps the honesty rules, the consent and permission notes, and the screen-recording hygiene.

## 1. Why the current script won't read like an Apple, Claude or OpenAI ad

| | Current script | What the reference ads do |
|---|---|---|
| Length | 4:35 | ChatGPT's first brand films run 30 s ([It's Nice That](https://www.itsnicethat.com/articles/open-ai-miles-jay-film-advertising-project-151025)). Claude's "Keep thinking" opens with a 90 s film ([LBB](https://lbbonline.com/news/Anthropics-Claude-Is-the-AI-Platform-for-Problem-Solvers)) |
| Story | Sitcom: setup, explaining dialogue, jokes ("Grandma, I need grams") | One small moment. "Drop the viewer into the final scene of a movie", carried by a micro-performance, the camera and the music ([LBB](http://lbbonline.com/news/isle-of-any-chatgpt-tiny-movies-brand-campaign)) |
| Dialogue | About 25 spoken lines from non-actors | Little or none. The product's words appear as on-screen type. ChatGPT renders the prompt and answer as end credits |
| Product | A 2:00 screen recording under 192 words of voiceover | A few product beats, full screen, then out of the way |
| Look | Not specified | Shot on 35mm with custom lenses "for warmth and tactility" (OpenAI); one strong track chosen like a film's closing song |
| Proof | A fake competitor app as the "before" | No competitors. Apple-style small print, "Sequences shortened", keeps it honest |

The current script is also out of date in three ways:
- It says "no goal widget", but onboarding now has optional goals (#15).
- It shows typing in a measured weight, but the landing page says "No weighing".
- It doesn't use the app's boxes on the photo, which is the strongest visual the app has.

**Biggest single fix: cut the length.** Make two cuts from one shoot:

| Cut | Length | Used for |
|---|---|---|
| **Film** | 0:60 | Landing page (it fills the "Coming soon" recordings), pitch opener at judging, social |
| **Devpost** | ≤ 2:00 | Film (0:00–1:00), then a voiceover walkthrough (1:00–1:50), then the end card |

I couldn't confirm the Devpost video length limit, because the rules page is behind Cloudflare. Check it on Discord. Devpost plays video only from YouTube, Vimeo or Youku ([Devpost help](https://help.devpost.com/article/85-uploading-a-demo-video)).

## 2. Concept: "It doesn't pretend"

Every food app shows one exact number that nobody can trust. MealScholar's whole idea is to be honest about what a photo can tell you: a range, and one question when it isn't sure. That's the film. No villain, no explaining: one dinner, one photo, one question, and then the phone goes face down.

**On-screen type** (Instrument Serif, ≤ 7 words, 4 lines in total):
1. A photo can't weigh your food.
2. So it gives you a range.
3. When it isn't sure, it asks. Once.
4. Nutrients from real data. Not the AI.

Use line 4b instead of line 4 only if campus menu matching is merged and verified before the screen recordings: "Campus menu first. USDA as a backup."

**End card:** MealScholar / Nutrition from a photo, in honest ranges. / mealscholar.mannava.org

**Small print:** Prototype built at HackUMass XIV. Sequences shortened. Estimates, not medical advice.

## 3. Film shot list (0:60, 24 fps)

The 9→10 match cut carries the film. The camera's top-down shot of the plate cuts to the app showing the same photo, then the boxes draw on. So shot 9 and the photo taken in the app must have identical framing.

| # | Time | Shot | Lens / move | Sound |
|---|---|---|---|---|
| 1 | 0:00–0:04 | Outside, golden hour. Low sun through bare trees. Gabby and a friend walk toward the dining hall, breath visible | 1x, operator walks backward ahead of them | Wind, footsteps; music enters softly |
| 2 | 0:04–0:06 | Hand on the door, warm light spilling out | 2x, locked | Door, room swells in |
| 3 | 0:06–0:09 | Food lands on the plate, steam, macro | 2x or macro, locked, shallow | Clink |
| 4 | 0:09–0:11 | A drink pours from the dispenser | 2x, locked | Pour |
| 5 | 0:11–0:14 | Gabby finds a window table; a friend waves from across the room | 3x from far, handheld | Room tone |
| 6 | 0:14–0:17 | Tray lands, side light raking across the food | 1x, top down, slow descent | Tray clack |
| 7 | 0:17–0:19 | Over the shoulder: phone raised over the plate, screen not readable | 1x | |
| 8 | 0:19–0:21 | Top-down hero frame of the plate. Shutter | 1x, locked, same framing as the app photo | Shutter; music cuts out for a beat |
| 9 | 0:21–0:27 | **Match cut** to the full-screen app: the same photo, numbered boxes drawing on, then gram ranges. Type 1, then type 2 | Screen recording | Music returns with a pulse |
| 10 | 0:27–0:30 | Gabby's face in window light, small reaction | 3x, shallow | |
| 11 | 0:30–0:34 | App: the question appears (whatever Gemini really asks). Type 3 | Screen recording | |
| 12 | 0:34–0:38 | Gabby lifts the glass, glances at it, answers | 2x | Sip |
| 13 | 0:38–0:43 | App: the result after the answer, ranges settling | Screen recording | Soft tick |
| 14 | 0:43–0:47 | App: Today, the day so far as ranges. Type 4 | Screen recording | |
| 15 | 0:47–0:50 | Phone set face down on the table | 2x macro | Tap |
| 16 | 0:50–0:54 | Friends sit down, laugh; Gabby takes the first bite | 1x two-shot | Laughter; music peaks |
| 17 | 0:54–0:57 | Through the window from outside at blue hour: their warm table among others | 3x, locked | Music resolves |
| 18 | 0:57–1:00 | End card | | Last note |

**Extra footage to grab** so the edit has options: steam in backlight, cutlery, hands passing a tray, the window and sky, a wide of the hall. Only include faces of people who agreed to be filmed.

## 4. Devpost walkthrough (1:00–1:50)

Harsh's voiceover over clean screen recordings of the same meal, about 110 words, about 45 s:

> MealScholar starts with one photo. Gemini finds each food on the plate and estimates how much is there, as a low, likely and high amount. It never writes a nutrient number: those come from USDA FoodData Central, multiplied by the grams, so you can check the math. If the photo leaves something unclear, it asks one question and recomputes. If it picked the wrong food, swap it with a tap. Every meal adds up into today's totals, still as ranges, and a weekly view that only counts the days you logged. No food scale, no search box. Just a photo, and numbers that are honest about what a photo can tell you.

- Only if campus menu matching is merged and verified, add: "On campus, it checks today's dining hall menu first and uses its nutrition facts. USDA is the backup."
- Optional on-screen line: "About 6 seconds per photo". That's the measured median, 5.9 s, from `BENCHMARK.md`.
- Show only real output. Use the numbers the app actually returns for the filmed plate, not the ones in this plan.

## 5. Camera: making phones look like film

- **App:** Blackmagic Camera (free, iOS and Android), so nothing changes automatically between takes.
- **Format:** 3840×2160, **24 fps**, shutter 1/48. If dining hall LEDs flicker or band, switch to 1/60.
- **Lock** ISO, white balance and focus for each location. Keep ISO as low as the light allows.
- **Color:** use Apple Log on iPhone 15 Pro or newer. On other phones, turn HDR / Dolby Vision off, or the clips look washed out in the editor.
- **File size:** record HEVC. ProRes 4K fills a phone in minutes.
- **Lenses:** 2x or 3x for faces and food. Telephoto compression is most of the "cinematic" look. Avoid 0.5x, which distorts faces.
- **Movement:** one slow move per shot, or locked off. Brace elbows on your body or the table. Use a gimbal if someone has one.
- **Takes:** at least 3 takes of 8–10 s each, so the editor has handles.
- **Exposure:** expose for faces and keep the windows from blowing out. Wipe the lens before every setup.

## 6. Light, set, sound

- **Light:**
  - Window table, low sun, window at 45° to Gabby's face, camera on the shadow side.
  - Avoid tables directly under the ceiling panels.
  - A white napkin or paper on the table works as a bounce to fill shadows.
- **The plate:**
  - 3–4 colorful, separate foods plus **one drink**. In the benchmark, every drink and salad photo got a clarifying question. Without that, the question beat isn't guaranteed.
  - At lunch, test 3–4 candidate plates on the live app. Keep the plate whose real output looks best: clean boxes and a question you can understand from the picture.
- **Wardrobe:** solid muted colors with layers. No logos, no text. No UMass logos without permission.
- **Phone:**
  - Do Not Disturb on, light mode, full battery, clean demo account with a few days already logged.
  - The landing page's mock-ups show 9:41 as the time; match that if you can.
- **Sound:**
  - No sync dialogue in the 60 s film.
  - Record sound effects separately: tray, cutlery, pour, shutter, door, footsteps, laughter.
  - 30 s of room tone at each location.
  - Voiceover: in a closet or a parked car, phone about 15 cm from the mouth and slightly off to the side, 3 takes.
- **Music:** one licensed track (YouTube Audio Library, Pixabay Music or Uppbeat). Its shape: quiet, a lift at the boxes (0:21), a peak at the friends (0:50), and it resolves on the end card.

## 7. Edit and finish

- **Software:** DaVinci Resolve (free) or CapCut. 24 fps timeline. Export H.264 at 1080p or 4K for YouTube.
- **Cutting:** cut on the music. Most shots 2–4 s; only the app beats hold longer.
- **Grade:** convert Log to Rec.709, natural skin, warm but not orange, slightly lifted blacks, light grain at most. No teal-and-orange LUT.
- **Screen recordings:** use the phone's own screen recording at full resolution. In a 16:9 frame, either scale in from the phone in the shot, or center it on the brand page color `#EEF0F2`.
- **On-screen type:**
  - Instrument Serif for the lines, Geist for small text. Both are in `web/fonts/`.
  - Color `#1C1C1C` on light backgrounds, `#FAFAFF` over footage.
  - Fade in and out over 8–12 frames. Hold each line at least 1 s per 3 words.
- **Captions:** an SRT file for the Devpost cut.
- **Reuse:** the same footage fills the landing page's three "Coming soon" recordings: snap to nutrients, one question, check then Today.

## 8. Today

Sunset in Amherst is 4:29 PM and blue hour ends at 4:59 PM. Check dining hall hours before planning around them.

| When (EST) | What | Who |
|---|---|---|
| Now–11:00 | Agree on this plan, roles and which dining hall. Ask UMass Dining for permission. Install Blackmagic Camera. Pick the music | Gabby (owner) + team |
| 11:00–13:00 | Lunch scout: test candidate plates on the live app, find the window table, note where the light comes from | Gabby + Joseph |
| 15:30–16:29 | Golden hour exteriors: shots 1, 2 | Camera + Gabby + friend |
| 16:29–16:59 | Blue hour: shot 17, from outside the window | Camera |
| 16:30–18:00 | Inside the dining hall: shots 3–16, before the dinner rush | Everyone |
| 19:00–20:30 | Screen recordings using the hero plate photo, plus the voiceover | Harsh (app owner) |
| 21:00–23:00 | Rough cut, ready for the 11 PM submission draft | Editor |
| Sun 07:00–08:15 | Grade, captions, export, upload to YouTube | Editor |
| Sun 08:30 | Final submission | Harsh |

**Before the screen recordings:** no merges to `main` from 19:00 to 20:30. Every push redeploys the app. If #7 lands without #13, the old UI comes back in the middle of recording.

## 9. Rules kept from the docx

- Get consent from anyone identifiable. Get dining hall permission. Shoot off-peak.
- Show only what the app does at the time of recording. Real output only. Shortened waits are covered by "Sequences shortened".
- Don't show competitor apps. Don't imply UMass endorsement.
- No API keys, terminals, notifications or other people's diary entries on screen.
