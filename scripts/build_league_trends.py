#!/usr/bin/env python3
"""
Aggregate raw Understat shot data into docs/trends/data.json for the dashboard.

Usage:
    python scripts/build_league_trends.py

Reads:   data/understat/shots/{league}_{season}.json   (from download_understat.py)
Writes:  docs/trends/data.json

Coordinate system (Understat normalized):
    X = 0.0 (own goal line) → 1.0 (opponent's goal line)
    Y = 0.0 (left touchline) → 0.5 (center) → 1.0 (right touchline)
    All shots in the dataset are from the attacking direction.

Derived metrics:
    dist_m      – distance to goal in meters (see shot_distance())
    in_box      – shot from inside the penalty area  (X > 0.843)
    centrality  – how lateral-central the shot is; 1 = perfectly centered, 0 = at sideline
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# ── PATHS ──────────────────────────────────────────────────────────────────────
REPO_ROOT  = Path(__file__).parent.parent
SHOTS_DIR  = REPO_ROOT / "data" / "understat" / "shots"
OUT_FILE   = REPO_ROOT / "docs" / "trends" / "data.json"

# ── LEAGUE METADATA ────────────────────────────────────────────────────────────
LEAGUES = {
    "EPL":        {"name": "Premier League", "country": "Inglaterra", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "color": "#00a8e8", "iso": 826},
    "La_liga":    {"name": "La Liga",         "country": "España",     "flag": "🇪🇸",        "color": "#ff6900", "iso": 724},
    "Bundesliga": {"name": "Bundesliga",      "country": "Alemania",   "flag": "🇩🇪",        "color": "#e8c320", "iso": 276},
    "Serie_A":    {"name": "Serie A",         "country": "Italia",     "flag": "🇮🇹",        "color": "#1d9e54", "iso": 380},
    "Ligue_1":    {"name": "Ligue 1",         "country": "Francia",    "flag": "🇫🇷",        "color": "#bf5af2", "iso": 250},
    "RFPL":       {"name": "Liga Rusa",       "country": "Rusia",      "flag": "🇷🇺",        "color": "#ff3a5e", "iso": 643},
}
SEASONS = [str(y) for y in range(2014, 2025)]

# ── PITCH GEOMETRY ─────────────────────────────────────────────────────────────
PITCH_LEN   = 105.0  # metres (goal line to goal line)
PITCH_WIDTH = 68.0   # metres

# Penalty area: 16.5m from goal line; in normalized X coords:
IN_BOX_X_THRESHOLD = 1.0 - (16.5 / PITCH_LEN)  # ≈ 0.843

# Distance histogram bin edges (metres)
DIST_BINS   = [0, 5, 10, 15, 20, 25, 30, 40, 60]
DIST_LABELS = ["0-5", "5-10", "10-15", "15-20", "20-25", "25-30", "30-40", "40+"]


# ── MATH HELPERS ───────────────────────────────────────────────────────────────

def shot_distance(x: float, y: float) -> float:
    """Straight-line distance from shot position to goal centre (metres)."""
    dx = (1.0 - x) * PITCH_LEN
    dy = (y - 0.5) * PITCH_WIDTH
    return math.sqrt(dx * dx + dy * dy)


def centrality(y: float) -> float:
    """
    Lateral centrality score: 1 = dead-centre (y=0.5), 0 = at a touchline.
    Formula: 1 - 2*|y - 0.5|
    """
    return max(0.0, 1.0 - 2.0 * abs(y - 0.5))


def median(lst: list) -> float:
    if not lst:
        return 0.0
    s = sorted(lst)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def pct(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


# ── AGGREGATION ────────────────────────────────────────────────────────────────

def aggregate(shots: list[dict]) -> dict:
    """Compute all dashboard metrics for a list of Understat shot dicts."""
    n = len(shots)
    if n == 0:
        return {}

    goals     = 0
    xg_total  = 0.0
    dists     = []
    centrals  = []
    in_box    = 0
    situations = {}
    shot_types = {}

    for s in shots:
        try:
            x = float(s["X"])
            y = float(s["Y"])
        except (KeyError, ValueError, TypeError):
            continue

        result = s.get("result", "")
        if result == "Goal":
            goals += 1

        try:
            xg_total += float(s.get("xG", 0) or 0)
        except (ValueError, TypeError):
            pass

        d = shot_distance(x, y)
        dists.append(d)
        centrals.append(centrality(y))

        if x >= IN_BOX_X_THRESHOLD:
            in_box += 1

        sit = s.get("situation", "OpenPlay") or "OpenPlay"
        situations[sit] = situations.get(sit, 0) + 1

        st = s.get("shotType", "Unknown") or "Unknown"
        shot_types[st] = shot_types.get(st, 0) + 1

    # Distance histogram
    bins = [0] * len(DIST_LABELS)
    for d in dists:
        placed = False
        for i, edge in enumerate(DIST_BINS[1:]):
            if d < edge:
                bins[i] += 1
                placed = True
                break
        if not placed:
            bins[-1] += 1

    n_valid = len(dists)
    return {
        "shots":          n,
        "goals":          goals,
        "xg_total":       round(xg_total, 2),
        "xg_per_shot":    round(xg_total / n_valid, 4) if n_valid else 0,
        "conversion":     pct(goals, n),
        "pct_in_box":     pct(in_box, n),
        "avg_dist":       round(sum(dists) / n_valid, 2) if n_valid else 0,
        "median_dist":    round(median(dists), 2),
        "avg_centrality": round(sum(centrals) / n_valid, 4) if n_valid else 0,
        "dist_bins":      bins,
        "dist_labels":    DIST_LABELS,
        "by_situation":   dict(sorted(situations.items(), key=lambda x: -x[1])),
        "by_shot_type":   dict(sorted(shot_types.items(), key=lambda x: -x[1])),
    }


# ── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    if not SHOTS_DIR.exists():
        print(f"✗ Shots directory not found: {SHOTS_DIR}")
        print("  Run scripts/download_understat.py first.")
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "leagues":     {},
        "all_seasons": SEASONS,
        "dist_labels": DIST_LABELS,
    }

    total_shots  = 0
    found_files  = 0

    for league_key, meta in LEAGUES.items():
        print(f"\n── {meta['name']} ─────────────────────────────")
        output["leagues"][league_key] = {
            **meta,
            "seasons": {},
        }

        for season in SEASONS:
            path = SHOTS_DIR / f"{league_key}_{season}.json"
            if not path.exists():
                print(f"  {season}: missing")
                continue

            shots = json.loads(path.read_text())
            stats = aggregate(shots)
            if not stats:
                print(f"  {season}: empty")
                continue

            output["leagues"][league_key]["seasons"][season] = {
                "label":  f"{season}/{int(season)+1}",
                **stats,
            }
            total_shots += stats["shots"]
            found_files += 1
            print(
                f"  {season}: {stats['shots']:>6} shots | "
                f"{stats['goals']:>4} goals | "
                f"xG/shot {stats['xg_per_shot']:.3f} | "
                f"median dist {stats['median_dist']:.1f}m | "
                f"in box {stats['pct_in_box']*100:.0f}%"
            )

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n✓ Wrote {OUT_FILE}")
    print(f"  {found_files} league-seasons | {total_shots:,} total shots")
    print(f"  File size: {OUT_FILE.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
