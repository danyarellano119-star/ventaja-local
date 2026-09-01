"""Actualiza la web entera con un solo comando.

    python scripts/actualizar.py

Qué hace, en orden:

1. **Descarga el xG** de las cinco grandes ligas desde el endpoint interno de
   Understat (``/getLeagueData/{liga}/{año}``). Trae las cuatro últimas
   temporadas: en cuanto la nueva empiece a rodar, sus partidos entran solos.
2. **Recalcula las fuerzas** de ataque y defensa de cada equipo con decaimiento
   temporal, de modo que lo reciente pesa más que lo viejo.
3. **Refresca el calendario** desde openfootball si la temporada ya está
   publicada allí. Si no lo está, conserva el que ya hubiera guardado, así que
   ejecutar el script nunca deja la web sin partidos.
4. **Regenera** ``datos_ligas.json`` e ``index.html``.

Sobre las fuentes: FBref responde 403 a cualquier petición automatizada, así que
no se usa aquí. El calendario inicial de 2026/27 se extrajo a mano de FBref y
queda como respaldo hasta que openfootball publique la temporada.
"""

from __future__ import annotations

import json
import math
import sys
import unicodedata
from datetime import date, datetime, time as _hora, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import europa
import simular
import aciertos as mod_aciertos
import escudos as mod_escudos
import fotos as mod_fotos
import ligas_goles as mod_goles
import football_data as mod_fd
import registro as mod_registro
import estadios as mod_estadios

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
JSON_SALIDA = WEB / "datos_ligas.json"

# clave interna -> (nombre, país, código Understat, código openfootball)
LIGAS = {
    "premier":    ("Premier League", "Inglaterra", "EPL",        "en.1"),
    "laliga":     ("LaLiga",         "España",     "La_liga",    "es.1"),
    "bundesliga": ("Bundesliga",     "Alemania",   "Bundesliga", "de.1"),
    "seriea":     ("Serie A",        "Italia",     "Serie_A",    "it.1"),
    "ligue1":     ("Ligue 1",        "Francia",    "Ligue_1",    "fr.1"),
}

CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

XI = 0.0030      # decaimiento diario; vida media ~231 días
ANIOS_HISTORIA = 4    # temporadas que alimentan el modelo (medido: más no mejora)
ANIOS_GRAFICOS = 12   # temporadas que se muestran en los panoramas
PRIMER_ANIO = 2014    # Understat no publica xG anterior a esta temporada
MIN_PARTIDOS = 10     # con menos, las fuerzas de un equipo son puro ruido
SITIO = "https://danyarellano119-star.github.io/ventaja-local/"

# Las fuentes dan la hora local del estadio, sin decir de qué zona es. Sin esto
# no hay forma de convertirla: «20:30» no significa nada hasta saber dónde.
ZONAS = {
    "premier": "Europe/London", "laliga": "Europe/Madrid",
    "bundesliga": "Europe/Berlin", "seriea": "Europe/Rome",
    "ligue1": "Europe/Paris", "eredivisie": "Europe/Amsterdam",
    "primeira": "Europe/Lisbon", "superlig": "Europe/Istanbul",
    "superleague": "Europe/Athens", "premiership": "Europe/London",
    "eliteserien": "Europe/Oslo", "allsvenskan": "Europe/Stockholm",
    "veikkausliiga": "Europe/Helsinki", "irlanda": "Europe/Dublin",
    "islandia": "Atlantic/Reykjavik", "estonia": "Europe/Tallinn",
    "letonia": "Europe/Riga", "lituania": "Europe/Vilnius",
    "georgia": "Asia/Tbilisi", "brasileirao": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires", "colombia": "America/Bogota",
    "ecuador": "America/Guayaquil", "paraguay": "America/Asuncion",
    "japon": "Asia/Tokyo", "china": "Asia/Shanghai", "nigeria": "Africa/Lagos",
}


def a_utc(fecha: str, hora: str, clave_liga: str) -> str:
    """Pasa la hora local del estadio a UTC, para que la web la traduzca sola.

    Se hace aquí y no en el navegador porque el cálculo necesita saber la zona
    del país y si ese día había horario de verano, y eso lo resuelve Python con
    la base de datos del sistema sin traerse nada al cliente.
    """
    zona = ZONAS.get(clave_liga)
    if not zona or not fecha:
        return ""
    try:
        h, m = (hora or "00:00").split(":")[:2]
        local = datetime.combine(date.fromisoformat(fecha),
                                 _hora(int(h), int(m)), ZoneInfo(zona))
        return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    except Exception:
        return ""
RHO = -0.109     # corrección Dixon-Coles de marcadores bajos

# Nombres de Understat que conviene acortar o acentuar al mostrarlos
BONITO = {
    "Wolverhampton Wanderers": "Wolves", "RasenBallsport Leipzig": "RB Leipzig",
    "Borussia M.Gladbach": "M'gladbach", "FC Cologne": "Colonia",
    "Parma Calcio 1913": "Parma", "Paris Saint Germain": "PSG",
    "Atletico Madrid": "Atlético Madrid", "Alaves": "Alavés",
    "Manchester United": "Manchester Utd", "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nottingham", "Borussia Dortmund": "Dortmund",
    "Bayer Leverkusen": "Leverkusen", "Eintracht Frankfurt": "Frankfurt",
    "VfB Stuttgart": "Stuttgart", "FC Heidenheim": "Heidenheim",
    "Leeds": "Leeds United",

    # Los nombres de las ligas nuevas vienen en su idioma y con la razón social
    # entera. Se acortan a como se les llama en español: quien no sepa portugués
    # no tiene por qué adivinar que «Sporting Clube de Portugal» es el Sporting
    # de Lisboa, ni que «Sport Lisboa e Benfica» es el Benfica de toda la vida.
    "Sporting Clube de Portugal": "Sporting de Lisboa",
    "Sport Lisboa e Benfica": "Benfica",
    "Sporting Clube de Braga": "Braga",
    "FC Porto": "Oporto",
    "Vitória Guimarães": "Vitória de Guimarães",
    "CD Nacional": "Nacional",
    "CD Santa Clara": "Santa Clara",
    "CD Tondela": "Tondela",
    "CF Estrela da Amadora": "Estrela da Amadora",
    "CS Marítimo": "Marítimo",
    "Casa Pia AC": "Casa Pia",
    "FC Alverca": "Alverca",
    "FC Arouca": "Arouca",
    "FC Famalicão": "Famalicão",
    "GD Estoril Praia": "Estoril",
    "Gil Vicente FC": "Gil Vicente",
    "Moreirense FC": "Moreirense",
    "Rio Ave FC": "Rio Ave",
    "Académico de Viseu FC": "Académico de Viseu",

    "AFC Ajax": "Ajax", "Feyenoord Rotterdam": "Feyenoord",
    "FC Twente '65": "Twente", "FC Groningen": "Groningen",
    "FC Utrecht": "Utrecht", "FC Volendam": "Volendam",
    "SBV Excelsior": "Excelsior", "SC Heerenveen": "Heerenveen",
    "SC Cambuur-Leeuwarden": "Cambuur", "Willem II Tilburg": "Willem II",
    "Heracles Almelo": "Heracles", "Telstar 1963": "Telstar",
    "ADO Den Haag": "Den Haag", "PEC Zwolle": "Zwolle",

    "CR Flamengo": "Flamengo", "SE Palmeiras": "Palmeiras",
    "SC Corinthians Paulista": "Corinthians", "SC Internacional": "Internacional",
    "CR Vasco da Gama": "Vasco da Gama", "CA Mineiro": "Atlético Mineiro",
    "CA Paranaense": "Athletico Paranaense", "Cruzeiro EC": "Cruzeiro",
    "EC Bahia": "Bahía", "EC Vitória": "Vitória", "Fluminense FC": "Fluminense",
    "Grêmio FBPA": "Gremio", "Santos FC": "Santos", "São Paulo FC": "São Paulo",
    "Botafogo FR": "Botafogo", "Coritiba FBC": "Coritiba",
    "Chapecoense AF": "Chapecoense", "Mirassol FC": "Mirassol",
    "RB Bragantino": "Bragantino",
}


# --------------------------------------------------------------------------- #
# Temporadas
# --------------------------------------------------------------------------- #

def temporada_actual(hoy: date | None = None) -> int:
    """Año de inicio de la temporada en curso (las europeas arrancan en agosto)."""
    hoy = hoy or date.today()
    return hoy.year if hoy.month >= 7 else hoy.year - 1


def etiqueta(anio: int) -> str:
    return f"{anio}-{str(anio + 1)[2:]}"


# --------------------------------------------------------------------------- #
# Descarga
# --------------------------------------------------------------------------- #

_CACHE_UNDERSTAT: dict[tuple[str, int], tuple[list, list]] = {}


def bajar_understat(codigo: str, anio: int) -> tuple[list[dict], list[dict]]:
    """Partidos con xG y fichas de jugadores. Listas vacías si aún no hay nada.

    El resultado se guarda en memoria porque las mismas temporadas hacen falta
    dos veces: para ajustar el modelo y para dibujar los panoramas históricos.
    """
    if (codigo, anio) in _CACHE_UNDERSTAT:
        return _CACHE_UNDERSTAT[(codigo, anio)]
    url = f"https://understat.com/getLeagueData/{codigo}/{anio}"
    cab = {**CABECERAS, "Referer": f"https://understat.com/league/{codigo}/{anio}"}
    try:
        r = requests.get(url, headers=cab, timeout=60)
        if r.status_code != 200:
            return [], []
        datos = r.json()
        partidos = [m for m in datos.get("dates", []) if m.get("isResult")]
        _CACHE_UNDERSTAT[(codigo, anio)] = (partidos, datos.get("players", []))
        return _CACHE_UNDERSTAT[(codigo, anio)]
    except Exception as e:
        print(f"    [aviso] Understat {codigo}/{anio}: {type(e).__name__}", file=sys.stderr)
        return [], []


def seleccionar_jugadores(fichas: list[dict], por_equipo: int = 16) -> dict[str, list]:
    """Se queda con los jugadores que interesan para apostar, equipo por equipo.

    Entran los más productivos (goles más asistencias), los más amonestados
    —relevantes para los mercados de tarjetas— y, si queda sitio, los que más
    han jugado, que son los que casi seguro estarán en el campo.
    """
    POS = {"F": "DEL", "S": "DEL", "A": "MED", "M": "MED", "D": "DEF",
           "GK": "POR", "Sub": "SUP"}

    por_club: dict[str, list] = {}
    for p in fichas:
        try:
            minutos = int(p["time"])
            if minutos < 200:
                continue
            noventas = minutos / 90
            por_club.setdefault(p["team_title"], []).append({
                "n": p["player_name"],
                "p": POS.get((p.get("position") or "M").split()[0], "MED"),
                "pj": int(p["games"]), "min": minutos,
                "g": int(p["goals"]), "a": int(p["assists"]),
                "t": int(p["shots"]),
                "xg": round(float(p["xG"]), 1),
                "ta": int(p["yellow_cards"]), "tr": int(p["red_cards"]),
                # Sin penaltis: distingue al goleador de quien vive del punto
                "npg": int(p["npg"]), "npxg": round(float(p["npxG"]), 1),
                # Creación de juego
                "xa": round(float(p["xA"]), 1), "kp": int(p["key_passes"]),
                # Participación en el ataque, con y sin su remate o su pase final
                "xgc": round(float(p["xGChain"]), 1),
                "xgb": round(float(p["xGBuildup"]), 1),
                # Tasas por 90 minutos: lo único que permite comparar a un
                # titular con alguien que sale media hora por partido.
                "g90": round(int(p["goals"]) / noventas, 2),
                "a90": round(int(p["assists"]) / noventas, 2),
                "xg90": round(float(p["xG"]) / noventas, 2),
                "xa90": round(float(p["xA"]) / noventas, 2),
                "t90": round(int(p["shots"]) / noventas, 2),
                "kp90": round(int(p["key_passes"]) / noventas, 2),
            })
        except (KeyError, ValueError):
            continue

    salida = {}
    for club, jugadores in por_club.items():
        elegidos = sorted(jugadores, key=lambda j: -(j["g"] + j["a"]))[:8]
        nombres = {j["n"] for j in elegidos}

        for orden in (lambda j: -(j["ta"] + j["tr"] * 3),   # tarjetas
                      lambda j: -j["min"]):                 # minutos jugados
            for j in sorted(jugadores, key=orden):
                if len(elegidos) >= por_equipo:
                    break
                if j["n"] not in nombres:
                    elegidos.append(j)
                    nombres.add(j["n"])
        salida[club] = elegidos
    return salida


ANIOS_TRAYECTORIA = 5   # temporadas de historial por jugador


def trayectoria_jugadores(nombres: set[str], actual: int) -> dict[str, list]:
    """Historial temporada a temporada de cada jugador, en las cinco ligas.

    Se recorren todas las competiciones y no sólo la del equipo actual, de modo
    que quien cambia de país aparece con las dos etapas. Las descargas ya están
    en memoria de cuando se calcularon las fuerzas y los panoramas, así que esto
    no añade ni una petición.

    Ojo con lo que **no** cubre: Understat sólo publica las ligas nacionales.
    Champions, Europa League y las copas no están en ninguna fuente que se pueda
    consultar de forma automática, así que no aparecen aquí.
    """
    historial: dict[str, list] = {}

    for anio in range(actual - ANIOS_TRAYECTORIA + 1, actual + 1):
        if anio < PRIMER_ANIO:
            continue
        for clave, (nombre_liga, _pais, cod_us, _of) in LIGAS.items():
            _, fichas = bajar_understat(cod_us, anio)
            for f in fichas:
                n = f.get("player_name")
                if n not in nombres:
                    continue
                try:
                    minutos = int(f["time"])
                    if minutos < 90:      # un rato suelto no es una temporada
                        continue
                    # Como listas y no como diccionarios: con más de cinco mil
                    # filas, repetir los nombres de campo costaba 400 KB de más.
                    # El orden lo reconstruye la web y está anotado abajo.
                    historial.setdefault(n, []).append([
                        anio, clave,
                        BONITO.get(f["team_title"], f["team_title"]),
                        int(f["games"]), minutos,
                        int(f["goals"]), int(f["assists"]),
                        round(float(f["xG"]), 1), round(float(f["xA"]), 1),
                        int(f["shots"]), int(f["key_passes"]),
                        int(f["yellow_cards"]), int(f["red_cards"]),
                    ])
                except (KeyError, ValueError):
                    continue

    # De la más reciente a la más antigua, que es como se quiere leer
    for filas in historial.values():
        filas.sort(key=lambda x: x[0], reverse=True)
    return historial


# Orden de los campos de cada fila de trayectoria, tal y como los lee la web
CAMPOS_TRAYECTORIA = ["anio", "liga", "eq", "pj", "min", "g", "a",
                      "xg", "xa", "t", "kp", "ta", "tr"]


def bajar_roster(id_partido: str) -> dict | None:
    """Alineación de un partido: minutos y tarjetas de cada jugador."""
    try:
        r = requests.get(f"https://understat.com/getMatchData/{id_partido}",
                         headers={**CABECERAS,
                                  "Referer": f"https://understat.com/match/{id_partido}"},
                         timeout=40)
        return r.json().get("rosters") if r.status_code == 200 else None
    except Exception:
        return None


def detectar_bajas(partidos: list[dict], hoy: date, ventana: int = 3) -> dict[str, dict]:
    """Anota el estado reciente de cada jugador según los últimos partidos.

    Distingue dos cosas que no tienen el mismo valor:

    - **Sancionado**: vio roja en el último partido de su equipo. Es una regla
      dura del reglamento, así que el jugador queda descartado.
    - **Sin minutos**: no ha jugado en los últimos ``ventana`` partidos. Esto es
      sólo un dato, **no una conclusión**: puede estar lesionado, pero también
      rotando, recién recuperado o simplemente suplente. Se muestra como aviso y
      su probabilidad se sigue calculando con normalidad.

    Sólo se aplica si el equipo ha jugado hace poco: al principio de temporada
    los partidos más recientes son de hace meses y no dicen nada útil.
    """
    por_equipo: dict[str, list] = {}
    for m in partidos:
        for eq, lado in ((m["h"]["title"], "h"), (m["a"]["title"], "a")):
            por_equipo.setdefault(eq, []).append(
                (m["datetime"][:10], m["id"], lado))

    estado: dict[str, dict] = {}
    consultados: dict[str, dict] = {}

    for equipo, lista in por_equipo.items():
        lista.sort(reverse=True)
        if not lista:
            continue
        ultima = datetime.strptime(lista[0][0], "%Y-%m-%d").date()
        if (hoy - ultima).days > 30:
            continue                      # parón largo: no hay nada que inferir

        recientes = lista[:ventana]
        minutos: dict[str, int] = {}
        rojas: set[str] = set()

        for i, (_, id_partido, lado) in enumerate(recientes):
            rosters = consultados.get(id_partido) or bajar_roster(id_partido)
            if rosters is None:
                continue
            consultados[id_partido] = rosters
            for ficha in (rosters.get(lado) or {}).values():
                nombre = ficha.get("player")
                if not nombre:
                    continue
                minutos[nombre] = minutos.get(nombre, 0) + int(ficha.get("time", 0))
                if i == 0 and int(ficha.get("red_card", 0)) > 0:
                    rojas.add(nombre)

        for nombre, mins in minutos.items():
            if nombre in rojas:
                estado.setdefault(equipo, {})[nombre] = "sancionado"
            elif mins == 0:
                estado.setdefault(equipo, {})[nombre] = "sin_minutos"

    return estado


def mercados(lam: float, mu: float, rho: float, maxg: int = 8) -> dict:
    """Todo lo que el modelo dice de un partido, no sólo quién gana.

    Son los mismos números que la web enseña en la ficha del partido: el 1X2,
    las líneas de goles, si marcan los dos y el marcador más probable. Se
    calculan aquí para poder guardarlos y, cuando el partido acabe, comprobar
    uno a uno si se cumplieron.
    """
    def pois(k, l):
        return math.exp(-l + k * math.log(l) - math.lgamma(k + 1))

    m = [[pois(i, lam) * pois(j, mu) for j in range(maxg + 1)]
         for i in range(maxg + 1)]
    m[0][0] *= 1 - lam * mu * rho
    m[0][1] *= 1 + lam * rho
    m[1][0] *= 1 + mu * rho
    m[1][1] *= 1 - rho
    total = sum(sum(f) for f in m)

    pl = pe = pv = o15 = o25 = o35 = btts = 0.0
    mejor, p_mejor = (0, 0), 0.0
    for i in range(maxg + 1):
        for j in range(maxg + 1):
            p = m[i][j] / total
            if i > j:
                pl += p
            elif i == j:
                pe += p
            else:
                pv += p
            if i + j > 1.5:
                o15 += p
            if i + j > 2.5:
                o25 += p
            if i + j > 3.5:
                o35 += p
            if i and j:
                btts += p
            if p > p_mejor:
                mejor, p_mejor = (i, j), p

    return {"pl": pl, "pe": pe, "pv": pv,
            "o15": o15, "o25": o25, "o35": o35, "btts": btts,
            "marcador": f"{mejor[0]}-{mejor[1]}", "p_marcador": p_mejor}


def mismo_equipo(a: str, b: str) -> bool:
    """¿Estos dos nombres son del mismo club?

    Understat y openfootball recortan distinto: «Ipswich» frente a «Ipswich Town
    FC», «Hull» frente a «Hull City AFC». Normalizar no basta porque sobreviven
    palabras como «town» o «city», así que se acepta también que uno empiece por
    el otro. El mínimo de cuatro letras evita que «Leeds» y «Le Havre» se
    confundan por compartir principio.
    """
    x, y = normalizar(a), normalizar(b)
    if not x or not y:
        return False
    if x == y:
        return True
    corto, largo = (x, y) if len(x) <= len(y) else (y, x)
    # El prefijo tiene que cortar en palabra entera. Sin esto, «Inter» casaba
    # con «Internacional» por compartir las cinco primeras letras.
    return (len(corto) >= 4 and largo.startswith(corto)
            and largo[len(corto):len(corto) + 1] == " ")


def bajar_calendario(codigo_of: str, anio: int) -> list[dict]:
    """Partidos sin jugar de openfootball. Lista vacía si la temporada no está."""
    url = (f"https://cdn.jsdelivr.net/gh/openfootball/football.json@master/"
           f"{etiqueta(anio)}/{codigo_of}.json")
    try:
        r = requests.get(url, timeout=45)
        if r.status_code != 200:
            return []
        futuros = []
        for m in r.json().get("matches", []):
            if m.get("score"):
                continue     # ya jugado
            futuros.append({
                "j": int(str(m.get("round", "0")).split()[-1] or 0),
                "fecha": m["date"],
                "hora": (m.get("time") or "00:00")[:5],
                "l_raw": m["team1"], "v_raw": m["team2"],
            })
        return futuros
    except Exception as e:
        print(f"    [aviso] openfootball {codigo_of}: {type(e).__name__}", file=sys.stderr)
        return []


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #

def ajustar_fuerzas(partidos: list[dict], referencia: date, iteraciones: int = 400):
    """Estima ataque, defensa y ventaja de local sobre log(xG), con decaimiento.

    Resuelve el sistema por iteración alternada (Gauss-Seidel): es equivalente a
    los mínimos cuadrados ponderados del modelo pero sin montar la matriz, que
    con varias temporadas sería innecesariamente grande.
    """
    obs = []
    for m in partidos:
        dias = (referencia - datetime.strptime(m["datetime"][:10], "%Y-%m-%d").date()).days
        peso = math.exp(-XI * max(dias, 0))
        h, a = m["h"]["title"], m["a"]["title"]
        obs.append((h, a, 1, math.log(max(float(m["xG"]["h"]), 0.05)), peso))
        obs.append((a, h, 0, math.log(max(float(m["xG"]["a"]), 0.05)), peso))

    equipos = sorted({o[0] for o in obs})
    atq = {e: 0.0 for e in equipos}
    dfn = {e: 0.0 for e in equipos}
    gamma = 0.25

    # Índices por equipo para no recorrer todas las observaciones en cada paso
    como_atacante = {e: [] for e in equipos}
    como_defensor = {e: [] for e in equipos}
    for o in obs:
        como_atacante[o[0]].append(o)
        como_defensor[o[1]].append(o)

    for _ in range(iteraciones):
        num = sum(p * (y - atq[a] + dfn[d]) for a, d, loc, y, p in obs if loc)
        den = sum(p for *_, loc, _, p in ((o[0], o[1], o[2], o[3], o[4]) for o in obs) if loc)
        gamma = num / den if den else gamma

        for e in equipos:
            ob = como_atacante[e]
            den2 = sum(p for *_, p in ob)
            if den2:
                atq[e] = sum(p * (y + dfn[d] - gamma * loc) for _, d, loc, y, p in ob) / den2

        media = sum(atq.values()) / len(equipos)
        for e in equipos:
            atq[e] -= media

        for e in equipos:
            ob = como_defensor[e]
            den3 = sum(p for *_, p in ob)
            if den3:
                dfn[e] = sum(p * (atq[a] - y + gamma * loc) for a, _, loc, y, p in ob) / den3

    return atq, dfn, gamma


def historial(partidos: list[dict], equipos_validos: set[str],
              maximo: int = 38) -> dict[str, list]:
    """Últimos partidos de cada equipo, para las gráficas de su ficha.

    Cada entrada es compacta a propósito —la web lleva los datos embebidos— y
    guarda lo justo para dibujar forma, xG y reparto local/visitante.
    """
    por_equipo: dict[str, list] = {}
    for m in sorted(partidos, key=lambda x: x["datetime"]):
        gh, ga = int(m["goals"]["h"]), int(m["goals"]["a"])
        xh, xa = round(float(m["xG"]["h"]), 2), round(float(m["xG"]["a"]), 2)
        h, a = m["h"]["title"], m["a"]["title"]
        if h in equipos_validos:
            por_equipo.setdefault(h, []).append(
                {"f": m["datetime"][:10], "r": a, "c": "L",
                 "gf": gh, "gc": ga, "xf": xh, "xc": xa})
        if a in equipos_validos:
            por_equipo.setdefault(a, []).append(
                {"f": m["datetime"][:10], "r": h, "c": "V",
                 "gf": ga, "gc": gh, "xf": xa, "xc": xh})
    return {e: v[-maximo:] for e, v in por_equipo.items()}


def agregar(partidos: list[dict]) -> dict:
    """Totales por equipo: puntos, goles, xG, xGA y partidos jugados."""
    ag = {}
    def caja(e):
        return ag.setdefault(e, {"pts": 0, "pj": 0, "gf": 0, "gc": 0, "xg": 0.0, "xga": 0.0})
    for m in partidos:
        h, a = m["h"]["title"], m["a"]["title"]
        gh, ga = int(m["goals"]["h"]), int(m["goals"]["a"])
        xh, xa = float(m["xG"]["h"]), float(m["xG"]["a"])
        for eq, gf, gc, xg, xga in ((h, gh, ga, xh, xa), (a, ga, gh, xa, xh)):
            c = caja(eq)
            c["pj"] += 1; c["gf"] += gf; c["gc"] += gc
            c["xg"] += xg; c["xga"] += xga
            c["pts"] += 3 if gf > gc else 1 if gf == gc else 0
    for c in ag.values():
        c["xg"] = round(c["xg"], 1)
        c["xga"] = round(c["xga"], 1)
    return ag


# --------------------------------------------------------------------------- #
# Emparejado de nombres
# --------------------------------------------------------------------------- #

def componentes_principales(equipos: dict) -> dict:
    """Reduce el perfil de cada equipo a dos ejes por componentes principales.

    En vez de comparar quince métricas a la vez, se buscan las dos combinaciones
    que mejor separan a los equipos entre sí. Casi siempre el primer eje acaba
    representando la calidad general (los buenos a un lado, los malos al otro) y
    el segundo, el estilo: equipos que apuestan por atacar frente a los que se
    apoyan en no encajar.

    Se usan seis variables por partido, todas tipificadas para que ninguna pese
    más por tener una escala mayor. El cálculo es una descomposición en valores
    singulares sobre la matriz centrada, que es exactamente lo que hace un PCA.
    """
    import numpy as np

    clubes = [e for e in equipos.values() if not e.get("nuevo") and e.get("pj")]
    if len(clubes) < 6:
        return {}

    VARIABLES = [
        ("Ocasiones generadas", lambda e: e["xg"] / e["pj"]),
        ("Ocasiones concedidas", lambda e: -e["xga"] / e["pj"]),
        ("Goles marcados", lambda e: e["gf"] / e["pj"]),
        ("Goles encajados", lambda e: -e["gc"] / e["pj"]),
        ("Puntería", lambda e: (e["gf"] - e["xg"]) / e["pj"]),
        ("Solidez del portero", lambda e: (e["xga"] - e["gc"]) / e["pj"]),
    ]

    X = np.array([[f(e) for _, f in VARIABLES] for e in clubes], dtype=float)
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)

    U, S, Vt = np.linalg.svd(X, full_matrices=False)
    coords = U[:, :2] * S[:2]
    varianza = (S ** 2) / (S ** 2).sum()

    # El signo que devuelve la SVD es arbitrario: se fija para que a la derecha
    # queden siempre los equipos con más puntos, que es lo intuitivo.
    puntos = np.array([e["pts"] / e["pj"] for e in clubes])
    for j in range(2):
        if np.corrcoef(coords[:, j], puntos)[0, 1] < 0:
            coords[:, j] *= -1
            Vt[j] *= -1

    return {
        "equipos": {e["clave"]: [round(float(coords[i, 0]), 3),
                                 round(float(coords[i, 1]), 3)]
                    for i, e in enumerate(clubes)},
        "varianza": [round(float(varianza[0]) * 100, 1),
                     round(float(varianza[1]) * 100, 1)],
        "cargas": [
            [{"v": VARIABLES[k][0], "p": round(float(Vt[j, k]), 3)}
             for k in range(len(VARIABLES))]
            for j in range(2)
        ],
    }


def resumen_historico(cod_us: str, anio_final: int,
                      temporadas: int = ANIOS_GRAFICOS) -> list[dict]:
    """Clasificación de cada temporada disponible, para el panorama de liga.

    Va mucho más atrás que la ventana del modelo: aquí no se predice nada, sólo
    se muestra cómo ha evolucionado la liga.
    """
    salida = []
    for anio in range(max(anio_final - temporadas, PRIMER_ANIO), anio_final):
        partidos, _ = bajar_understat(cod_us, anio)
        if len(partidos) < 100:
            continue
        ag = agregar(partidos)
        tabla = sorted(ag.items(), key=lambda kv: (-kv[1]["pts"],
                                                   -(kv[1]["gf"] - kv[1]["gc"])))
        salida.append({
            "temp": etiqueta(anio),
            "campeon": BONITO.get(tabla[0][0], tabla[0][0]),
            "pts_campeon": tabla[0][1]["pts"],
            "goles": sum(c["gf"] for c in ag.values()),
            "partidos": len(partidos),
            "goles_partido": round(sum(c["gf"] for c in ag.values()) / len(partidos), 2),
            # Clasificación completa: la web deja consultar cualquier temporada
            "tabla": [{"e": BONITO.get(k, k), "pj": v["pj"], "pts": v["pts"],
                       "gf": v["gf"], "gc": v["gc"],
                       "xg": v["xg"], "xga": v["xga"]} for k, v in tabla],
        })
    return salida


# Palabras que sobran en el nombre de un club: sociedades y muletillas que cada
# fuente escribe a su manera. Aquí NO entran «united», «city» ni «town», aunque
# lo parezcan: quitarlas dejaba a Manchester United y Manchester City reducidos
# los dos a «manchester», y el calendario acababa asignándole al United partidos
# del City.
_SOCIEDAD = {"fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "sv", "cd", "ud",
             "rcd", "sd", "rc", "club", "calcio",
             # openfootball escribe el nombre oficial completo —«ACF
             # Fiorentina», «TSG 1899 Hoffenheim», «Olympique Lyonnais»—
             # mientras que la fuente de estadísticas usa el corto. Sin
             # descartar estas siglas, cada equipo aparecía dos veces: una con
             # sus datos y otra vacía, con el calendario colgando de la falsa.
             "acf", "aj", "ogc", "us", "ss", "tsg", "fsv", "es", "rb", "ca",
             "vfb", "vfl", "bsc", "sk", "fk", "cr", "ec", "se", "aa",
             "olympique", "stade", "de", "del", "la", "le", "les", "los", "do"}

# Abreviaturas que unas fuentes usan y otras no
_EQUIVALE = {"utd": "united", "man": "manchester", "wolves": "wolverhampton",
             "spurs": "tottenham", "nott m": "nottingham", "psg": "paris",
             # Nombres que no se parecen entre fuentes
             "koln": "cologne", "rennais": "rennes", "munchen": "munich",
             "monchengladbach": "gladbach"}
_EQUIVALE.pop("psg", None)   # ver _ALIAS_NOMBRE: «paris» a secas es del Paris FC

# Nombres enteros que hay que traducir antes de comparar nada. Van aquí y no en
# _EQUIVALE porque cambiar una palabra suelta se contagia: con «psg» → «paris»,
# el PSG casaba con el Paris FC, que es otro club y este año juega en la misma
# liga.
_ALIAS_NOMBRE = {
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "sporting cp": "sporting portugal",
    "sporting lisboa": "sporting portugal",
}

# Palabras que llevan tantos clubes que sólo valen si coinciden exactas. Sin
# esto «Sporting» casaba con el «Sport Lisboa e Benfica» por parecido de raíz.
_SOLO_EXACTA = {"sport", "sporting", "real", "atletico", "athletic", "deportivo",
                "racing", "city", "town", "united", "borussia", "dynamo",
                "spartak", "estrela", "nacional", "internacional", "juventud"}


def normalizar(nombre: str) -> str:
    """Clave laxa para casar nombres entre fuentes distintas.

    Se quedan las palabras que identifican al club y se van las que no dicen
    nada. Es deliberadamente menos agresiva de lo que parece necesario: dos
    equipos distintos que acaben con la misma clave son un error mucho más caro
    que dos formas del mismo equipo que no se reconozcan.
    """
    # Se quitan todos los acentos, no una lista a mano: faltaban la diéresis
    # alemana y la tilde portuguesa, así que «Köln» y «Mönchengladbach» no
    # casaban nunca con «Cologne» ni con «Gladbach».
    s = unicodedata.normalize("NFKD", nombre.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    for a, b in [(".", " "), ("-", " "), ("&", " "), ("'", " ")]:
        s = s.replace(a, b)
    # Los años y números del nombre oficial —«1899», «04», «1901»— no
    # identifican a nadie: los lleva media Bundesliga.
    palabras = [_EQUIVALE.get(w, w) for w in s.split()
                if w not in _SOCIEDAD and len(w) > 1 and not w.isdigit()]
    limpio = " ".join(palabras)
    return _ALIAS_NOMBRE.get(limpio, limpio)


def emparejar(nombre: str, candidatos: dict[str, str]) -> str | None:
    """Busca el equipo de Understat que corresponde a un nombre del calendario.

    Los calendarios recortan los nombres largos («Racing Sant» por «Racing
    Santander»), así que además de la coincidencia exacta se acepta que uno sea
    prefijo del otro o que compartan la primera palabra distintiva.
    """
    n = normalizar(nombre)
    if n in candidatos:
        return candidatos[n]

    sueltos = [o for c, o in candidatos.items()
               if c.startswith(n) or n.startswith(c) or (len(n) > 4 and n in c)]
    if len(sueltos) == 1:
        return sueltos[0]

    # Última pasada: misma palabra inicial y longitud parecida
    palabras = n.split()
    if palabras:
        primera = palabras[0]
        iguales = [o for c, o in candidatos.items()
                   if c.split() and c.split()[0] == primera and len(primera) > 3]
        if len(iguales) == 1:
            return iguales[0]

    # Pasada por palabras. Los nombres oficiales llevan la palabra que
    # identifica al club en medio y no al principio: «Olympique Lyonnais» por
    # «Lyon», «Racing Club de Lens» por «Lens». Se cuenta cuántas palabras
    # comparten los dos nombres y sólo se acepta si hay un candidato que gana
    # solo: emparejar mal a dos equipos es mucho peor que no emparejar.
    if palabras:
        puntuados = []
        for clave, original in candidatos.items():
            suyas = clave.split()
            if not suyas:
                continue
            # El nombre corto tiene que quedar cubierto **entero** por el
            # largo. Con exigir una palabra cualquiera bastaba para que
            # «Coventry City» casara con el «Manchester City» por compartir
            # «city», que es justo el error que no puede pasar.
            cortas, largas = ((palabras, suyas) if len(palabras) <= len(suyas)
                              else (suyas, palabras))
            if all(any(_misma_palabra(a, b) for b in largas) for a in cortas):
                puntuados.append((len(cortas), original))
        if puntuados:
            mejor = max(c for c, _ in puntuados)
            ganadores = {o for c, o in puntuados if c == mejor}
            if len(ganadores) == 1:
                return ganadores.pop()
    return None


def _misma_palabra(a: str, b: str) -> bool:
    """¿Estas dos palabras nombran a lo mismo?

    Vale la igualdad, que una empiece por la otra —«Lyon» y «Lyonnais», «Brest»
    y «Brestois»— o que una contenga a la otra cuando es larga —«Gladbach»
    dentro de «Mönchengladbach»—. Por debajo de cuatro letras no se arriesga:
    «Real» y «Rayo» comparten demasiado con demasiados.
    """
    if a == b:
        return len(a) > 3
    if a in _SOLO_EXACTA or b in _SOLO_EXACTA:
        return False
    corta, larga = (a, b) if len(a) < len(b) else (b, a)
    if len(corta) < 4:
        return False
    return larga.startswith(corta) or (len(corta) > 4 and corta in larga)


def perfil_ascendido(equipos: dict) -> dict:
    """Nivel medio de los tres últimos clasificados, para los recién ascendidos."""
    peores = sorted(equipos.values(), key=lambda e: e["pts"])[:3]
    n = len(peores) or 1
    return {
        "atq": sum(e["atq"] for e in peores) / n,
        "def": sum(e["def"] for e in peores) / n,
        "pts": round(sum(e["pts"] for e in peores) / n),
        "pj":  peores[0]["pj"] if peores else 38,
        "gf":  round(sum(e["gf"] for e in peores) / n),
        "gc":  round(sum(e["gc"] for e in peores) / n),
        "xg":  round(sum(e["xg"] for e in peores) / n, 1),
        "xga": round(sum(e["xga"] for e in peores) / n, 1),
        "jug": [], "hist": [],   # de un ascendido no hay nada en esta categoría
    }


# --------------------------------------------------------------------------- #
# Publicación
# --------------------------------------------------------------------------- #

DESCRIPCION = ("Probabilidades, cuotas y estadísticas de cada partido de la "
               "Premier League, LaLiga, Serie A, Bundesliga y Ligue 1. "
               "Análisis por equipo y por jugador, con datos actualizados.")

FAVICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
           "viewBox='0 0 32 32'><rect width='32' height='32' rx='7' "
           "fill='%231F6FB2'/><path d='M16 6l7 5-2.7 8.2h-8.6L9 11z' "
           "fill='white'/></svg>")


def envolver_html(cuerpo: str, titulo: str) -> str:
    """Añade la cabecera que necesitan navegadores y buscadores.

    La plantilla sólo contiene el contenido; aquí se le pone alrededor el
    documento completo, con idioma, descripción y las etiquetas que usan
    WhatsApp o las redes para generar la vista previa de un enlace.
    """
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<meta name="description" content="{DESCRIPCION}">
<link rel="canonical" href="{SITIO}">
<link rel="icon" href="{FAVICON}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Ventaja Local">
<meta property="og:locale" content="es_ES">
<meta property="og:title" content="{titulo}">
<meta property="og:description" content="{DESCRIPCION}">
<meta property="og:url" content="{SITIO}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{titulo}">
<meta name="twitter:description" content="{DESCRIPCION}">
</head>
<body>
{cuerpo}
</body>
</html>
"""


def escribir_seo(hoy: date) -> None:
    """robots.txt y sitemap.xml, los dos archivos que buscan los rastreadores."""
    robots = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {SITIO}sitemap.xml",
        "",
    ]
    (WEB / "robots.txt").write_text("\n".join(robots), encoding="utf-8")

    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        f"    <loc>{SITIO}</loc>",
        f"    <lastmod>{hoy.isoformat()}</lastmod>",
        "    <changefreq>daily</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
        "</urlset>",
        "",
    ]
    (WEB / "sitemap.xml").write_text("\n".join(sitemap), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #

def main() -> None:
    hoy = date.today()
    actual = temporada_actual(hoy)
    anterior = actual - 1
    print(f"Actualizando · {hoy:%d/%m/%Y} · temporada {etiqueta(actual)}\n")

    previo = {}
    previo_europa = None
    if JSON_SALIDA.exists():
        # Sólo sirve de respaldo para el calendario. Si estuviera ilegible —por
        # ejemplo con marcas de conflicto de un git a medias— no es motivo para
        # abortar: se regenera entero de todas formas.
        try:
            _guardado = json.loads(JSON_SALIDA.read_text(encoding="utf-8"))
            previo = _guardado.get("ligas", {})
            previo_europa = _guardado.get("europa")
        except (json.JSONDecodeError, OSError) as e:
            print(f"    [aviso] {JSON_SALIDA.name} ilegible ({type(e).__name__}); "
                  f"se regenera desde cero", file=sys.stderr)

    # Con hora, no sólo la fecha: el robot corre cada tres horas y así se
    # distingue una copia recién servida de una que lleve rato en el navegador.
    reg = mod_registro.cargar()
    # Las claves del archivo se recalculan con el normalizador de ahora. Sin
    # esto, cada mejora del emparejado de nombres partía el historial en dos.
    reg, movidas = mod_registro.migrar(reg, normalizar)
    if movidas:
        print(f"    {movidas} fichas del registro reetiquetadas con la clave nueva")
    reg_nuevos = reg_resueltos = 0

    salida = {"generado": hoy.isoformat(),
              "generado_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
              "temporada": etiqueta(actual), "ligas": {}}

    for clave, (nombre, pais, cod_us, cod_of) in LIGAS.items():
        print(f"{nombre}")

        # Cuatro temporadas: por debajo de eso el modelo pierde precisión y por
        # encima no gana nada, según el backtest de scripts/experimento_historia.py
        p_act, f_act = bajar_understat(cod_us, actual)
        p_ant, f_ant = bajar_understat(cod_us, anterior)
        previas = []
        for atras in range(2, ANIOS_HISTORIA):
            previas += bajar_understat(cod_us, actual - atras)[0]
        partidos = previas + p_ant + p_act
        if not partidos:
            print("    sin datos de Understat; se conserva lo anterior")
            if clave in previo:
                salida["ligas"][clave] = previo[clave]
            continue

        # Las fichas de la temporada en curso mandan en cuanto haya suficientes
        # partidos; antes de eso, las del año pasado describen mejor al plantel.
        fichas = f_act if len(p_act) >= 60 else f_ant
        plantillas = seleccionar_jugadores(fichas)

        bajas = detectar_bajas(p_act or p_ant, hoy)
        n_bajas = 0
        for club, jugadores in plantillas.items():
            estado_club = bajas.get(club, {})
            for j in jugadores:
                if j["n"] in estado_club:
                    j["baja"] = estado_club[j["n"]]
                    n_bajas += 1

        print(f"    {len(partidos)} partidos con xG · "
              f"{sum(len(v) for v in plantillas.values())} jugadores"
              + (f" · {n_bajas} con baja detectada" if n_bajas else ""))

        atq, dfn, gamma = ajustar_fuerzas(partidos, hoy)
        # Las fuerzas usan las cuatro temporadas ponderadas, pero las cifras que
        # se muestran (puntos, goles, xG) son de una sola: sumar cuatro años
        # daría un «114 partidos, 173 puntos» sin sentido. Y hasta que la nueva
        # temporada tenga recorrido, describe mejor al equipo la anterior.
        referencia = p_act if len(p_act) >= 50 else (p_ant or p_act)
        ag = agregar(referencia)
        ag_act = agregar(p_act)
        ag_ant = agregar(p_ant)
        ag_ambas = agregar(p_ant + p_act)

        # El historial abarca las dos temporadas seguidas, no sólo la de
        # referencia: así la ficha del equipo puede enseñar su estado de ahora
        # —que es lo que la gente busca— y también dejar mirar hacia atrás.
        hist = historial(p_ant + p_act, set(atq), maximo=60)
        # Fecha en que arrancó la temporada nueva, para que la web sepa por
        # dónde cortar ese historial sin marcar cada partido uno a uno.
        corte_act = min((m["datetime"][:10] for m in p_act), default="")

        pj_ventana: dict[str, int] = {}
        for m in partidos:
            for eq in (m["h"]["title"], m["a"]["title"]):
                pj_ventana[eq] = pj_ventana.get(eq, 0) + 1

        equipos = {}
        for e in atq:
            # El ajuste incluye equipos de temporadas anteriores que este año ya
            # no están en la categoría; sus fuerzas ayudan al modelo, pero no
            # tienen cifras que mostrar, así que no llegan a la web.
            if e not in ag:
                continue
            # Un equipo con dos o tres partidos en toda la ventana tiene
            # fuerzas sin sentido: se trata igual que a un ascendido. Se cuentan
            # los de las cuatro temporadas, no los de la actual, porque en
            # agosto todos llevarían uno o dos.
            if pj_ventana.get(e, 0) < MIN_PARTIDOS:
                continue
            equipos[e] = {"nombre": BONITO.get(e, e), "clave": e, "nuevo": False,
                          "atq": round(atq[e], 5), "def": round(dfn[e], 5), **ag[e],
                          "jug": plantillas.get(e, []), "hist": hist.get(e, []),
                          # Las mismas cifras separadas por temporada, para que
                          # la web deje elegir: lo que va de curso, lo de la
                          # temporada pasada, o las dos sumadas.
                          "temp": {"act": ag_act.get(e), "ant": ag_ant.get(e),
                                   "ambas": ag_ambas.get(e)}}

        # Calendario: openfootball si está; si no, el que ya hubiera
        crudos = bajar_calendario(cod_of, actual)
        if crudos:
            indice = {normalizar(e): e for e in equipos}
            base = perfil_ascendido(equipos)
            futuros, sin_casar = [], set()
            for m in crudos:
                ids = []
                for bruto in (m["l_raw"], m["v_raw"]):
                    eq = emparejar(bruto, indice)
                    if eq is None:
                        eq = bruto
                        if eq not in equipos:
                            equipos[eq] = {"nombre": BONITO.get(bruto, bruto), "clave": eq, "nuevo": True, **base}
                            sin_casar.add(bruto)
                    ids.append(eq)
                futuros.append({"j": m["j"], "fecha": m["fecha"], "hora": m["hora"],
                                "l": ids[0], "v": ids[1]})

            # openfootball tarda días en cargar los marcadores, así que no sirve
            # para saber qué se ha jugado ya. Understat sí va al día: se cruzan
            # sus partidos con el calendario y se descartan los repetidos, más
            # todo lo que tenga fecha pasada.
            jugados_por_dia: dict[str, list] = {}
            for m in p_act:
                jugados_por_dia.setdefault(m["datetime"][:10], []).append(
                    (m["h"]["title"], m["a"]["title"]))
            antes = len(futuros)
            # Se compara con la clave del equipo, no con su nombre para mostrar.
            # «Nottingham Forest» se muestra como «Nottingham» y «Hull» llega del
            # calendario como «Hull City AFC»: normalizando las claves ambas se
            # reducen a lo mismo, mientras que mezclar nombre y clave no casaba
            # jamás y dejaba en la lista partidos ya jugados.
            def ya_jugado(f):
                return any(mismo_equipo(f["l"], l) and mismo_equipo(f["v"], v)
                           for l, v in jugados_por_dia.get(f["fecha"], []))

            futuros = [f for f in futuros
                       if f["fecha"] >= hoy.isoformat() and not ya_jugado(f)]
            if antes != len(futuros):
                print(f"    {antes - len(futuros)} partidos ya jugados fuera de la lista")

            futuros.sort(key=lambda p: (p["fecha"], p["hora"]))
            for f in futuros:
                f["utc"] = a_utc(f["fecha"], f["hora"], clave)
            partidos_web = futuros[:60]
            ascendidos = sorted(sin_casar)
            print(f"    calendario de openfootball: {len(futuros)} por jugar")
        elif clave in previo:
            partidos_web = previo[clave]["partidos"]
            ascendidos = previo[clave].get("ascendidos", [])
            base = perfil_ascendido(equipos)
            for p in partidos_web:                      # ascendidos del respaldo
                for eq in (p["l"], p["v"]):
                    if eq not in equipos:
                        anterior_eq = previo[clave]["equipos"].get(eq, {})
                        equipos[eq] = {"nombre": anterior_eq.get("nombre", eq),
                                       "clave": eq, "nuevo": True, **base}
            partidos_web = [p for p in partidos_web if p["fecha"] >= hoy.isoformat()] or partidos_web
            print(f"    openfootball aún no publica {etiqueta(actual)}; "
                  f"se conserva el calendario guardado ({len(partidos_web)} partidos)")
        else:
            partidos_web, ascendidos = [], []
            print("    sin calendario disponible")

        # Etiquetas de procedencia, para que la web pueda decir de qué temporada
        # sale cada cifra en lugar de un vago «temporada anterior».
        if referencia is p_act and p_act:
            temp_fuerzas = f"{etiqueta(anterior)} y {etiqueta(actual)}"
            temp_hist = etiqueta(actual)
            jornadas = f" · {len(p_act)} partidos jugados"
        else:
            temp_fuerzas = etiqueta(anterior)
            temp_hist = etiqueta(anterior)
            jornadas = ""

        pca = componentes_principales(equipos)
        historico = resumen_historico(cod_us, actual)
        if historico:
            print(f"    {len(historico)} temporadas de histórico · "
                  f"último campeón: {historico[-1]['campeon']}")

        # Predicción de temporada: se juega el año entero muchas veces con los
        # equipos que aparecen en el calendario, no con los del año pasado.
        del_calendario = {p["l"] for p in partidos_web} | {p["v"] for p in partidos_web}
        plantel = {k: v for k, v in equipos.items()
                   if not del_calendario or k in del_calendario}
        descensos = 3 if len(plantel) >= 18 else 2
        pron = simular.simular_liga(plantel, gamma, RHO, descensos=descensos)
        if pron:
            campeon = pron[0]
            print(f"    favorito al título: {campeon['nombre']} "
                  f"({campeon['titulo']:.0f} %)")

        # Historial verificable: qué habría dicho el modelo antes de cada
        # partido ya jugado de la temporada de referencia.
        previos = [m for m in partidos if m not in referencia]
        hist_aciertos = mod_aciertos.historial_aciertos(
            previos, referencia, XI, RHO, BONITO)
        if hist_aciertos:
            print(f"    acierto histórico: {hist_aciertos['pct']:.1f} % "
                  f"en {hist_aciertos['n']} partidos")

        # Registro público: se apunta lo que el modelo dice HOY de cada partido
        # por jugar, y se anota el resultado de los que ya se jugaron. Un
        # pronóstico guardado no se toca nunca más.
        for pw in partidos_web:
            el, ev = equipos.get(pw["l"]), equipos.get(pw["v"])
            if not el or not ev or el.get("atq") is None or ev.get("atq") is None:
                continue
            lam = math.exp(el["atq"] - ev["def"] + gamma)
            mu = math.exp(ev["atq"] - el["def"])
            pw["prob"] = mercados(lam, mu, RHO)
        reg_nuevos += mod_registro.anotar_pronosticos(
            reg, clave, nombre, partidos_web, equipos, normalizar)
        reg_resueltos += mod_registro.resolver(reg, clave, p_act, normalizar, mismo_equipo)
        for pw in partidos_web:
            pw.pop("prob", None)      # sólo hacía falta para el registro

        salida["ligas"][clave] = {
            "nombre": nombre, "pais": pais, "continente": "Europa",
            "pca": pca, "historico": historico,
            "pronostico": pron,
            "aciertos": hist_aciertos,
            "temp_fuerzas": temp_fuerzas,
            "temp_hist": temp_hist,
            # Qué temporadas puede elegir el usuario en la tabla, y cuánto
            # llevamos jugado de la nueva: con pocas jornadas hay que avisar.
            "temp_opciones": {"act": etiqueta(actual), "ant": etiqueta(anterior),
                              "ambas": f"{etiqueta(anterior)} + {etiqueta(actual)}"},
            "pj_act": len(p_act),
            "corte_act": corte_act,
            "temp_jug": etiqueta(actual) if fichas is f_act else etiqueta(anterior),
            "nota_temp": temp_hist + jornadas,
            "gamma": round(gamma, 5), "rho": RHO,
            "equipos": dict(sorted(equipos.items())),
            "partidos": partidos_web,
            "ascendidos": ascendidos,
        }
        print(f"    {len(equipos)} equipos · ventaja local {math.exp(gamma):.3f}x")

    # ── Ligas sin xG ───────────────────────────────────────────────────── #
    # Portugal, Países Bajos, Brasil y demás no tienen ocasiones de gol en
    # ninguna fuente abierta, así que el modelo se ajusta sobre los goles. El
    # resto del cálculo es idéntico porque los partidos llegan con la misma
    # forma; lo único que cambia es que se marcan con «sin_xg» para que la web
    # lo diga y no ofrezca lo que no puede.
    print("")
    print("Ligas sin xG")
    saltadas: list[str] = []
    for clave, cfg in mod_goles.LIGAS.items():
        nombre, pais = cfg["nombre"], cfg["pais"]
        partidos, anio = mod_goles.historial(clave, ANIOS_HISTORIA)
        if len(partidos) < 150:
            saltadas.append(f"{nombre} (pocos partidos)")
            continue

        temp_act = mod_goles.etiqueta_temporada(clave, anio)
        p_act = mod_goles.descargar(clave, anio)
        p_ant = mod_goles.descargar(clave, anio - 1)

        # El calendario puede estar en el archivo del año en curso —si la
        # temporada va por la mitad— o en el siguiente, si ya se publicó.
        futuros = mod_goles.calendario(clave, anio, hoy)
        anio_cal = anio
        if not futuros:
            siguiente = anio + 1
            futuros = mod_goles.calendario(clave, siguiente, hoy)
            if futuros:
                anio_cal = siguiente
        if not futuros:
            saltadas.append(nombre)
            continue

        atq, dfn, gamma = ajustar_fuerzas(partidos, hoy)
        referencia = p_act if len(p_act) >= 50 else (p_ant or p_act)
        ag = agregar(referencia)
        ag_act, ag_ant = agregar(p_act), agregar(p_ant)
        ag_ambas = agregar(p_ant + p_act)
        hist = historial(p_ant + p_act, set(atq), maximo=60)
        corte_act = min((m["datetime"][:10] for m in p_act), default="")

        pj_ventana: dict[str, int] = {}
        for m in partidos:
            for eq in (m["h"]["title"], m["a"]["title"]):
                pj_ventana[eq] = pj_ventana.get(eq, 0) + 1

        equipos = {}
        for e in atq:
            if e not in ag or pj_ventana.get(e, 0) < MIN_PARTIDOS:
                continue
            equipos[e] = {"nombre": BONITO.get(e, e), "clave": e, "nuevo": False,
                          "atq": round(atq[e], 5), "def": round(dfn[e], 5), **ag[e],
                          "jug": [], "hist": hist.get(e, []),
                          "temp": {"act": ag_act.get(e), "ant": ag_ant.get(e),
                                   "ambas": ag_ambas.get(e)}}

        indice = {normalizar(e): e for e in equipos}
        base = perfil_ascendido(equipos)
        partidos_web, sin_casar = [], set()
        for m in futuros:
            ids = []
            for bruto in (m["local"], m["visita"]):
                eq = emparejar(bruto, indice)
                if eq is None:
                    eq = bruto
                    if eq not in equipos:
                        equipos[eq] = {"nombre": BONITO.get(bruto, mod_fd.nombre_corto(bruto)),
                                       "clave": eq,
                                       "nuevo": True, "jug": [], **base}
                        sin_casar.add(bruto)
                ids.append(eq)
            partidos_web.append({"j": "", "fecha": m["fecha"], "hora": m["hora"],
                                 "utc": a_utc(m["fecha"], m["hora"], clave),
                                 "l": ids[0], "v": ids[1]})
        partidos_web = partidos_web[:60]

        del_calendario = {p["l"] for p in partidos_web} | {p["v"] for p in partidos_web}
        plantel = {k: v for k, v in equipos.items() if k in del_calendario}
        pron = simular.simular_liga(plantel, gamma, RHO,
                                    descensos=3 if len(plantel) >= 18 else 2)
        previos = [m for m in partidos if m not in referencia]
        hist_aciertos = mod_aciertos.historial_aciertos(
            previos, referencia, XI, RHO, BONITO)

        for pw in partidos_web:
            el, ev = equipos.get(pw["l"]), equipos.get(pw["v"])
            if not el or not ev or el.get("atq") is None or ev.get("atq") is None:
                continue
            lam = math.exp(el["atq"] - ev["def"] + gamma)
            mu = math.exp(ev["atq"] - el["def"])
            pw["prob"] = mercados(lam, mu, RHO)
        reg_nuevos += mod_registro.anotar_pronosticos(
            reg, clave, nombre, partidos_web, equipos, normalizar)
        reg_resueltos += mod_registro.resolver(reg, clave, p_act, normalizar, mismo_equipo)
        for pw in partidos_web:
            pw.pop("prob", None)

        salida["ligas"][clave] = {
            "nombre": nombre, "pais": pais, "sin_xg": True,
            "continente": cfg["continente"],
            "pca": componentes_principales(equipos), "historico": [],
            "pronostico": pron, "aciertos": hist_aciertos,
            "temp_fuerzas": temp_act, "temp_hist": temp_act, "temp_jug": "",
            "nota_temp": f"{len(p_act)} partidos jugados",
            "temp_opciones": {"act": temp_act,
                              "ant": mod_goles.etiqueta_temporada(clave, anio - 1),
                              "ambas": f"{mod_goles.etiqueta_temporada(clave, anio - 1)}"
                                       f" + {temp_act}"},
            "pj_act": len(p_act), "corte_act": corte_act,
            "gamma": round(gamma, 5), "rho": RHO,
            "equipos": equipos, "partidos": partidos_web,
            "ascendidos": sorted(sin_casar), "nivel_europeo": None,
        }
        print(f"    {nombre} ({pais}): {len(partidos)} partidos, "
              f"{len(equipos)} equipos, {len(partidos_web)} por jugar"
              + (f" · favorito {pron[0]['nombre']} ({pron[0]['titulo']:.0f} %)"
                 if pron else ""))

    if saltadas:
        print(f"    en espera de que la fuente publique su temporada: "
              f"{', '.join(saltadas)}")

    # ── Competiciones de football-data.org ─────────────────────────────── #
    # Las que openfootball no publica y sí trae esta fuente. Se calculan igual
    # que las anteriores: mismo modelo sobre goles, misma forma de partido.
    if mod_fd.disponible():
        print("")
        print("Competiciones de football-data.org")
        calendarios = mod_fd.calendarios(hoy)
        for clave, info in calendarios.items():
            partidos = mod_fd.historial(clave)
            if len(partidos) < 150 or not info["partidos"]:
                print(f"    {info['nombre']}: sin datos suficientes")
                continue

            atq, dfn, gamma = ajustar_fuerzas(partidos, hoy)
            corte = (hoy.replace(year=hoy.year - 1)).isoformat()
            referencia = [m for m in partidos if m["datetime"][:10] >= corte]
            ag = agregar(referencia or partidos)
            hist = historial(referencia or partidos, set(atq))

            pj_ventana: dict[str, int] = {}
            for m in partidos:
                for eq in (m["h"]["title"], m["a"]["title"]):
                    pj_ventana[eq] = pj_ventana.get(eq, 0) + 1

            equipos = {}
            for e in atq:
                if e not in ag or pj_ventana.get(e, 0) < MIN_PARTIDOS:
                    continue
                equipos[e] = {"nombre": BONITO.get(e, mod_fd.nombre_corto(e)),
                              "clave": e,
                              "nuevo": False, "atq": round(atq[e], 5),
                              "def": round(dfn[e], 5), **ag[e],
                              "jug": [], "hist": hist.get(e, [])}

            indice = {normalizar(e): e for e in equipos}
            base = perfil_ascendido(equipos)
            partidos_web, sin_casar = [], set()
            for m in info["partidos"]:
                ids = []
                for bruto in (m["local"], m["visita"]):
                    eq = emparejar(bruto, indice)
                    if eq is None:
                        eq = bruto
                        if eq not in equipos:
                            equipos[eq] = {"nombre": BONITO.get(bruto, bruto),
                                           "clave": eq, "nuevo": True,
                                           "jug": [], **base}
                            sin_casar.add(bruto)
                    ids.append(eq)
                partidos_web.append({"j": "", "fecha": m["fecha"],
                                     "hora": m["hora"], "utc": m["utc"],
                                     "l": ids[0], "v": ids[1]})
            partidos_web = partidos_web[:60]

            # Simular la temporada sólo tiene sentido en una liga: en una copa
            # por eliminatorias no hay clasificación general que predecir.
            pron = []
            if info.get("es_liga"):
                del_calendario = ({q["l"] for q in partidos_web}
                                  | {q["v"] for q in partidos_web})
                plantel = {k: v for k, v in equipos.items() if k in del_calendario}
                pron = simular.simular_liga(plantel, gamma, RHO,
                                            descensos=3 if len(plantel) >= 18 else 0)

            for pw in partidos_web:
                el, ev = equipos.get(pw["l"]), equipos.get(pw["v"])
                if not el or not ev or el.get("atq") is None or ev.get("atq") is None:
                    continue
                lam = math.exp(el["atq"] - ev["def"] + gamma)
                mu = math.exp(ev["atq"] - el["def"])
                pw["prob"] = mercados(lam, mu, RHO)
            reg_nuevos += mod_registro.anotar_pronosticos(
                reg, clave, info["nombre"], partidos_web, equipos, normalizar)
            reg_resueltos += mod_registro.resolver(
                reg, clave, referencia or partidos, normalizar, mismo_equipo)
            for pw in partidos_web:
                pw.pop("prob", None)

            salida["ligas"][clave] = {
                "nombre": info["nombre"], "pais": info["pais"], "sin_xg": True,
                "continente": info["continente"],
                "es_copa": not info.get("es_liga", True),
                "pca": componentes_principales(equipos), "historico": [],
                "pronostico": pron, "aciertos": {},
                "temp_fuerzas": "", "temp_hist": "", "temp_jug": "",
                "nota_temp": f"{len(partidos)} partidos de historial",
                "gamma": round(gamma, 5), "rho": RHO,
                "equipos": equipos, "partidos": partidos_web,
                "ascendidos": sorted(sin_casar), "nivel_europeo": None,
            }
            print(f"    {info['nombre']}: {len(partidos)} partidos, "
                  f"{len(equipos)} equipos, {len(partidos_web)} por jugar")
    else:
        print("")
        print("football-data.org: sin clave, se omite")

    # ── Competiciones europeas ─────────────────────────────────────────── #
    # Se calculan al final porque el desnivel entre ligas necesita las fuerzas
    # de todas ellas ya estimadas.
    print("")
    print("Competiciones europeas")
    temporadas_euro = [etiqueta(a) for a in
                       range(max(actual - ANIOS_GRAFICOS, PRIMER_ANIO), actual + 1)]
    partidos_euro = europa.recopilar(temporadas_euro)
    if partidos_euro:
        n_part = sum(len(v) for v in partidos_euro.values())
        print(f"    {n_part} partidos de {len(partidos_euro)} torneos-temporada")
        nivel = europa.estimar_nivel_ligas(partidos_euro, salida["ligas"])
        salida["europa"] = {
            "temporadas": temporadas_euro,
            "partidos": n_part,
            "nivel_ligas": nivel,
            "torneos": europa.resumen_torneos(partidos_euro),
            "paises": europa.rendimiento_por_pais(partidos_euro),
            "embudo": europa.embudo_por_pais(partidos_euro),
            "ediciones": len(partidos_euro),
            "equipos": europa.equipos_destacados(partidos_euro),
            "favoritos": simular.favoritos_europeos(
                salida["ligas"], nivel.get("log_niveles", {})),
        }
        # El desnivel se guarda también en cada liga, para poder comparar
        # equipos de competiciones distintas cuando haya calendario europeo.
        for clave_liga, factor in nivel["log_niveles"].items():
            if clave_liga in salida["ligas"]:
                salida["ligas"][clave_liga]["nivel_europeo"] = factor
        orden = sorted(nivel["niveles"].items(), key=lambda kv: -kv[1])
        print("    nivel relativo: " +
              " · ".join(f"{salida['ligas'][k]['nombre']} {v:.2f}"
                         for k, v in orden if k in salida["ligas"]))
    else:
        print("    sin datos europeos disponibles")
        if previo_europa:
            salida["europa"] = previo_europa

    # Escudos: los que el repositorio no tenga se quedan sin entrada y la web
    # les pinta su distintivo de colores.
    print("")
    print("Escudos")
    salida["escudos"] = mod_escudos.mapear(salida["ligas"])
    # football-data.org publica el escudo de cada equipo suyo. Es la única
    # fuente que cubre la Championship y los clubes sudamericanos, así que
    # rellena lo que el repositorio de logos y Wikidata dejan fuera.
    propios = mod_fd.escudos()
    if propios:
        # El emparejado no puede ser por el nombre tal cual: en la web los
        # clubes brasileños llevan su nombre en español —«Flamengo», «Bahía»—
        # y la fuente los escribe «CR Flamengo», «EC Bahia». Se compara por la
        # forma normalizada, que ya descarta siglas y acentos.
        indice_esc = {}
        for bruto, url in propios.items():
            indice_esc.setdefault(normalizar(bruto), url)

        puestos = 0
        for lg in salida["ligas"].values():
            for eq in lg["equipos"].values():
                if salida["escudos"].get(eq["nombre"]):
                    continue
                url = (propios.get(eq["nombre"])
                       or propios.get(eq["clave"])
                       or indice_esc.get(normalizar(eq["nombre"]))
                       or indice_esc.get(normalizar(eq["clave"])))
                if url:
                    salida["escudos"][eq["nombre"]] = url
                    puestos += 1
        print(f"    {puestos} escudos más desde football-data.org")
    total_eq = sum(len(lg["equipos"]) for lg in salida["ligas"].values())
    print(f"    {len(salida['escudos'])} de {total_eq} equipos con escudo")

    salida["estadios"] = mod_estadios.mapear(salida["ligas"])
    print(f"    {len(salida['estadios'])} equipos con foto de su estadio")

    salida["logos"] = mod_escudos.logos_competiciones()
    print(f"    {len(salida['logos'])} competiciones con logo")

    # Las fotos van en caché: aquí sólo se consultan los jugadores nuevos.
    salida["fotos"] = mod_fotos.mapear(salida["ligas"])
    print(f"    {len(salida['fotos'])} jugadores con foto")

    print("")
    print("Trayectoria de los jugadores")
    nombres_jug = {j["n"] for lg in salida["ligas"].values()
                   for e in lg["equipos"].values() for j in (e.get("jug") or [])}
    salida["trayectoria"] = trayectoria_jugadores(nombres_jug, actual)
    salida["campos_trayectoria"] = CAMPOS_TRAYECTORIA
    con_varias = sum(1 for v in salida["trayectoria"].values() if len(v) > 1)
    print(f"    {len(salida['trayectoria'])} jugadores con historial "
          f"({con_varias} con más de una temporada)")

    mod_registro.guardar(reg)
    salida["registro"] = mod_registro.resumen(reg)
    print("")
    print("Registro público de pronósticos")
    print(f"    {reg_nuevos} pronósticos nuevos · {reg_resueltos} resueltos ahora")
    r = salida["registro"]
    if r.get("n"):
        print(f"    acumulado: {r['aciertos']}/{r['n']} aciertos "
              f"({r['pct']} %) desde {r['desde']} · {r['pendientes']} por resolver")
    elif r.get("pendientes"):
        print(f"    {r['pendientes']} apuntados, ninguno jugado todavía")

    JSON_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
    print(f"\ndatos_ligas.json  {JSON_SALIDA.stat().st_size:,} bytes")

    plantilla = (WEB / "plantilla.html").read_text(encoding="utf-8")
    datos = JSON_SALIDA.read_text(encoding="utf-8")
    if "/*__DATOS__*/" not in plantilla:
        raise SystemExit("La plantilla no contiene el marcador /*__DATOS__*/")
    import re as _re
    cuerpo = plantilla.replace("/*__DATOS__*/", datos)
    m = _re.search(r"<title>(.*?)</title>", cuerpo)
    titulo = "Ventaja Local · Probabilidades y estadísticas de fútbol"
    if m:
        cuerpo = cuerpo.replace(m.group(0), "", 1)   # el título va en la cabecera
    (WEB / "index.html").write_text(envolver_html(cuerpo, titulo), encoding="utf-8")
    escribir_seo(hoy)
    print(f"index.html        {(WEB / 'index.html').stat().st_size:,} bytes")
    print("\nListo. Vuelve a publicar web/index.html para actualizar la página.")


if __name__ == "__main__":
    main()
