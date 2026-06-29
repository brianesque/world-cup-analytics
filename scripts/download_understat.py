#!/usr/bin/env python3
"""
Download shot and player data from Understat for all 6 leagues and seasons 2014-2024.

Usage:
    pip install aiohttp
    python scripts/download_understat.py

Output structure:
    data/understat/shots/{league}_{season}.json      ← all shots for that league/season
    data/understat/players/{league}_{season}.json    ← player season stats
    data/understat/_ckpt/{league}_{season}/          ← per-match checkpoint files

Estimated time: ~45-90 min (5 concurrent requests, 0.4s delay between batches)
Resume-safe: already-completed files and match checkpoints are skipped automatically.

Coordinate system (Understat):
    X = 0.0 (own goal line) → 1.0 (opponent's goal line)
    Y = 0.0 (left side) → 0.5 (center) → 1.0 (right side)
"""

from __future__ import annotations

import asyncio
import aiohttp
import json
import re
import sys
import time
from pathlib import Path

# ── CONFIG ─────────────────────────────────────────────────────────────────────

LEAGUES = {
    "EPL":        "Premier League",
    "La_liga":    "La Liga",
    "Bundesliga": "Bundesliga",
    "Serie_A":    "Serie A",
    "Ligue_1":    "Ligue 1",
    "RFPL":       "Liga Rusa",
}
# Season key = start year (2014 → 2014/15 season)
SEASONS = [str(y) for y in range(2014, 2025)]

MAX_CONCURRENT = 5    # simultaneous HTTP requests
DELAY           = 0.4  # seconds to sleep after each request
RETRY_DELAY     = 5    # seconds between retries
MAX_RETRIES     = 3

BASE_URL = "https://understat.com"
DATA_DIR = Path(__file__).parent.parent / "data" / "understat"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; research/portfolio project)"}


# ── HTML PARSER ────────────────────────────────────────────────────────────────

def parse_js_var(html: str, var_name: str):
    """
    Extract a JSON-encoded JavaScript variable from an Understat HTML page.
    Understat embeds data as: var NAME = JSON.parse('...')
    The string is unicode-escaped (\\u00e9 etc.) and needs careful decoding.
    """
    pattern = rf"var {var_name}\s*=\s*JSON\.parse\('(.+?)'\)"
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    # Attempt 1: standard unicode_escape → utf-8
    try:
        decoded = raw.encode("utf-8").decode("unicode_escape")
        return json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    # Attempt 2: unicode_escape → latin-1 → utf-8 (handles accented chars)
    try:
        decoded = raw.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
        return json.loads(decoded)
    except Exception as e:
        print(f"    ⚠ Parse error [{var_name}]: {e}")
        return None


# ── FETCHER ────────────────────────────────────────────────────────────────────

async def fetch(session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore) -> str | None:
    async with sem:
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    r.raise_for_status()
                    text = await r.text()
                    await asyncio.sleep(DELAY)
                    return text
            except Exception as e:
                wait = RETRY_DELAY * (attempt + 1)
                print(f"    ⚠ {url.split('/')[-1]} attempt {attempt+1} failed: {e} (retry in {wait}s)")
                await asyncio.sleep(wait)
        print(f"    ✗ GIVE UP: {url}")
        return None


# ── MATCH DOWNLOADER ───────────────────────────────────────────────────────────

async def download_match_shots(
    session, sem, match_id: str, league: str, season: str, ckpt_dir: Path
) -> list[dict]:
    """
    Fetch shot data for one match. Returns list of shot dicts (home + away combined).
    Uses checkpoint file to avoid re-fetching.
    """
    ckpt_file = ckpt_dir / f"{match_id}.json"
    if ckpt_file.exists():
        with open(ckpt_file) as f:
            return json.load(f)

    html = await fetch(session, f"{BASE_URL}/match/{match_id}", sem)
    if not html:
        return []

    raw = parse_js_var(html, "shotsData")
    if not raw:
        return []

    # raw = {"h": [...], "a": [...]}
    shots = []
    for side in ("h", "a"):
        for shot in raw.get(side, []):
            shot["league"]  = league
            shot["season"]  = season
            shot["match_id"] = match_id
            shots.append(shot)

    with open(ckpt_file, "w") as f:
        json.dump(shots, f)
    return shots


# ── SEASON DOWNLOADER ──────────────────────────────────────────────────────────

async def download_season(session, sem, league: str, season: str) -> None:
    shots_dir   = DATA_DIR / "shots"
    players_dir = DATA_DIR / "players"
    ckpt_dir    = DATA_DIR / "_ckpt" / f"{league}_{season}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    out_shots   = shots_dir / f"{league}_{season}.json"
    out_players = players_dir / f"{league}_{season}.json"

    label = f"{LEAGUES[league]} {season}/{int(season)+1}"

    # ── 1. Player stats (single page fetch) ────────────────────────────────────
    if not out_players.exists():
        html = await fetch(session, f"{BASE_URL}/league/{league}/{season}", sem)
        if html:
            players = parse_js_var(html, "playersData")
            if players:
                with open(out_players, "w") as f:
                    json.dump(players, f)
                print(f"  ✓ {label}: {len(players)} players")
            else:
                print(f"  ✗ {label}: no playersData found")
        # Reuse html for matchesData below
        matches_html = html
    else:
        matches_html = None  # will fetch separately if needed for shots

    # ── 2. Shot data (per-match, checkpoint-based) ────────────────────────────
    if out_shots.exists():
        existing = json.loads(out_shots.read_text())
        print(f"  ↩ {label}: {len(existing)} shots (cached)")
        return

    if matches_html is None:
        matches_html = await fetch(session, f"{BASE_URL}/league/{league}/{season}", sem)

    if not matches_html:
        print(f"  ✗ {label}: failed to fetch league page")
        return

    matches_raw = parse_js_var(matches_html, "matchesData")
    if not matches_raw:
        print(f"  ✗ {label}: no matchesData found")
        return

    # matchesData is a dict keyed by match_id → normalize to list
    if isinstance(matches_raw, dict):
        match_list = list(matches_raw.values())
    else:
        match_list = matches_raw

    match_ids = [str(m.get("id") or m.get("match_id", "")) for m in match_list]
    match_ids = [mid for mid in match_ids if mid]

    print(f"  ↓ {label}: {len(match_ids)} matches ...", end=" ", flush=True)

    # Fetch all matches concurrently (semaphore limits parallelism)
    tasks   = [download_match_shots(session, sem, mid, league, season, ckpt_dir) for mid in match_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_shots = []
    errors    = 0
    for r in results:
        if isinstance(r, Exception):
            errors += 1
        elif isinstance(r, list):
            all_shots.extend(r)

    with open(out_shots, "w") as f:
        json.dump(all_shots, f)

    status = f"✓ {len(all_shots)} shots"
    if errors:
        status += f" ({errors} match errors)"
    print(status)


# ── MAIN ───────────────────────────────────────────────────────────────────────

async def main():
    (DATA_DIR / "shots").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "players").mkdir(parents=True, exist_ok=True)

    total  = len(LEAGUES) * len(SEASONS)
    done   = 0
    t_start = time.time()

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, ssl=False)

    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        for league in LEAGUES:
            print(f"\n── {LEAGUES[league]} ──────────────────────────────────")
            for season in SEASONS:
                done += 1
                elapsed = time.time() - t_start
                if done > 1 and elapsed > 0:
                    eta = elapsed / (done - 1) * (total - done + 1)
                    eta_min = int(eta // 60)
                    print(f"[{done}/{total}] ETA ~{eta_min} min")
                else:
                    print(f"[{done}/{total}]")
                await download_season(session, sem, league, season)

    elapsed_min = int((time.time() - t_start) // 60)
    print(f"\n✓ Done in {elapsed_min} min. Data saved to: {DATA_DIR}")
    print(f"  Next step: python scripts/build_league_trends.py")


if __name__ == "__main__":
    try:
        import aiohttp
    except ImportError:
        print("Missing dependency: pip install aiohttp")
        sys.exit(1)
    asyncio.run(main())
