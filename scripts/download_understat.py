#!/usr/bin/env python3
"""
Download shot and player data from Understat using Playwright (headless browser).
Understat is a JavaScript-rendered SPA — plain HTTP requests only get the empty shell.
Playwright runs a real Chromium instance that executes the JS and exposes the data.

Setup (una sola vez):
    pip3 install playwright
    python3 -m playwright install chromium

Usage:
    python3 scripts/download_understat.py

Output:
    data/understat/shots/{league}_{season}.json      ← todos los tiros de la temporada
    data/understat/players/{league}_{season}.json    ← stats de jugadores
    data/understat/_ckpt/{league}_{season}/          ← checkpoints por equipo

Estrategia: por cada liga/temporada carga la página de la liga (lista de equipos +
player stats), luego cada página de equipo para obtener sus disparos.
Total de cargas: ~1.400 páginas · ~3 tabs paralelas · ~5s/página ≈ 40-60 min.
Resume-safe: archivos ya descargados se saltean automáticamente.

Coordenadas Understat (normalizadas):
    X = 0.0 (línea de arco propio) → 1.0 (línea de arco rival)
    Y = 0.0 (banda izquierda)      → 0.5 (centro)              → 1.0 (banda derecha)
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
SEASONS    = [str(y) for y in range(2014, 2025)]  # 2014 = temporada 2014/15
MAX_TABS   = 3      # tabs paralelas (más no necesariamente es más rápido)
PAGE_DELAY = 1.5    # segundos de espera post-carga para que el JS termine
BASE_URL   = "https://understat.com"
DATA_DIR   = Path(__file__).parent.parent / "data" / "understat"


# ── HELPERS ────────────────────────────────────────────────────────────────────

async def js_var(page, name: str):
    """Lee una variable global de JavaScript de la página cargada. Retorna None si no existe."""
    try:
        return await page.evaluate(f"() => (typeof {name} !== 'undefined') ? {name} : null")
    except Exception:
        return None


async def goto(page, url: str, wait_var: str | None = None, timeout: int = 45_000):
    """Navega a la URL y opcionalmente espera que una variable JS esté definida."""
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    if wait_var:
        try:
            await page.wait_for_function(
                f"typeof {wait_var} !== 'undefined'",
                timeout=timeout,
            )
        except Exception:
            pass  # continuamos igual; si la variable falta se detecta después
    await asyncio.sleep(PAGE_DELAY)


# ── POR TEMPORADA ─────────────────────────────────────────────────────────────

async def download_season(browser, sem: asyncio.Semaphore, league: str, season: str) -> None:
    shots_dir   = DATA_DIR / "shots"
    players_dir = DATA_DIR / "players"
    ckpt_dir    = DATA_DIR / "_ckpt" / f"{league}_{season}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    label       = f"{LEAGUES[league]} {season}/{int(season)+1}"
    out_shots   = shots_dir / f"{league}_{season}.json"
    out_players = players_dir / f"{league}_{season}.json"

    if out_shots.exists() and out_players.exists():
        n = len(json.loads(out_shots.read_text()))
        print(f"  ↩ {label}: {n} shots (ya descargado)")
        return

    async with sem:
        page = await browser.new_page()
        try:
            # ── 1. Página de liga → player stats + lista de equipos ────────
            print(f"  ↓ {label}: cargando página de liga…", flush=True)
            await goto(page, f"{BASE_URL}/league/{league}/{season}", wait_var="matchesData")

            players_data = await js_var(page, "playersData")
            matches_data = await js_var(page, "matchesData")

            if players_data and not out_players.exists():
                out_players.write_text(json.dumps(players_data, ensure_ascii=False))
                print(f"    ✓ {len(players_data)} jugadores guardados")
            elif not players_data:
                print(f"    ✗ no se encontró playersData")

            if not matches_data:
                print(f"    ✗ no se encontró matchesData — salteo {label}")
                return

            # Extraer nombres únicos de equipos desde los partidos
            match_list = list(matches_data.values()) if isinstance(matches_data, dict) else matches_data
            teams: set[str] = set()
            for m in match_list:
                for side in ("h", "a"):
                    t = m.get(side, {})
                    if isinstance(t, dict) and t.get("title"):
                        teams.add(t["title"])

            if not teams:
                print(f"    ✗ no se encontraron equipos en matchesData")
                return

            print(f"    → {len(teams)} equipos")

            # ── 2. Página de cada equipo → sus disparos ────────────────────
            all_shots: list[dict] = []

            for team in sorted(teams):
                team_slug  = team.replace(" ", "_")
                ckpt_file  = ckpt_dir / f"{team_slug}.json"

                if ckpt_file.exists():
                    cached = json.loads(ckpt_file.read_text())
                    all_shots.extend(cached)
                    continue

                team_url = f"{BASE_URL}/team/{team_slug}/{season}"
                await goto(page, team_url, wait_var="shotsData")
                shots_raw = await js_var(page, "shotsData")

                team_shots: list[dict] = []
                if shots_raw and isinstance(shots_raw, dict):
                    # shotsData en página de equipo: {"h": [...shots partidos local...],
                    #                                  "a": [...shots partidos visitante...]}
                    # Cada elemento ya tiene todos los campos del disparo.
                    for side, shots_list in shots_raw.items():
                        for s in (shots_list or []):
                            s["league"]   = league
                            s["season"]   = season
                            s["team"]     = team
                            team_shots.append(s)

                ckpt_file.write_text(json.dumps(team_shots, ensure_ascii=False))
                all_shots.extend(team_shots)
                print(f"    ✓ {team}: {len(team_shots)} disparos", flush=True)

            # Deduplicar por ID de disparo (cada tiro debe aparecer una sola vez)
            seen:   set[str]  = set()
            unique: list[dict] = []
            for s in all_shots:
                sid = str(s.get("id", ""))
                if sid and sid in seen:
                    continue
                if sid:
                    seen.add(sid)
                unique.append(s)

            out_shots.write_text(json.dumps(unique, ensure_ascii=False))
            print(f"  ✓ {label}: {len(unique)} disparos totales guardados")

        except Exception as e:
            print(f"  ✗ Error en {label}: {e}")
        finally:
            await page.close()


# ── MAIN ───────────────────────────────────────────────────────────────────────

async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright no instalado. Corré:")
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
            # Procesar temporadas de la liga con paralelismo limitado por sem
            await asyncio.gather(
                *[download_season(browser, sem, league, s) for s in SEASONS]
            )
            done += len(SEASONS)
            elapsed = time.time() - t_start
            if done < total and done > 0:
                eta_sec = elapsed / done * (total - done)
                print(f"  ETA: ~{int(eta_sec // 60)} min restantes")

        await browser.close()

    mins = int((time.time() - t_start) // 60)
    print(f"\n✓ Listo en {mins} min. Datos en: {DATA_DIR}")
    print("  Siguiente paso:")
    print("    python3 scripts/build_league_trends.py")


if __name__ == "__main__":
    asyncio.run(main())
