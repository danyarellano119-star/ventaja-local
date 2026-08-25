"""Calendarios de football-data.org, para las competiciones que openfootball no cubre.

openfootball es la fuente principal y seguirá siéndolo: es abierta y no pide
credenciales. Pero publica las temporadas nuevas a su ritmo, y mientras tanto
hay competiciones con historial de sobra y ningún partido futuro que enseñar.

Esta fuente las completa. Necesita una clave, que se lee de la variable de
entorno ``FOOTBALL_DATA_KEY``; en GitHub llega desde un secreto del repositorio
y nunca aparece en el código ni en los registros. Sin clave, el módulo se calla
y devuelve listas vacías: la web sigue funcionando igual que hasta ahora.

Lo que el plan gratuito da: doce competiciones. Ocho ya las cubre openfootball,
así que aquí sólo se piden las tres que aporta de verdad —la Championship
inglesa, la Champions y la Libertadores—, y así se gastan menos peticiones del
límite de diez por minuto.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "datos" / "football_data.json"
BASE = "https://api.football-data.org/v4"

# Cuánto vale un calendario guardado. Las fechas se mueven poco de un día para
# otro y el límite de peticiones es estrecho, así que no hace falta más.
HORAS = 12
PAUSA = 7        # segundos entre peticiones; el plan libre da 10 por minuto

# clave interna -> (código en la API, nombre, país, continente, mes de inicio)
COMPETICIONES = {
    "championship": ("ELC", "Championship", "Inglaterra", "Europa", 8),
    "champions":    ("CL",  "Champions League", "Europa", "Europa", 8),
    "libertadores": ("CLI", "Copa Libertadores", "Sudamérica", "América", 1),
}


def _clave() -> str:
    return os.environ.get("FOOTBALL_DATA_KEY", "").strip()


def disponible() -> bool:
    return bool(_clave())


def _cargar() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _pedir(ruta: str) -> dict | None:
    """Una petición a la API. None si falla, para que quien llame lo decida."""
    try:
        r = requests.get(f"{BASE}/{ruta}", timeout=60,
                         headers={"X-Auth-Token": _clave()})
        if r.status_code == 429:
            time.sleep(65)          # se pasó el límite: esperar el minuto
            r = requests.get(f"{BASE}/{ruta}", timeout=60,
                             headers={"X-Auth-Token": _clave()})
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


CACHE_HIST = CACHE.with_name("football_data_historial.json")
DIAS_HIST = 3     # los resultados viejos no cambian; basta refrescar cada tanto


def historial(clave: str, temporadas: int = 3) -> list[dict]:
    """Partidos ya jugados, con la forma que usa el resto del programa.

    Se entregan igual que los de Understat, con los goles ocupando el lugar del
    xG: estas competiciones no tienen ocasiones de gol publicadas, y el modelo
    sabe ajustarse sobre goles.
    """
    if not disponible() or clave not in COMPETICIONES:
        return []

    try:
        guardado = json.loads(CACHE_HIST.read_text(encoding="utf-8"))             if CACHE_HIST.exists() else {}
    except Exception:
        guardado = {}

    fresca = (CACHE_HIST.exists()
              and time.time() - CACHE_HIST.stat().st_mtime < DIAS_HIST * 86400)
    if fresca and guardado.get(clave):
        return guardado[clave]

    cod = COMPETICIONES[clave][0]
    hoy = date.today()
    # La temporada se nombra por el año en que empieza
    inicio = hoy.year if hoy.month >= COMPETICIONES[clave][4] else hoy.year - 1

    partidos = []
    for atras in range(temporadas):
        datos = _pedir(f"competitions/{cod}/matches"
                       f"?status=FINISHED&season={inicio - atras}")
        time.sleep(PAUSA)
        if not datos:
            continue
        for m in datos.get("matches", []):
            fin = ((m.get("score") or {}).get("fullTime") or {})
            gl, gv = fin.get("home"), fin.get("away")
            local = (m.get("homeTeam") or {}).get("name")
            visita = (m.get("awayTeam") or {}).get("name")
            if gl is None or gv is None or not local or not visita:
                continue
            partidos.append({
                "datetime": (m.get("utcDate") or "")[:10] + " 00:00:00",
                "h": {"title": local}, "a": {"title": visita},
                "goals": {"h": int(gl), "a": int(gv)},
                "xG": {"h": float(gl), "a": float(gv)},
                "isResult": True,
            })

    if partidos:
        guardado[clave] = partidos
        CACHE_HIST.parent.mkdir(parents=True, exist_ok=True)
        CACHE_HIST.write_text(json.dumps(guardado, ensure_ascii=False),
                              encoding="utf-8")
    return partidos or guardado.get(clave, [])


def calendarios(hoy: date | None = None) -> dict:
    """Devuelve {clave: {info, partidos}} con lo que la API tenga programado.

    Los partidos salen con la misma forma que usa el resto del programa: fecha,
    hora y los dos equipos por su nombre. La hora viene en UTC, que es
    justamente lo que la web necesita para traducirla a la zona de cada uno.
    """
    if not disponible():
        return {}

    hoy = hoy or date.today()
    cache = _cargar()
    fresca = (CACHE.exists()
              and time.time() - CACHE.stat().st_mtime < HORAS * 3600)
    if fresca and cache:
        return {k: v for k, v in cache.items() if v.get("partidos")}

    fuera: dict[str, dict] = {}
    for clave, (cod, nombre, pais, continente, mes) in COMPETICIONES.items():
        datos = _pedir(f"competitions/{cod}/matches?status=SCHEDULED")
        time.sleep(PAUSA)
        if not datos:
            # Sin respuesta se conserva lo que hubiera: mejor un calendario de
            # ayer que ninguno.
            if cache.get(clave):
                fuera[clave] = cache[clave]
            continue

        partidos = []
        for m in datos.get("matches", []):
            local = (m.get("homeTeam") or {}).get("name")
            visita = (m.get("awayTeam") or {}).get("name")
            utc = m.get("utcDate") or ""
            if not local or not visita or not utc:
                continue          # eliminatorias sin sorteo: aún sin equipos
            if utc[:10] < hoy.isoformat():
                continue
            partidos.append({
                "fecha": utc[:10], "hora": utc[11:16],
                "utc": utc[:16] + "Z",
                "local": local, "visita": visita,
            })

        partidos.sort(key=lambda p: (p["fecha"], p["hora"]))
        fuera[clave] = {"nombre": nombre, "pais": pais,
                        "continente": continente, "mes": mes,
                        "partidos": partidos}

    if fuera:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(fuera, ensure_ascii=False, indent=0,
                                    sort_keys=True), encoding="utf-8")
    return {k: v for k, v in fuera.items() if v.get("partidos")}
