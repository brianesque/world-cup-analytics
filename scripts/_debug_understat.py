#!/usr/bin/env python3
"""Debug v3: inspecciona datesData y una página de partido."""
from __future__ import annotations
import asyncio, json, datetime

def jdump(obj, **kw):
    """json.dumps que convierte datetime a string."""
    def default(o):
        if isinstance(o, (datetime.datetime, datetime.date)):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, default=default, **kw)

async def debug():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        print("Cargando EPL 2014…")
        await page.goto("https://understat.com/league/EPL/2014",
                        wait_until="networkidle", timeout=60_000)

        # Estructura de teamsData
        teams = await page.evaluate("() => teamsData")
        print("\n── teamsData (primeras 2 entradas) ──")
        items = list(teams.items())[:2] if isinstance(teams, dict) else []
        for k, v in items:
            print(f"  [{k}]: {json.dumps(v, indent=4)[:300]}")

        # Estructura de datesData
        dates = await page.evaluate("() => datesData")
        # También mostrar un entry de history de teamsData para ver si tiene match_id
        print("\n── teamsData[71].history[0] ──")
        if teams and "71" in teams and teams["71"].get("history"):
            print(jdump(teams["71"]["history"][0], indent=2))

        print("\n── datesData[0] ──")
        if dates and len(dates) > 0:
            print(jdump(dates[0], indent=2))
        print(f"\n── datesData[1] ──")
        if dates and len(dates) > 1:
            print(jdump(dates[1], indent=2))

        # Extraer un match_id de datesData para probar la página de partido
        match_id = None
        if dates:
            for entry in dates:
                if isinstance(entry, dict):
                    mid = entry.get("id") or entry.get("match_id")
                    if mid:
                        match_id = mid
                        break
                    # A veces está anidado en h/a
                    for key in entry:
                        if isinstance(entry[key], dict) and entry[key].get("id"):
                            match_id = entry[key]["id"]
                            break
                if match_id:
                    break

        print(f"\n── Match ID encontrado: {match_id} ──")

        if match_id:
            print(f"Cargando /match/{match_id}…")
            await page.goto(f"https://understat.com/match/{match_id}",
                            wait_until="networkidle", timeout=60_000)
            match_vars = await page.evaluate("""() => {
                const result = {};
                for (const key of Object.keys(window)) {
                    if (key.endsWith('Data') || key.includes('shots') || key.includes('Shot')) {
                        const val = window[key];
                        const t = Array.isArray(val) ? 'Array('+val.length+')'
                                : (val && typeof val==='object') ? 'Object(keys:'+Object.keys(val).slice(0,6).join(',')+')'
                                : typeof val;
                        result[key] = t;
                    }
                }
                return result;
            }""")
            print("\nVariables en página de partido:")
            for k, v in match_vars.items():
                print(f"  {k}: {v}")

            shots = await page.evaluate("""() => {
                if (typeof shotsData !== 'undefined') return shotsData;
                return null;
            }""")
            if shots:
                all_shots = []
                if isinstance(shots, dict):
                    for side in shots.values():
                        if isinstance(side, list):
                            all_shots.extend(side)
                elif isinstance(shots, list):
                    all_shots = shots
                print(f"\nshotsData: {len(all_shots)} disparos totales")
                if all_shots:
                    print("Muestra disparo[0]:")
                    print(jdump(all_shots[0], indent=2))
            else:
                print("\nshotsData NO encontrado en página de partido")

        await browser.close()

asyncio.run(debug())
