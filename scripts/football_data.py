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

# clave interna -> (código, nombre, país, continente, mes de inicio, es liga)
#
# La última casilla distingue una liga de una copa. La Libertadores y la
# Champions son torneos por eliminatorias: no tienen clasificación general ni
# descensos, así que la web no debe enseñarles nada de eso.
COMPETICIONES = {
    "championship": ("ELC", "Championship", "Inglaterra", "Europa", 8, True),
    "champions":    ("CL",  "Champions League", "Europa", "Europa", 8, False),
    "libertadores": ("CLI", "Copa Libertadores", "Sudamérica", "América", 1, False),
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
            _apuntar_escudo(m.get("homeTeam") or {})
            _apuntar_escudo(m.get("awayTeam") or {})
            _apuntar_id(m.get("homeTeam") or {})
            _apuntar_id(m.get("awayTeam") or {})
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
    for clave, (cod, nombre, pais, continente, mes, es_liga) in COMPETICIONES.items():
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
            _apuntar_escudo(m.get("homeTeam") or {})
            _apuntar_escudo(m.get("awayTeam") or {})
            _apuntar_id(m.get("homeTeam") or {})
            _apuntar_id(m.get("awayTeam") or {})
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
                        "es_liga": es_liga, "partidos": partidos}

    if fuera:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(fuera, ensure_ascii=False, indent=0,
                                    sort_keys=True), encoding="utf-8")
    return {k: v for k, v in fuera.items() if v.get("partidos")}


CACHE_EQ = CACHE.with_name("football_data_equipos.json")

# Sufijos de sociedad que sobran al mostrar el nombre. La fuente escribe
# «Coventry City FC» y «Hull City AFC»; en la web queda mejor sin ellos, y da
# igual para el emparejado interno porque ahí ya se descartan estas palabras.
_SOBRA = (" FC", " AFC", " CF", " SC", " F.C.", " A.F.C.")

# Escudos recogidos al vuelo. Cada partido que baja esta fuente ya trae el
# escudo de los dos equipos, así que no hace falta pedirlos aparte: se van
# apuntando según se leen los calendarios y los resultados.
_ESCUDOS: dict[str, str] = {}


def nombre_corto(nombre: str) -> str:
    for s in _SOBRA:
        if nombre.endswith(s) and len(nombre) - len(s) >= 4:
            return nombre[: -len(s)]
    return nombre


def _apuntar_escudo(equipo: dict) -> None:
    nombre, escudo = equipo.get("name"), equipo.get("crest")
    if nombre and escudo:
        _ESCUDOS[nombre_corto(nombre)] = escudo


def escudos() -> dict[str, str]:
    """Escudo de cada equipo de estas competiciones, por su nombre ya limpio.

    Es la única fuente que cubre la Championship y los clubes sudamericanos: el
    repositorio de logos que usamos para las grandes ligas no los tiene, y por
    Wikidata había que acertar el nombre oficial de cada uno.

    Lo recogido en esta ejecución se suma a lo guardado y se conserva, para que
    un equipo no pierda su escudo el día que no aparezca en ningún partido.
    """
    try:
        guardado = json.loads(CACHE_EQ.read_text(encoding="utf-8"))             if CACHE_EQ.exists() else {}
    except Exception:
        guardado = {}

    guardado.update(_ESCUDOS)
    if guardado:
        CACHE_EQ.parent.mkdir(parents=True, exist_ok=True)
        CACHE_EQ.write_text(json.dumps(guardado, ensure_ascii=False, indent=0,
                                       sort_keys=True), encoding="utf-8")
    return guardado


# --------------------------------------------------------------------------- #
# Jugadores
# --------------------------------------------------------------------------- #

CACHE_JUG = CACHE.with_name("football_data_jugadores.json")
DIAS_JUG = 2       # los goleadores cambian cada jornada
CACHE_PLANTILLAS = CACHE.with_name("football_data_plantillas.json")
DIAS_PLANTILLA = 25   # quién está en el equipo, casi no
TOPE_GOLEADORES = 300

# Identificador de cada equipo, apuntado al leer los partidos. Hace falta para
# pedir su plantilla, que va por otro camino de la API. Se guarda en disco por
# lo mismo que los escudos: en una ejecución con la caché de partidos caliente
# no se lee ninguno, y sin ellos no habría plantillas.
CACHE_IDS = CACHE.with_name("football_data_ids.json")
_IDS: dict[str, int] = {}


def _ids() -> dict[str, int]:
    try:
        guardado = json.loads(CACHE_IDS.read_text(encoding="utf-8"))             if CACHE_IDS.exists() else {}
    except Exception:
        guardado = {}
    guardado.update(_IDS)
    if guardado:
        CACHE_IDS.parent.mkdir(parents=True, exist_ok=True)
        CACHE_IDS.write_text(json.dumps(guardado, ensure_ascii=False, indent=0,
                                        sort_keys=True), encoding="utf-8")
    return guardado

# Cómo llama la fuente a cada demarcación y cómo la llama el resto del programa
_PUESTOS = {
    "Goalkeeper": "POR", "Defence": "DEF", "Midfield": "MED", "Offence": "DEL",
    "Centre-Back": "DEF", "Left-Back": "DEF", "Right-Back": "DEF",
    "Defensive Midfield": "MED", "Central Midfield": "MED",
    "Attacking Midfield": "MED", "Left Midfield": "MED", "Right Midfield": "MED",
    "Left Winger": "DEL", "Right Winger": "DEL", "Centre-Forward": "DEL",
}


def _apuntar_id(equipo: dict) -> None:
    nombre, ident = equipo.get("name"), equipo.get("id")
    if nombre and ident:
        _IDS[nombre_corto(nombre)] = ident


def _edad(nacimiento: str | None, hoy: date) -> int | None:
    if not nacimiento or len(nacimiento) < 10:
        return None
    try:
        n = date.fromisoformat(nacimiento[:10])
    except ValueError:
        return None
    return hoy.year - n.year - ((hoy.month, hoy.day) < (n.month, n.day))


def jugadores(clave: str, temporada: int, hoy: date | None = None) -> dict[str, list]:
    """Plantilla de cada equipo con lo que se sepa de cada jugador.

    Aquí no hay ocasiones de gol: esta fuente no las publica y no las publica
    nadie gratis para estas competiciones. Lo que sí hay son goles,
    asistencias, penaltis y partidos de quien haya marcado, más la plantilla
    entera con puesto, edad y nacionalidad. Es menos de lo que tienen las cinco
    grandes ligas, y la web lo dice en vez de fingir lo contrario.
    """
    if not disponible() or clave not in COMPETICIONES:
        return {}

    hoy = hoy or date.today()
    try:
        guardado = json.loads(CACHE_JUG.read_text(encoding="utf-8")) \
            if CACHE_JUG.exists() else {}
    except Exception:
        guardado = {}

    fresca = (CACHE_JUG.exists()
              and time.time() - CACHE_JUG.stat().st_mtime < DIAS_JUG * 86400)
    if fresca and guardado.get(clave):
        return guardado[clave]

    cod = COMPETICIONES[clave][0]

    # 1) Quién ha marcado, con sus números
    marcas: dict[str, dict] = {}
    datos = _pedir(f"competitions/{cod}/scorers"
                   f"?limit={TOPE_GOLEADORES}&season={temporada}")
    time.sleep(PAUSA)
    for s in (datos or {}).get("scorers", []):
        p = s.get("player") or {}
        eq = nombre_corto((s.get("team") or {}).get("name") or "")
        if not p.get("name") or not eq:
            continue
        marcas[f"{eq}|{p['name']}"] = {
            "g": s.get("goals") or 0, "a": s.get("assists") or 0,
            "pen": s.get("penalties") or 0, "pj": s.get("playedMatches") or 0,
        }

    # 2) La plantilla de cada equipo, para que estén también los que no marcan.
    #    Los identificadores se apuntan al leer los partidos, pero si esa caché
    #    está fresca no se lee ninguno; entonces se piden aparte, que es una
    #    sola petición y deja el archivo listo para las siguientes veces.
    ids = _ids()
    de_esta = _pedir(f"competitions/{cod}/teams")
    time.sleep(PAUSA)
    for t in (de_esta or {}).get("teams", []):
        if t.get("name") and t.get("id"):
            _IDS[nombre_corto(t["name"])] = t["id"]
    ids = _ids()

    # Las plantillas se guardan aparte y duran mucho más: los goleadores
    # cambian cada jornada, pero quién está en el equipo casi no se mueve a
    # mitad de temporada, y pedir cincuenta plantillas cada vez añadía ocho
    # minutos a cada actualización para traer lo mismo.
    try:
        cache_sq = json.loads(CACHE_PLANTILLAS.read_text(encoding="utf-8"))             if CACHE_PLANTILLAS.exists() else {}
    except Exception:
        cache_sq = {}
    sq_fresca = (CACHE_PLANTILLAS.exists()
                 and time.time() - CACHE_PLANTILLAS.stat().st_mtime < DIAS_PLANTILLA * 86400)

    plantillas: dict[str, list] = {}
    for equipo, ident in sorted(ids.items()):
        crudo = cache_sq.get(str(ident))
        if crudo is None or not sq_fresca:
            info = _pedir(f"teams/{ident}")
            time.sleep(PAUSA)
            if info:
                crudo = info.get("squad") or []
                cache_sq[str(ident)] = crudo
        if not crudo:
            continue
        for p in crudo:
            nombre = p.get("name")
            if not nombre:
                continue
            st = marcas.get(f"{equipo}|{nombre}", {})
            pj = st.get("pj", 0)
            ficha = {
                "n": nombre,
                "p": _PUESTOS.get(p.get("position") or "", "SUP"),
                "pj": pj, "min": 0,
                "g": st.get("g", 0), "a": st.get("a", 0),
                "pen": st.get("pen", 0),
                "nac": p.get("nationality") or "",
                "edad": _edad(p.get("dateOfBirth"), hoy),
                # Sin ocasiones de gol: se marca para que la web no dibuje
                # gráficos que no tienen datos detrás.
                "basico": True,
            }
            if pj:
                ficha["g90"] = round(ficha["g"] / pj, 3)
                ficha["a90"] = round(ficha["a"] / pj, 3)
            plantillas.setdefault(equipo, []).append(ficha)

    # Quien haya marcado y no aparezca en ninguna plantilla —cedido, traspasado—
    # se añade igual: sus goles cuentan y el usuario los busca.
    for llave, st in marcas.items():
        eq, nombre = llave.split("|", 1)
        if any(j["n"] == nombre for j in plantillas.get(eq, [])):
            continue
        plantillas.setdefault(eq, []).append({
            "n": nombre, "p": "SUP", "pj": st["pj"], "min": 0,
            "g": st["g"], "a": st["a"], "pen": st["pen"], "nac": "", "edad": None,
            "basico": True,
            "g90": round(st["g"] / st["pj"], 3) if st["pj"] else 0,
            "a90": round(st["a"] / st["pj"], 3) if st["pj"] else 0,
        })

    for eq in plantillas:
        plantillas[eq].sort(key=lambda j: (-(j["g"] + j["a"]), j["n"]))

    if cache_sq:
        CACHE_PLANTILLAS.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PLANTILLAS.write_text(json.dumps(cache_sq, ensure_ascii=False),
                                     encoding="utf-8")

    if plantillas:
        guardado[clave] = plantillas
        CACHE_JUG.parent.mkdir(parents=True, exist_ok=True)
        CACHE_JUG.write_text(json.dumps(guardado, ensure_ascii=False),
                             encoding="utf-8")
    return plantillas or guardado.get(clave, {})
