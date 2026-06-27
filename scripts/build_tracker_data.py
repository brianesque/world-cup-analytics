"""
build_tracker_data.py
----------------------
Rebuilds docs/tracker/data.json from the live "FIFA World Cup 2026 Dataset"
by MD Mominul Islam (CC0 license):
  https://github.com/mominullptr/FIFA-World-Cup-2026-Dataset

This script is meant to be run:
  - manually, whenever you want a fresh snapshot, OR
  - automatically, once a day, by .github/workflows/update_tracker.yml

It does THREE things, in order:
  1. Downloads the latest CSVs straight from the source repo on GitHub.
  2. Applies any entries from manual_overrides.json — these are results we
     verified by hand (news outlets / FIFA match centre) for matches that
     the source repo hadn't marked "Completed" yet at the time we checked.
     Each override only gets applied IF the source still shows that exact
     match as not-Completed, so once the upstream catches up, our override
     quietly stops being needed (no double-counting, no conflicts).
  3. Aggregates everything into docs/tracker/data.json, which is the only
     file the live page (docs/tracker/index.html) actually reads at runtime.

Why split data from the page like this?
The page never needs to be rebuilt. Only this JSON file changes, daily.
That's what keeps the dashboard "alive" without anyone hand-editing HTML.

Run locally:
    pip install pandas
    python scripts/build_tracker_data.py
"""

import json
import os
from pathlib import Path

import pandas as pd

BASE = "https://raw.githubusercontent.com/mominullptr/FIFA-World-Cup-2026-Dataset/main/"
ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "docs" / "tracker" / "data.json"
OVERRIDES_PATH = ROOT / "scripts" / "manual_overrides.json"


def fetch_csv(name):
    return pd.read_csv(BASE + name)


def load_overrides():
    if not OVERRIDES_PATH.exists():
        return []
    with open(OVERRIDES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_overrides(matches_df, events_df, team_stats_df, overrides):
    """Patch in verified results for matches the source hasn't updated yet.
    Skips an override automatically once the source itself shows the match
    as Completed, so reruns never double-apply or conflict with real data.
    """
    next_event_id = int(events_df.event_id.max()) + 1 if len(events_df) else 1
    applied = []

    for ov in overrides:
        mask = (matches_df.home_team_name == ov["home"]) & (matches_df.away_team_name == ov["away"])
        if not mask.any():
            continue
        current_status = matches_df.loc[mask, "status"].iloc[0]
        if current_status == "Completed":
            continue  # upstream has already caught up — nothing to do

        matches_df.loc[mask, "date"] = ov["date"]
        matches_df.loc[mask, "stadium_name"] = ov["stadium_name"]
        matches_df.loc[mask, "city"] = ov["city"]
        matches_df.loc[mask, "home_score"] = ov["home_score"]
        matches_df.loc[mask, "away_score"] = ov["away_score"]
        matches_df.loc[mask, "status"] = "Completed"
        match_id = int(matches_df.loc[mask, "match_id"].iloc[0])

        new_rows = []
        for ev in ov.get("events", []):
            new_rows.append({
                "event_id": next_event_id,
                "match_id": match_id,
                "minute": ev["minute"],
                "event_type": ev["event_type"],
                "team_id": ev["team_id"],
                "player_id": ev["player_id"],
            })
            next_event_id += 1
        if new_rows:
            events_df = pd.concat([events_df, pd.DataFrame(new_rows)], ignore_index=True)

        for ts in ov.get("team_stats", []):
            row = {c: ts.get(c) for c in team_stats_df.columns}
            row["match_id"] = match_id
            team_stats_df = pd.concat([team_stats_df, pd.DataFrame([row])], ignore_index=True)

        applied.append(f"{ov['home']} vs {ov['away']}")

    return matches_df, events_df, team_stats_df, applied


def build():
    teams = fetch_csv("teams.csv")
    matches = fetch_csv("matches_detailed.csv")
    team_stats = fetch_csv("match_team_stats.csv")
    events = fetch_csv("match_events.csv")
    squads = fetch_csv("squads_and_players.csv")

    overrides = load_overrides()
    matches, events, team_stats, applied = apply_overrides(matches, events, team_stats, overrides)
    if applied:
        print("Applied manual overrides for:", ", ".join(applied))

    team_id_to_name = dict(zip(teams.team_id, teams.team_name))
    team_name_to_group = dict(zip(teams.team_name, teams.group_letter))
    player_id_to_name = dict(zip(squads.player_id, squads.player_name))
    player_id_to_team = dict(zip(squads.player_id, squads.team_id))

    completed = matches[matches.status == "Completed"].copy()
    upcoming = matches[matches.status == "Scheduled"].copy()

    # ---- group standings (group stage only) ----
    group_matches = completed[completed.stage_name == "Group Stage"]
    standings = {}

    def get_or_init(team):
        if team not in standings:
            standings[team] = {"team": team, "group": team_name_to_group.get(team, "?"),
                                "P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "Pts": 0}
        return standings[team]

    for _, m in group_matches.iterrows():
        h, a = m["home_team_name"], m["away_team_name"]
        hs, as_ = int(m["home_score"]), int(m["away_score"])
        sh, sa = get_or_init(h), get_or_init(a)
        sh["P"] += 1; sa["P"] += 1
        sh["GF"] += hs; sh["GA"] += as_
        sa["GF"] += as_; sa["GA"] += hs
        if hs > as_:
            sh["W"] += 1; sh["Pts"] += 3; sa["L"] += 1
        elif hs < as_:
            sa["W"] += 1; sa["Pts"] += 3; sh["L"] += 1
        else:
            sh["D"] += 1; sa["D"] += 1; sh["Pts"] += 1; sa["Pts"] += 1

    for t in teams.team_name:
        get_or_init(t)
    for s in standings.values():
        s["GD"] = s["GF"] - s["GA"]
    standings_list = sorted(standings.values(), key=lambda s: (s["group"], -s["Pts"], -s["GD"], -s["GF"]))

    def match_row(m):
        return {
            "match_id": int(m["match_id"]), "date": m["date"],
            "stage": m["stage_name"], "home": m["home_team_name"], "away": m["away_team_name"],
            "home_score": None if pd.isna(m["home_score"]) else int(m["home_score"]),
            "away_score": None if pd.isna(m["away_score"]) else int(m["away_score"]),
            "status": m["status"], "stadium": m["stadium_name"], "city": m["city"],
            "potm": "" if pd.isna(m.get("player_of_the_match_name")) else m.get("player_of_the_match_name"),
            "referee": "" if pd.isna(m.get("referee_name")) else m.get("referee_name"),
        }

    results = [match_row(m) for _, m in completed.sort_values("date").iterrows()]
    fixtures = [match_row(m) for _, m in upcoming.sort_values("date").iterrows()]

    def event_leaderboard(event_type):
        sub = events[events.event_type == event_type]
        counts = sub.groupby("player_id").size()
        rows = [{"player": player_id_to_name.get(pid, "Unknown"),
                 "team": team_id_to_name.get(player_id_to_team.get(pid), "?"),
                 "count": int(cnt)} for pid, cnt in counts.items()]
        return sorted(rows, key=lambda r: -r["count"])

    top_scorers = event_leaderboard("Goal")
    top_assists = event_leaderboard("Assist")
    yellow_cards = event_leaderboard("Yellow Card")
    red_cards = event_leaderboard("Red Card")

    ts = team_stats.copy()
    ts["team_name"] = ts["team_id"].map(team_id_to_name)
    team_agg = ts.groupby("team_name").agg(
        matches=("match_id", "count"),
        avg_possession=("possession_pct", "mean"),
        avg_shots=("total_shots", "mean"),
        avg_sot=("shots_on_target", "mean"),
        avg_corners=("corners", "mean"),
        avg_fouls=("fouls", "mean"),
        total_saves=("saves", "sum"),
    ).round(1).reset_index()
    team_agg_list = json.loads(team_agg.to_json(orient="records"))

    total_goals = int(completed["home_score"].sum() + completed["away_score"].sum())
    kpis = {
        "matches_completed": int(len(completed)),
        "matches_remaining": int(len(upcoming)),
        "total_matches": int(len(matches)),
        "total_goals": total_goals,
        "avg_goals_per_match": round(total_goals / len(completed), 2) if len(completed) else 0,
        "last_updated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
    }

    data = {
        "standings": standings_list, "results": results, "fixtures": fixtures,
        "top_scorers": top_scorers, "top_assists": top_assists,
        "yellow_cards": yellow_cards, "red_cards": red_cards,
        "team_stats": team_agg_list, "kpis": kpis,
        "groups": sorted(teams.group_letter.unique().tolist()),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote {OUT_PATH} — {kpis['matches_completed']} matches completed, {kpis['total_goals']} goals.")


if __name__ == "__main__":
    build()
