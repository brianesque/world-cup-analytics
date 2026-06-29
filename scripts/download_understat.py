#!/usr/bin/env python3
"""
Download shot data from Understat using Playwright (headless Chromium).
Understat is a JavaScript SPA — plain HTTP won't work, we need a real browser.

Setup (una sola vez):
    pip3 install playwright
    python3 -m playwright install chromium

Usage:
    python3 scripts/download_understat.py

Estrategia:
  1. Página de liga  → datesData  (lista de partidos con IDs)
  2. /match/{id}    → shotsData   (disparos individuales con X, Y, xG, etc.)
  3. Checkpoint por partido → combinar en shots/{league}_{season}.json

Total: ~25.000 páginas de partido · 5 tabs paralelas · ~3s/página ≈ 4-5 hs.
Resume-safe: partidos y temporadas ya descargadas se saltean.

Campos de cada disparo (Understat):
    id, minute, result, X, Y, xG, player, player_id,
    h_a, situation, season, shotType, match_id,
    h_team, a_team, h_goals, a_goals, date,
    player_assisted, lastAction
    + [league] agregado por este script

Coordenadas normalizadas:
    X = 0.0 (arco propio) → 1.0 (arco rival)
    Y = 0.0 (banda izq.)  → 0.5 (centro)    → 1.0 (banda der.)
"""
from __future__ import annotations

import asyncio
import json
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
SEASONS    = [str(y) for y in range(2014, 2025)]
MAX_TABS   = 5      # tabs paralelas para partidos
PAGE_DELAY = 0.5    # segundos extra post-carga
BASE_URL   = "https://understat.com"
DATA_DIR   = Path(__file__).parent.parent / "data" / "understat"


# ── HELPERS ────────────────────────────────────────────────────────────────────

async def js_var(page, name: str):
    try:
        return await page.evaluate(f"() => (typeof {name} !== 'undefined') ? {name} : null")
    except Exception:
        return None


async def goto(page, url: str, wait_var: str | None = None, timeout: int = 45_000):
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    if wait_var:
        try:
            await page.wait_for_function(
                f"typeof {wait_var} !== 'undefined'", timeout=timeout
            )
        except Exception:
            pass
    await asyncio.sleep(PAGE_DELAY)


# ── DESCARGA DE UN PARTIDO ────────────────────────────────────────────────────

async def download_match(browser, sem: asyncio.Semaphore, match_id: str,
                         league: str, season: str, ckpt_dir: Path) -> list[dict]:
    ckpt_file = ckpt_dir / f"{match_id}.json"
    if ckpt_file.exists():
        return json.loads(ckpt_file.read_text())

    async with sem:
        page = await browser.new_page()
        try:
            await goto(page, f"{BASE_URL}/match/{match_id}", wait_var="shotsData")
            shots_raw = await js_var(page, "shotsData")

            shots: list[dict] = []
            if shots_raw and isinstance(shots_raw, dict):
                for shot_list in shots_raw.values():   # keys: "h", "a"
                    for s in (shot_list or []):
                        s["league"] = league
                        shots.append(s)

            ckpt_file.write_text(json.dumps(shots, ensure_ascii=False))
            return shots
        except Exception as e:
            print(f"    ✗ match {match_id}: {e}")
            return []
        finally:
            await page.close()


# ── DESCARGA DE UNA TEMPORADA ─────────────────────────────────────────────────

async def download_season(browser, sem: asyncio.Semaphore,
                          league: str, season: str) -> None:
    label       = f"{LEAGUES[league]} {season}/{int(season)+1}"
    shots_dir   = DATA_DIR / "shots"
    players_dir = DATA_DIR / "players"
    ckpt_dir    = DATA_DIR / "_ckpt" / f"{league}_{season}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    out_shots   = shots_dir   / f"{league}_{season}.json"
    out_players = players_dir / f"{league}_{season}.json"
    match_list_file = ckpt_dir / "_matches.json"

    # ── Ya completado ──────────────────────────────────────────────────────────
    if out_shots.exists() and out_players.exists():
        n = len(json.loads(out_shots.read_text()))
        print(f"  ↩ {label}: {n} disparos (ya descargado)")
        return

    # ── 1. Página de liga: player stats + lista de partidos ───────────────────
    if not match_list_file.exists() or not out_players.exists():
        print(f"  ↓ {label}: cargando página de liga…", flush=True)
        async with sem:
            page = await browser.new_page()
            try:
                await goto(page, f"{BASE_URL}/league/{league}/{season}",
                           wait_var="datesData")

                players_data = await js_var(page, "playersData")
                dates_data   = await js_var(page, "datesData")

                if players_data and not out_players.exists():
                    out_players.write_text(
                        json.dumps(players_data, ensure_ascii=False)
                    )
                    print(f"    ✓ {len(players_data)} jugadores")

                if dates_data:
                    match_list_file.write_text(
                        json.dumps(dates_data, ensure_ascii=False, default=str)
                    )
            finally:
                await page.close()

    if not match_list_file.exists():
        print(f"  ✗ {label}: no se pudo obtener la lista de partidos")
        return

    dates_data = json.loads(match_list_file.read_text())
    match_ids  = [str(m["id"]) for m in dates_data if m.get("id") and m.get("isResult")]

    # También incluir partidos no jugados aún (por si la temporada está en curso)
    if not match_ids:
        match_ids = [str(m["id"]) for m in dates_data if m.get("id")]

    already_done = sum(1 for mid in match_ids if (ckpt_dir / f"{mid}.json").exists())
    print(f"  ↓ {label}: {len(match_ids)} partidos ({already_done} ya descargados)")

    # ── 2. Disparos por partido (paralelo) ────────────────────────────────────
    tasks = [
        download_match(browser, sem, mid, league, season, ckpt_dir)
        for mid in match_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_shots: list[dict] = []
    errors = 0
    for r in results:
        if isinstance(r, Exception):
            errors += 1
        elif isinstance(r, list):
            all_shots.extend(r)

    out_shots.write_text(json.dumps(all_shots, ensure_ascii=False))
    status = f"✓ {label}: {len(all_shots)} disparos"
    if errors:
        status += f" ({errors} partidos con error)"
    print(f"  {status}")


# ── MAIN ───────────────────────────────────────────────────────────────────────

async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Falta Playwright:")
        print("  pip3 install playwright")
        print("  python3 -m playwright install chromium")
        sys.exit(1)

    (DATA_DIR / "shots").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "players").mkdir(parents=True, exist_ok=True)

    total   = len(LEAGUES) * len(SEASONS)
    done    = 0
    t_start = time.time()

    async with async_playwright() as p:
        print("Iniciando Chromium headless…")
        browser = await p.chromium.launch(headless=True)
        sem     = asyncio.Semaphore(MAX_TABS)

        for league in LEAGUES:
            print(f"\n── {LEAGUES[league]} ──────────────────────────────────")
            await asyncio.gather(
                *[download_season(browser, sem, league, s) for s in SEASONS]
            )
            done += len(SEASONS)
            elapsed = time.time() - t_start
            if done < total:
                eta = elapsed / done * (total - done)
                print(f"  ETA: ~{int(eta // 60)} min restantes")

        await browser.close()

    mins = int((time.time() - t_start) // 60)
    print(f"\n✓ Listo en {mins} min  —  datos en: {DATA_DIR}")
    print("  Siguiente: python3 scripts/build_league_trends.py")


if __name__ == "__main__":
    asyncio.run(main())
