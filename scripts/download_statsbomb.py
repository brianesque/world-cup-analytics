#!/usr/bin/env python3
"""
Descarga shot data de La Liga 2004/05–2013/14 desde StatsBomb Open Data (GitHub).
Transforma al mismo schema que download_understat.py para que build_league_trends.py
lo pueda leer sin cambios.

Nota de modelo xG:
  - Understat usa red neuronal propia con coords + situación + tipo de remate.
  - StatsBomb usa modelo con freeze-frame (posición de defensores y arquero).
  - Para temporadas históricas (pre-2014) el freeze-frame puede no estar disponible,
    por lo que statsbomb_xg retroactivo es similar en insumos a Understat.
  - En el dashboard usamos xG como referencia secundaria; métricas primarias son
    distancia, % en área y centralidad (geométricas, comparables entre fuentes).

Uso:
    python3 scripts/download_statsbomb.py

Lee:  StatsBomb Open Data GitHub (raw JSON)
Escribe:  data/statsbomb/shots/La_liga_{year}.json
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── PATHS ──────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
OUT_DIR     = REPO_ROOT / "data" / "statsbomb" / "shots"
SB_BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

# ── LA LIGA SEASONS A DESCARGAR ───────────────────────────────────────────────
# competition_id=11 (La Liga).  Solo bajamos 2004-2013; Understat cubre 2014+.
# season_id → year_start
LA_LIGA_COMP_ID = 11
SEASONS = {
    37: "2004",   # 2004/2005
    38: "2005",   # 2005/2006
    39: "2006",   # 2006/2007
    40: "2007",   # 2007/2008
    41: "2008",   # 2008/2009
    21: "2009",   # 2009/2010
    22: "2010",   # 2010/2011
    23: "2011",   # 2011/2012
    24: "2012",   # 2012/2013
    25: "2013",   # 2013/2014
}

# ── NORMALIZACIÓN DE NOMBRES DE EQUIPO ────────────────────────────────────────
# StatsBomb usa algunos nombres distintos a Understat.
# Equipos que aparecen en AMBOS períodos necesitan nombre consistente.
TEAM_NAME_MAP = {
    "Atlético de Madrid":        "Atletico Madrid",
    "Atlético Madrid":           "Atletico Madrid",
    "Deportivo de La Coruña":    "Deportivo La Coruna",
    "Deportivo La Coruña":       "Deportivo La Coruna",
    "Málaga":                    "Malaga",
    "Cádiz":                     "Cadiz",
    "Almería":                   "Almeria",
    "Leganés":                   "Leganes",
    "Getafe":                    "Getafe",          # mismo
    "Levante":                   "Levante",          # mismo
    "Real Betis Balompié":       "Real Betis",
    "Rayo Vallecano de Madrid":  "Rayo Vallecano",
    "Athletic Club":             "Athletic Club",    # mismo
    "Girona":                    "Girona",           # mismo
    "Alavés":                    "Alaves",
    "Espanyol":                  "Espanyol",         # mismo
    "Numancia":                  "Numancia",
    "Recreativo":                "Recreativo",
    "Xerez":                     "Xerez",
    "Racing de Santander":       "Racing Santander",
    "Osasuna":                   "Osasuna",          # mismo
    "Real Valladolid":           "Valladolid",
    "Real Sociedad":             "Real Sociedad",    # mismo
    "Real Zaragoza":             "Zaragoza",
    "UD Almería":                "Almeria",
    "UD Las Palmas":             "Las Palmas",
    "SD Eibar":                  "Eibar",
    "Elche":                     "Elche",
    "Córdoba":                   "Cordoba",
    "Cultural Leonesa":          "Cultural Leonesa",
}

def normalize_team(name: str) -> str:
    return TEAM_NAME_MAP.get(name, name)

# ── HTTP HELPER ───────────────────────────────────────────────────────────────
def fetch_json(url: str, retries: int = 3) -> list | dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "world-cup-analytics/1.0 (academic)"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"Failed after {retries} retries: {url}")

# ── TRANSFORMACIÓN DE SHOT EVENT ──────────────────────────────────────────────
# StatsBomb pitch: 120×80, gol en [120, 40].
# Understat: X=0..1 (0=arco propio, 1=arco rival), Y=0..1 (0.5=centro).
# La transformación X=x/120, Y=y/80 es equivalente a nuestra fórmula de distancia.

OUTCOME_MAP = {
    "Goal":    "Goal",
    "Saved":   "SavedShot",
    "Blocked": "BlockedShot",
    "Off T":   "MissedShots",
    "Wayward": "MissedShots",
    "Post":    "MissedShots",
    "Saved Off T": "MissedShots",
    "No Touch":    "MissedShots",
}

SITUATION_MAP = {
    "Open Play": "OpenPlay",
    "Free Kick": "DirectFreekick",
    "Corner":    "FromCorner",
    "Penalty":   "Penalty",
    "Kick Off":  "OpenPlay",
}

BODY_PART_MAP = {
    "Right Foot": "RightFoot",
    "Left Foot":  "LeftFoot",
    "Head":       "Head",
    "No Touch":   "Head",
    "Chest":      "Head",
}

def transform_shot(event: dict, home_team: str, away_team: str) -> dict | None:
    """Convierte un evento de tipo Shot de StatsBomb al schema de Understat."""
    loc = event.get("location")
    if not loc or len(loc) < 2:
        return None

    x_sb, y_sb = float(loc[0]), float(loc[1])

    # StatsBomb normaliza coords para que el ataque vaya hacia x=120.
    X = x_sb / 120.0
    Y = y_sb / 80.0

    shot      = event.get("shot", {})
    outcome   = shot.get("outcome", {}).get("name", "")
    result    = OUTCOME_MAP.get(outcome, "MissedShots")
    situation = SITUATION_MAP.get(shot.get("type", {}).get("name", "Open Play"), "OpenPlay")
    shot_type = BODY_PART_MAP.get(shot.get("body_part", {}).get("name", ""), "RightFoot")

    team_name = normalize_team(event.get("team", {}).get("name", ""))
    ht        = normalize_team(home_team)
    at        = normalize_team(away_team)
    h_a       = "h" if team_name == ht else "a"

    xg = shot.get("statsbomb_xg") or 0.0

    return {
        "X":         round(X, 6),
        "Y":         round(Y, 6),
        "xG":        round(float(xg), 6),
        "result":    result,
        "situation": situation,
        "shotType":  shot_type,
        "h_a":       h_a,
        "h_team":    ht,
        "a_team":    at,
        "player":    event.get("player", {}).get("name", ""),
        "_src":      "statsbomb",   # tag interno, ignorado por aggregate()
    }

# ── MAIN ───────────────────────────────────────────────────────────────────────
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total_shots = 0
    total_matches = 0

    for season_id, year in SEASONS.items():
        out_file = OUT_DIR / f"La_liga_{year}.json"

        matches_url = f"{SB_BASE_URL}/matches/{LA_LIGA_COMP_ID}/{season_id}.json"
        print(f"\n── La Liga {year}/{int(year)+1} (season_id={season_id}) {'─'*30}")
        print(f"  Descargando matches… {matches_url}")

        try:
            matches = fetch_json(matches_url)
        except Exception as e:
            print(f"  ✗ Error obteniendo matches: {e}")
            continue

        season_shots: list[dict] = []
        n_matches = len(matches)

        for i, match in enumerate(matches, 1):
            match_id   = match["match_id"]
            home_team  = match["home_team"]["home_team_name"]
            away_team  = match["away_team"]["away_team_name"]
            score_home = match.get("home_score", "?")
            score_away = match.get("away_score", "?")

            events_url = f"{SB_BASE_URL}/events/{match_id}.json"
            try:
                events = fetch_json(events_url)
            except urllib.error.HTTPError as e:
                print(f"  [{i}/{n_matches}] ✗ HTTP {e.code} — match {match_id}")
                continue
            except Exception as e:
                print(f"  [{i}/{n_matches}] ✗ {e} — match {match_id}")
                continue

            match_shots = [
                s for e in events
                if e.get("type", {}).get("name") == "Shot"
                for s in [transform_shot(e, home_team, away_team)]
                if s is not None
            ]

            season_shots.extend(match_shots)
            print(
                f"  [{i:>2}/{n_matches}] {home_team} {score_home}-{score_away} {away_team}"
                f"  → {len(match_shots)} tiros"
            )

            # Pausa cortés entre requests (evitar rate-limit del CDN de GitHub)
            time.sleep(0.15)

        out_file.write_text(json.dumps(season_shots, ensure_ascii=False))
        print(f"\n  ✓ {len(season_shots):,} tiros en {n_matches} partidos → {out_file.name}")
        total_shots   += len(season_shots)
        total_matches += n_matches

    print(f"\n{'═'*60}")
    print(f"✓ Descarga completa: {total_shots:,} tiros en {total_matches} partidos")
    print(f"  Archivos: {OUT_DIR}")

if __name__ == "__main__":
    main()
