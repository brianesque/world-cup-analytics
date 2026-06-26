# World Cup Analytics

Two football data-visualization projects, built to show two different
skills: working with **real historical event data** (StatsBomb) and keeping
a dashboard **alive on top of a live, occasionally-lagging public source**
(the 2026 World Cup tracker).

**Live site:** enable GitHub Pages (see below) and it'll be at
`https://<your-username>.github.io/<repo-name>/`

```
docs/                    <- this is what GitHub Pages actually serves
  index.html             <- landing page linking the two dashboards
  tracker/
    index.html           <- 2026 Live Tournament Tracker (fetches data.json)
    data.json             <- generated file, rebuilt daily — don't hand-edit
  shot-map/
    index.html            <- 2018+2022 Shot Map (data embedded, static forever)
scripts/
  build_tracker_data.py   <- rebuilds docs/tracker/data.json from scratch
  build_shot_map_data.py  <- rebuilds the shot map's embedded data (rarely needed)
  manual_overrides.json   <- hand-verified results, see "Manual overrides" below
.github/workflows/
  update_tracker.yml      <- GitHub Action: runs build_tracker_data.py daily
```

## The two dashboards

### 1. 2026 Live Tournament Tracker (`docs/tracker/`)

Group standings, top scorers/assists, discipline, results and fixtures for
the **ongoing** FIFA World Cup 2026.

- **Data source:** ["FIFA World Cup 2026 Dataset" by MD Mominul Islam](https://github.com/mominullptr/FIFA-World-Cup-2026-Dataset)
  (CC0 license). It's a relational dataset of teams/venues/matches/events,
  refreshed by the maintainer as real matches conclude.
- **Why it stays current without anyone touching the HTML:** the page
  doesn't contain any data itself — it `fetch()`es `data.json` at load time.
  A GitHub Action (`update_tracker.yml`) runs `build_tracker_data.py` once a
  day, which re-downloads the source CSVs fresh and regenerates `data.json`.
  Only that one small file changes; the page never needs to be rebuilt.
- **Known limitation:** the source repo is maintained by hand and can lag a
  match or two behind real life, sometimes by a day. Matches still marked
  `"Scheduled"` in the source also have **placeholder venue/stadium fields**
  that are not reliable — only matches marked `"Completed"` have a verified
  venue. The page doesn't try to hide this; treat "Scheduled" fixtures as
  "who plays who and roughly when", not as confirmed logistics.

#### Manual overrides

Sometimes the source hasn't updated yet but the result is already public
(news outlets, FIFA's own match centre). `scripts/manual_overrides.json`
holds hand-verified patches for exactly those cases. Each entry only gets
applied **if the source still shows that match as not-`Completed`** — once
the upstream dataset catches up on its own, the override is automatically
skipped, so reruns never double-count or conflict with real data.

To add one: copy the existing structure in `manual_overrides.json` (team
names must match exactly what's in `teams.csv` upstream), cite where you
verified it, and list the `Goal`/`Assist`/card events you can attribute with
confidence. If you can't confirm who scored (e.g. a deflected own goal),
leave it out of the events list — it still counts toward the final score,
just not toward the individual scorer leaderboard.

### 2. 2018 & 2022 Shot Map (`docs/shot-map/`)

Every shot (not just goals — 3,120 of them) from all 64 matches of each of
the last two World Cups, plotted on a pitch with filters for team, stage,
body part, play pattern, and goals-only vs. all shots.

- **Data source:** [StatsBomb Open Data](https://github.com/statsbomb/open-data)
  (free, public, explicitly licensed for personal/portfolio use). Real
  event-level data — actual x/y shot locations, not estimates.
- **Why this one doesn't need updating:** both tournaments are long over.
  The data is embedded directly in `index.html` and will never change.
  `scripts/build_shot_map_data.py` is included only so the whole pipeline
  is reproducible / auditable — you will not normally need to run it again.

## Putting this on GitHub Pages (so anyone with the link can open it)

1. Push this repo to GitHub (public repo — Pages on a free plan needs the
   repo to be public to get a public URL).
2. In the repo: **Settings → Pages → Build and deployment → Source: "Deploy
   from a branch"**, branch `main`, folder `/docs`. Save.
3. GitHub gives you a URL like `https://<username>.github.io/<repo>/` within
   a minute or two. That URL is public — no login, no account needed for
   anyone who opens it. There's no way to make a GitHub Pages site
   "private" on a free personal account; if that's ever a requirement,
   the workaround is a private repo + GitHub Pro/Team, which serves the
   same Pages site but enforces GitHub login for viewers.
4. The "Update World Cup 2026 tracker data" Action under the **Actions**
   tab will run automatically every day at 12:00 UTC, and you can also hit
   **"Run workflow"** there any time you want a fresh pull right now.

## Running things locally

```bash
pip install pandas
python scripts/build_tracker_data.py     # rewrites docs/tracker/data.json
```

To preview `docs/tracker/index.html` locally, don't just double-click the
file — browsers block `fetch()` against local files for security reasons.
Instead, from the `docs/` folder run a tiny local server:

```bash
cd docs
python -m http.server 8000
# then open http://localhost:8000/tracker/ in your browser
```

The shot map (`docs/shot-map/index.html`) has its data embedded, so it
*does* open fine directly from disk.

## Data integrity notes (so this holds up if anyone checks)

- All "real" data claims in this repo were verified against the live data
  itself (downloaded and inspected row by row), not just taken from a
  dataset's description text.
- Own goals are intentionally **not** added to the individual scorer
  leaderboard in the tracker — the source schema has no own-goal event
  type, and crediting it to a player as a "Goal" would misrepresent it.
  They're still reflected correctly in the match score itself.
- Nothing here claims to be official FIFA data. The CC0 dataset behind the
  tracker is community-maintained and explicitly *not* affiliated with FIFA;
  it's sourced from fifa.com and sofascore.com per its own `data_source`
  column, with full traceability.

## License / attribution

- StatsBomb Open Data: see [their license](https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf)
  (free for public, personal, and non-commercial use with attribution).
- "FIFA World Cup 2026 Dataset" by MD Mominul Islam: CC0 (public domain).
- This repo's own code: do whatever you want with it.
