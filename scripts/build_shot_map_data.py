"""
build_shot_map_data.py
-----------------------
Rebuilds the embedded shot dataset used by docs/shot-map/index.html, from
StatsBomb's free, public Open Data repository:
  https://github.com/statsbomb/open-data

You will almost never need to run this — both World Cups are over, the
data can't change, and it's already embedded in index.html. It's included
so the pipeline is fully reproducible and auditable.

What it does:
  1. Downloads match lists + every match's event file for the World Cups
     you list in EDITIONS, straight from StatsBomb's GitHub repo.
  2. Filters down to Shot events only, excludes penalty shootouts (period 5)
     since those aren't meaningful for an in-game shot-distance analysis.
  3. Computes distance-to-goal in meters (StatsBomb's pitch units are
     conventionally treated as ~yards: 120 x 80 = roughly a full-size
     pitch; 1 unit ~= 1 yard ~= 0.9144 m — this is an approximation, not an
     exact unit conversion, and is labelled as such wherever it's shown).
  4. Writes shots_data.json — paste/replace this into the `const SHOTS = `
     line in docs/shot-map/index.html if you ever need to regenerate it
     (e.g. to add another World Cup StatsBomb has since released for free).

Run:
    pip install pandas
    python scripts/build_shot_map_data.py
"""

import json
import math
from pathlib import Path

import pandas as pd

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/"
ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "scripts" / "shots_data.json"

# competition_id is always 43 for the Men's World Cup on StatsBomb;
# season_id changes per edition. Add more editions here if StatsBomb ever
# frees up another tournament's data (check data/competitions.json first).
EDITIONS = [
    ("2018", 43, 3),
    ("2022", 43, 106),
]

X_GOAL, Y_GOAL = 120, 40          # StatsBomb goal-mouth center
PENALTY_BOX_X, BOX_Y = 102, (18, 62)
SIX_YARD_X, SIX_YARD_Y = 114, (30, 50)
YARD_TO_METER = 0.9144


def fetch_json(url):
    return json.loads(pd.io.common.urlopen(url).read())


def build():
    rows = []
    for edition, comp_id, season_id in EDITIONS:
        matches = fetch_json(f"{BASE}data/matches/{comp_id}/{season_id}.json")
        match_info = {m["match_id"]: m for m in matches}
        print(f"{edition}: {len(matches)} matches")

        for match_id, minfo in match_info.items():
            events = fetch_json(f"{BASE}data/events/{match_id}.json")
            stage = minfo.get("competition_stage", {}).get("name", "")
            home = minfo.get("home_team", {}).get("home_team_name", "")
            away = minfo.get("away_team", {}).get("away_team_name", "")
            date = minfo.get("match_date", "")

            for e in events:
                if e.get("type", {}).get("name") != "Shot":
                    continue
                if e.get("period") == 5:  # exclude penalty shootouts
                    continue
                shot = e.get("shot", {})
                loc = e.get("location")
                if not loc:
                    continue
                x, y = loc[0], loc[1]
                dist_units = math.hypot(X_GOAL - x, Y_GOAL - y)
                dist_m = round(dist_units * YARD_TO_METER, 2)
                in_box = bool(x >= PENALTY_BOX_X and BOX_Y[0] <= y <= BOX_Y[1])
                six_yard = bool(x >= SIX_YARD_X and SIX_YARD_Y[0] <= y <= SIX_YARD_Y[1])
                outcome = shot.get("outcome", {}).get("name", "")
                outcome_grp = (
                    "Goal" if outcome == "Goal"
                    else "On Target" if outcome in ("Saved", "Saved to Post")
                    else "Blocked" if outcome == "Blocked"
                    else "Off Target"
                )
                rows.append({
                    "edition": edition, "match_id": match_id, "date": date,
                    "home": home, "away": away, "stage": stage,
                    "team": e.get("team", {}).get("name", ""),
                    "player": e.get("player", {}).get("name", ""),
                    "minute": e.get("minute"),
                    "x": round(x, 1), "y": round(y, 1), "dist_m": dist_m,
                    "in_box": in_box, "six_yard": six_yard,
                    "outcome": outcome, "outcome_grp": outcome_grp,
                    "body_part": shot.get("body_part", {}).get("name", ""),
                    "play_pattern": e.get("play_pattern", {}).get("name", ""),
                    "shot_type": shot.get("type", {}).get("name", ""),
                    "xg": round(shot.get("statsbomb_xg", 0) or 0, 3),
                    "is_goal": outcome == "Goal",
                })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {OUT_PATH} — {len(rows)} shots across {len(EDITIONS)} editions.")
    print("Paste this into the `const SHOTS = ...;` line in docs/shot-map/index.html if regenerating.")


if __name__ == "__main__":
    build()
