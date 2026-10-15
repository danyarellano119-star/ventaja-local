"""Cuotas del mercado, para saber si el modelo aporta algo o no.

Acertar más que el azar está bien, pero no es la prueba que importa. La prueba
es el mercado: las casas de apuestas incorporan lesiones, alineaciones,
rumores y el dinero de miles de personas. Si nuestras probabilidades no son al
menos tan buenas como las suyas, el modelo no da ninguna ventaja por bonito
que sea.

Esto guarda la cuota de cada partido **antes de jugarse**, junto al pronóstico,
para poder comparar después sobre los mismos partidos.

Necesita una clave de the-odds-api.com, que en su plan gratuito da 500
peticiones al mes; con una consulta diaria por competición sobra. Se lee de la
variable de entorno ``ODDS_API_KEY``. **Sin clave este módulo no hace nada** y
el resto de la web funciona igual que siempre.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "datos" / "cuotas.json"
BASE = "https://api.the-odds-api.com/v4"
HORAS = 8          # cada cuánto se vuelve a preguntar

# Nuestra clave de competición -> cómo la llama the-odds-api
DEPORTES = {
    "premier": "soccer_epl",
    "laliga": "soccer_spain_la_liga",
    "seriea": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue1": "soccer_france_ligue_one",
    "eredivisie": "soccer_netherlands_eredivisie",
    "primeira": "soccer_portugal_primeira_liga",
    "brasileirao": "soccer_brazil_campeonato",
    "championship": "soccer_efl_champ",
    "champions": "soccer_uefa_champs_league",
}


def _clave() -> str:
    return os.environ.get("ODDS_API_KEY", "").strip()


def disponible() -> bool:
    return bool(_clave())


def _sin_margen(cl: float, ce: float, cv: float) -> tuple[float, float, float] | None:
    """Pasa de cuotas a probabilidades quitando el margen de la casa.

    Una cuota de 2,00 no es un 50 %: la casa se queda un margen y las tres
    probabilidades implícitas suman más de 1. Se reparte proporcionalmente,
    que es lo más simple y lo que hace todo el mundo.
    """
    if min(cl, ce, cv) <= 1.0:
        return None
    inv = (1 / cl, 1 / ce, 1 / cv)
    total = sum(inv)
    if not (1.0 < total < 1.30):      # fuera de eso, la cuota es sospechosa
        return None
    return tuple(round(x / total, 4) for x in inv)


def _mejor_cuota(partido: dict) -> tuple[float, float, float] | None:
    """La mejor cuota de cada resultado entre todas las casas.

    Se toma la mejor y no la media porque es la que un apostante podría
    conseguir de verdad, y porque así el margen que se descuenta es el más
    pequeño: el mercado queda retratado en su versión más exigente.
    """
    local = partido.get("home_team")
    visita = partido.get("away_team")
    mejor = {"L": 0.0, "E": 0.0, "V": 0.0}
    for casa in partido.get("bookmakers", []):
        for mercado in casa.get("markets", []):
            if mercado.get("key") != "h2h":
                continue
            for r in mercado.get("outcomes", []):
                nombre, precio = r.get("name"), r.get("price")
                if not precio:
                    continue
                lado = ("L" if nombre == local else
                        "V" if nombre == visita else
                        "E" if nombre == "Draw" else None)
                if lado and precio > mejor[lado]:
                    mejor[lado] = precio
    if min(mejor.values()) <= 1.0:
        return None
    return _sin_margen(mejor["L"], mejor["E"], mejor["V"])


def recoger(claves_liga: list[str] | None = None) -> dict:
    """Devuelve {clave_liga: [{fecha, local, visita, pl, pe, pv}]}.

    Lo recogido se acumula en disco: una cuota de un partido ya jugado no se
    puede volver a pedir, así que si no se guarda se pierde para siempre.
    """
    try:
        guardado = json.loads(CACHE.read_text(encoding="utf-8")) \
            if CACHE.exists() else {}
    except Exception:
        guardado = {}

    if not disponible():
        return guardado

    fresca = (CACHE.exists()
              and time.time() - CACHE.stat().st_mtime < HORAS * 3600)
    if fresca and guardado:
        return guardado

    pedir = [k for k in (claves_liga or DEPORTES) if k in DEPORTES]
    nuevas = 0
    for clave in pedir:
        try:
            r = requests.get(
                f"{BASE}/sports/{DEPORTES[clave]}/odds/",
                params={"regions": "eu,uk", "markets": "h2h",
                        "oddsFormat": "decimal", "apiKey": _clave()},
                timeout=45)
            if r.status_code != 200:
                continue
            por_id = {f"{x['fecha']}|{x['local']}|{x['visita']}": x
                      for x in guardado.get(clave, [])}
            for p in r.json():
                prob = _mejor_cuota(p)
                if not prob:
                    continue
                fila = {"fecha": (p.get("commence_time") or "")[:10],
                        "local": p.get("home_team"), "visita": p.get("away_team"),
                        "pl": prob[0], "pe": prob[1], "pv": prob[2]}
                llave = f"{fila['fecha']}|{fila['local']}|{fila['visita']}"
                if llave not in por_id:
                    nuevas += 1
                # La cuota más reciente antes del partido es la que vale
                por_id[llave] = fila
            guardado[clave] = list(por_id.values())
        except Exception:
            continue
        time.sleep(1.0)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(guardado, ensure_ascii=False, indent=0,
                                sort_keys=True), encoding="utf-8")
    total = sum(len(v) for v in guardado.values())
    print(f"    {total} partidos con cuota guardada ({nuevas} nuevos)")
    return guardado


def buscar(cuotas: dict, clave_liga: str, fecha: str,
           local: str, visita: str, norm) -> dict | None:
    """La cuota de un partido concreto, casando nombres con tolerancia."""
    for fila in cuotas.get(clave_liga, []):
        if fila["fecha"] != fecha:
            continue
        if norm(fila["local"]) == norm(local) and norm(fila["visita"]) == norm(visita):
            return fila
    return None
