"""Ligas sin xG: Portugal, Países Bajos, Brasil, Argentina y compañía.

Understat sólo publica las cinco grandes ligas europeas, así que para el resto
no hay ocasiones de gol por ninguna parte. Lo que sí hay son los resultados, en
el repositorio abierto ``openfootball`` y en formato Football.TXT.

El modelo Dixon-Coles no necesita xG para funcionar: puede ajustarse sobre los
goles. Pierde precisión —en el contraste que hicimos, el xG ganaba por un 6 %—
pero las probabilidades siguen siendo válidas. Por eso estos partidos se
entregan con la **misma forma** que los de Understat, con los goles ocupando el
lugar del xG: así el resto del programa los trata igual sin cambiar una línea.

Lo que estas ligas no tendrán nunca por esta vía: fichas de jugador, fotos,
percentiles ni nada que dependa de datos individuales. Esa información no está
en la fuente.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import requests

BASE = "https://raw.githubusercontent.com/openfootball"

# Cada liga: cómo se llama, dónde vive en openfootball y cuándo empieza su
# temporada. El mes de inicio distingue las de invierno europeas, que reparten
# el curso entre dos años («2025-26»), de las que lo juegan dentro del año
# natural («2025»), como casi toda América y el norte de Europa.
#
# Están todas las que la fuente publica, no sólo las que hoy tienen datos: el
# programa descarta sola la que no tenga partidos por jugar, así que en cuanto
# openfootball saque una temporada nueva, esa liga aparece en la web sin tocar
# nada. Por eso la lista es larga aunque de momento sólo entren unas pocas.
def _liga(nombre, pais, continente, repo, carpeta, sufijo, mes):
    return {"nombre": nombre, "pais": pais, "continente": continente,
            "repo": repo, "carpeta": carpeta, "sufijo": sufijo, "mes": mes}


LIGAS = {
    # ── Europa ────────────────────────────────────────────────────────────
    "eredivisie":  _liga("Eredivisie", "Países Bajos", "Europa", "europe", "netherlands", "nl1", 8),
    "primeira":    _liga("Primeira Liga", "Portugal", "Europa", "europe", "portugal", "pt1", 8),
    "superlig":    _liga("Süper Lig", "Turquía", "Europa", "europe", "turkey", "tr1", 8),
    "superleague": _liga("Super League", "Grecia", "Europa", "europe", "greece", "gr1", 8),
    "premiership": _liga("Premiership", "Escocia", "Europa", "europe", "scotland", "sco1", 8),
    "eliteserien": _liga("Eliteserien", "Noruega", "Europa", "europe", "norway", "no1", 1),
    "allsvenskan": _liga("Allsvenskan", "Suecia", "Europa", "europe", "sweden", "se1", 1),
    "veikkausliiga": _liga("Veikkausliiga", "Finlandia", "Europa", "europe", "finland", "fi1", 1),
    "irlanda":     _liga("Premier Division", "Irlanda", "Europa", "europe", "ireland", "ie1", 1),
    "islandia":    _liga("Besta deild", "Islandia", "Europa", "europe", "iceland", "is1", 1),
    "estonia":     _liga("Meistriliiga", "Estonia", "Europa", "europe", "estonia", "ee1", 1),
    "letonia":     _liga("Virsliga", "Letonia", "Europa", "europe", "latvia", "lv1", 1),
    "lituania":    _liga("A Lyga", "Lituania", "Europa", "europe", "lithuania", "lt1", 1),
    "georgia":     _liga("Erovnuli Liga", "Georgia", "Europa", "europe", "georgia", "ge1", 1),

    # ── América ───────────────────────────────────────────────────────────
    "brasileirao": _liga("Brasileirão", "Brasil", "América", "south-america", "brazil", "br1", 1),
    "argentina":   _liga("Liga Profesional", "Argentina", "América", "south-america", "argentina", "ar1", 1),
    "colombia":    _liga("Primera A", "Colombia", "América", "south-america", "colombia", "co1", 1),
    "ecuador":     _liga("Liga Pro", "Ecuador", "América", "south-america", "ecuador", "ec1", 1),
    "paraguay":    _liga("División Profesional", "Paraguay", "América", "south-america", "paraguay", "py1", 1),

    # ── Asia y África ─────────────────────────────────────────────────────
    "japon":       _liga("J1 League", "Japón", "Asia", "world", "asia/japan", "jp1", 1),
    "china":       _liga("Super League", "China", "Asia", "world", "asia/china", "cn1", 1),
    "nigeria":     _liga("NPFL", "Nigeria", "África", "world", "africa/nigeria", "ng1", 8),
}

_PARTIDO = re.compile(r"^\s*(?:\d{2}:\d{2}\s+)?(.+?)\s+v\s+(.+?)\s{2,}(\d+)-(\d+)")
# Un partido aún sin jugar: mismo formato, pero sin marcador al final
_PENDIENTE = re.compile(r"^\s*(?:(\d{2}:\d{2})\s+)?(.+?)\s+v\s+([^\d]+?)\s*$")
_FECHA = re.compile(r"^\s*[A-Z][a-z]{2}\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$")

_MESES = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

_CACHE: dict[tuple[str, str], list] = {}


def es_de_invierno(clave: str) -> bool:
    """¿Su temporada cruza el cambio de año, como en Europa?"""
    return LIGAS[clave]["mes"] >= 7


def etiqueta_temporada(clave: str, anio: int) -> str:
    """Cómo se nombra la temporada que empieza en ese año."""
    return f"{anio}-{str(anio + 1)[2:]}" if es_de_invierno(clave) else str(anio)


def _nombre_archivo(clave: str, anio: int) -> str:
    sufijo = LIGAS[clave]["sufijo"]
    if es_de_invierno(clave):
        return f"{anio}-{str(anio + 1)[2:]}_{sufijo}.txt"
    return f"{anio}_{sufijo}.txt"


def descargar(clave: str, anio: int) -> list[dict]:
    """Partidos jugados de una temporada, con la forma que usa Understat.

    Devuelve lista vacía si esa temporada aún no existe, que es lo normal al
    principio de curso y en las ligas que se incorporaron hace poco.
    """
    if (clave, anio) in _CACHE:
        return _CACHE[(clave, anio)]

    lg = LIGAS[clave]
    url = f"{BASE}/{lg['repo']}/master/{lg['carpeta']}/{_nombre_archivo(clave, anio)}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            _CACHE[(clave, anio)] = []
            return []
        partidos = _leer(r.text, anio, lg["mes"])
    except Exception:
        return []

    _CACHE[(clave, anio)] = partidos
    return partidos


def _leer(texto: str, anio_inicio: int, mes_inicio: int) -> list[dict]:
    """Interpreta un archivo Football.TXT de liga nacional.

    El año se deduce del mes, no de la línea anterior. Muchas ligas dividen la
    temporada en fases y vuelven a empezar por la jornada 1, de modo que el mes
    retrocede a mitad del archivo; arrastrar el año a partir de eso llevaba las
    fechas a 2038. Con el mes de inicio de la competición basta: en una liga que
    arranca en agosto, de agosto a diciembre es el primer año y de enero a julio
    el segundo.
    """
    cruza = mes_inicio >= 7
    partidos: list[dict] = []
    fecha_actual: date | None = None

    for linea in texto.splitlines():
        f = _FECHA.match(linea)
        if f:
            mes_txt, dia, anio_txt = f.groups()
            mes = _MESES.get(mes_txt)
            if not mes:
                continue
            if anio_txt:
                anio = int(anio_txt)
            elif cruza:
                anio = anio_inicio if mes >= mes_inicio else anio_inicio + 1
            else:
                anio = anio_inicio
            try:
                fecha_actual = date(anio, mes, int(dia))
            except ValueError:
                fecha_actual = None
            continue

        m = _PARTIDO.match(linea)
        if not m or not fecha_actual:
            continue
        local, visita, gl, gv = m.groups()
        local, visita = local.strip(), visita.strip()
        if not local or not visita:
            continue

        # Misma estructura que Understat, con los goles ocupando el sitio del
        # xG: el resto del programa no necesita saber de dónde vienen.
        partidos.append({
            "datetime": f"{fecha_actual.isoformat()} 00:00:00",
            "h": {"title": local}, "a": {"title": visita},
            "goals": {"h": int(gl), "a": int(gv)},
            "xG": {"h": float(gl), "a": float(gv)},
            "isResult": True,
        })
    return partidos


def calendario(clave: str, anio: int, desde: date | None = None) -> list[dict]:
    """Partidos aún sin jugar de una temporada, en orden de fecha.

    Son las líneas sin marcador. Se filtran por fecha porque un archivo a medio
    curso mezcla lo jugado con lo que queda, y sólo interesa lo segundo.
    """
    lg = LIGAS[clave]
    mes_inicio = lg["mes"]
    url = f"{BASE}/{lg['repo']}/master/{lg['carpeta']}/{_nombre_archivo(clave, anio)}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            return []
    except Exception:
        return []

    desde = desde or date.today()
    cruza = mes_inicio >= 7
    fuera: list[dict] = []
    fecha_actual: date | None = None

    for linea in r.text.splitlines():
        f = _FECHA.match(linea)
        if f:
            mes_txt, dia, anio_txt = f.groups()
            mes = _MESES.get(mes_txt)
            if not mes:
                continue
            if anio_txt:
                a = int(anio_txt)
            elif cruza:
                a = anio if mes >= mes_inicio else anio + 1
            else:
                a = anio
            try:
                fecha_actual = date(a, mes, int(dia))
            except ValueError:
                fecha_actual = None
            continue

        if not fecha_actual or fecha_actual < desde:
            continue
        if _PARTIDO.match(linea):
            continue                      # ése ya se jugó
        m = _PENDIENTE.match(linea)
        if not m:
            continue
        hora, local, visita = m.groups()
        local, visita = local.strip(), visita.strip()
        if not local or not visita or len(local) < 3 or len(visita) < 3:
            continue
        fuera.append({
            "fecha": fecha_actual.isoformat(),
            "hora": hora or "",
            "local": local, "visita": visita,
        })

    fuera.sort(key=lambda x: (x["fecha"], x["hora"]))
    return fuera


def temporada_en_curso(clave: str, hoy: date | None = None) -> int:
    """Año en que empezó la temporada que se está jugando ahora."""
    hoy = hoy or date.today()
    if not es_de_invierno(clave):
        return hoy.year
    return hoy.year if hoy.month >= LIGAS[clave]["mes"] else hoy.year - 1


def historial(clave: str, temporadas: int, hoy: date | None = None) -> tuple[list, int]:
    """Partidos de las últimas ``temporadas``, y el año de la más reciente.

    Si la temporada nueva todavía no está publicada se retrocede una, para que
    la web no se quede en blanco entre campañas.
    """
    actual = temporada_en_curso(clave, hoy)
    if not descargar(clave, actual) and descargar(clave, actual - 1):
        actual -= 1

    todos = []
    for atras in range(temporadas):
        todos += descargar(clave, actual - atras)
    return todos, actual
