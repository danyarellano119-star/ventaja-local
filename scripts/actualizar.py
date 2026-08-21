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
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import europa
import simular
import aciertos as mod_aciertos
import escudos as mod_escudos
import fotos as mod_fotos

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


def normalizar(nombre: str) -> str:
    """Clave laxa para casar nombres entre fuentes distintas."""
    s = nombre.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        s = s.replace(a, b)
    for basura in [" fc", "fc ", " cf", "cf ", " afc", "afc ", " sv", " 1913",
                   " calcio", "1. ", " united", " city", "."]:
        s = s.replace(basura, " ")
    return " ".join(s.split())


def emparejar(nombre: str, candidatos: dict[str, str]) -> str | None:
    """Busca el equipo de Understat que corresponde a un nombre del calendario.

    Los calendarios recortan los nombres largos («Racing Sant» por «Racing
    Santander»), así que además de la coincidencia exacta se acepta que uno sea
    prefijo del otro o que compartan la primera palabra distintiva.
    """
    n = normalizar(nombre)
    if n in candidatos:
        return candidatos[n]

    for clave, original in candidatos.items():
        if clave.startswith(n) or n.startswith(clave) or (len(n) > 4 and n in clave):
            return original

    # Última pasada: misma palabra inicial y longitud parecida
    palabras = n.split()
    if palabras:
        primera = palabras[0]
        iguales = [o for c, o in candidatos.items()
                   if c.split() and c.split()[0] == primera and len(primera) > 3]
        if len(iguales) == 1:
            return iguales[0]
    return None


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
        _guardado = json.loads(JSON_SALIDA.read_text(encoding="utf-8"))
        previo = _guardado.get("ligas", {})
        previo_europa = _guardado.get("europa")

    salida = {"generado": hoy.isoformat(), "temporada": etiqueta(actual), "ligas": {}}

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

        # El historial se toma de la temporada más reciente con partidos
        hist = historial(referencia, set(atq))

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
                          "jug": plantillas.get(e, []), "hist": hist.get(e, [])}

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
                            equipos[eq] = {"nombre": bruto, "clave": eq, "nuevo": True, **base}
                            sin_casar.add(bruto)
                    ids.append(eq)
                futuros.append({"j": m["j"], "fecha": m["fecha"], "hora": m["hora"],
                                "l": ids[0], "v": ids[1]})
            futuros.sort(key=lambda p: (p["fecha"], p["hora"]))
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

        salida["ligas"][clave] = {
            "nombre": nombre, "pais": pais,
            "pca": pca, "historico": historico,
            "pronostico": pron,
            "aciertos": hist_aciertos,
            "temp_fuerzas": temp_fuerzas,
            "temp_hist": temp_hist,
            "temp_jug": etiqueta(actual) if fichas is f_act else etiqueta(anterior),
            "nota_temp": temp_hist + jornadas,
            "gamma": round(gamma, 5), "rho": RHO,
            "equipos": dict(sorted(equipos.items())),
            "partidos": partidos_web,
            "ascendidos": ascendidos,
        }
        print(f"    {len(equipos)} equipos · ventaja local {math.exp(gamma):.3f}x")

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
    total_eq = sum(len(lg["equipos"]) for lg in salida["ligas"].values())
    print(f"    {len(salida['escudos'])} de {total_eq} equipos con escudo")

    salida["logos"] = mod_escudos.logos_competiciones()
    print(f"    {len(salida['logos'])} competiciones con logo")

    # Las fotos van en caché: aquí sólo se consultan los jugadores nuevos.
    salida["fotos"] = mod_fotos.mapear(salida["ligas"])
    print(f"    {len(salida['fotos'])} jugadores con foto")

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
