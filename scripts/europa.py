"""Competiciones europeas: Champions, Europa League y Conference.

Understat no cubre los torneos continentales, así que los resultados vienen del
repositorio abierto ``openfootball/champions-league`` en formato Football.TXT.

Estos partidos sirven para dos cosas:

1. **Calibrar el desnivel entre ligas.** Las fuerzas de cada equipo se estiman
   dentro de su propia liga, de modo que un +0,30 de ataque inglés y un +0,30
   español no son comparables. Los cruces europeos son los únicos partidos donde
   esas ligas se miden entre sí, y permiten estimar cuánto vale cada una.
2. **Resumir lo que ha pasado** en Europa las últimas temporadas.

Limitación conocida: sólo tenemos fuerzas de equipos de las cinco grandes ligas,
que son alrededor de un quinto de los participantes. Los partidos con equipos
portugueses, neerlandeses, escoceses o de cualquier otra liga quedan fuera del
modelo, aunque sí cuentan para los resúmenes.
"""

from __future__ import annotations

import re
import unicodedata

import numpy as np
import requests
from scipy.optimize import minimize
from scipy.stats import poisson

BASE = "https://cdn.jsdelivr.net/gh/openfootball/champions-league@master"

TORNEOS = {"cl": "Champions League", "el": "Europa League", "conf": "Conference League"}
PAIS_LIGA = {"ENG": "premier", "ESP": "laliga", "GER": "bundesliga",
             "ITA": "seriea", "FRA": "ligue1"}
PAIS_NOMBRE = {
    "ENG": "Inglaterra", "ESP": "España", "GER": "Alemania", "ITA": "Italia",
    "FRA": "Francia", "POR": "Portugal", "NED": "Países Bajos", "BEL": "Bélgica",
    "SCO": "Escocia", "TUR": "Turquía", "AUT": "Austria", "CZE": "Chequia",
    "GRE": "Grecia", "UKR": "Ucrania", "NOR": "Noruega", "SUI": "Suiza",
    "DEN": "Dinamarca", "CRO": "Croacia", "SRB": "Serbia", "POL": "Polonia",
    "ISR": "Israel", "CYP": "Chipre", "AZE": "Azerbaiyán", "SWE": "Suecia",
    "ROU": "Rumanía", "SVK": "Eslovaquia", "SVN": "Eslovenia", "HUN": "Hungría",
    "BUL": "Bulgaria", "KAZ": "Kazajistán", "IRL": "Irlanda", "FIN": "Finlandia",
}

_PARTIDO = re.compile(r"^\s*(?:(\d{2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s{2,}(\d+)-(\d+)")
_FASE = re.compile("^\\s*▪\\s*(.+)$")

# Palabras de relleno en los nombres largos que usa openfootball
_RUIDO = {"fc", "cf", "afc", "sk", "ac", "as", "ss", "ssc", "sc", "rc", "cd",
          "club", "atletico", "de", "the", "borussia", "olympique", "sport",
          "lisboa", "e", "calcio", "1899", "1846", "1904", "05", "04"}


def _clave(nombre: str) -> str:
    """Reduce un nombre de club a sus palabras distintivas, sin acentos."""
    s = unicodedata.normalize("NFKD", nombre.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(p for p in s.split() if p not in _RUIDO and len(p) > 1)


def _pais(texto: str):
    m = re.search(r"\(([A-Z]{3})\)\s*$", texto.strip())
    if not m:
        return None, None
    return re.sub(r"\s*\([A-Z]{3}\)\s*$", "", texto.strip()), m.group(1)


def descargar_torneo(temporada: str, codigo: str) -> list[dict]:
    """Partidos jugados de un torneo y temporada. Vacío si aún no existe."""
    try:
        r = requests.get(f"{BASE}/{temporada}/{codigo}.txt", timeout=45)
        if r.status_code != 200:
            return []
    except Exception:
        return []

    partidos, fase = [], ""
    for linea in r.text.splitlines():
        f = _FASE.match(linea)
        if f:
            fase = f.group(1).strip()
            continue
        m = _PARTIDO.match(linea)
        if not m:
            continue
        _, bruto_l, bruto_v, gl, gv = m.groups()
        local, pl = _pais(bruto_l)
        visita, pv = _pais(bruto_v)
        if not (local and visita):
            continue
        partidos.append({"fase": fase, "local": local, "visita": visita,
                         "pl": pl, "pv": pv, "gl": int(gl), "gv": int(gv)})
    return partidos


def recopilar(temporadas: list[str]) -> dict:
    """Descarga los tres torneos de varias temporadas."""
    salida = {}
    for temp in temporadas:
        for codigo in TORNEOS:
            partidos = descargar_torneo(temp, codigo)
            if partidos:
                salida[(temp, codigo)] = partidos
    return salida


def estimar_nivel_ligas(partidos_por_temp: dict, ligas: dict) -> dict:
    """Cuánto vale cada liga respecto a las demás, según los cruces europeos.

    Ajusta por máxima verosimilitud un desnivel por liga que, sumado a las
    fuerzas domésticas de cada equipo, explique los goles de los partidos entre
    equipos de ligas distintas.
    """
    indice = {}
    for lk, lg in ligas.items():
        for e in lg["equipos"].values():
            if not e.get("nuevo"):
                indice[(lk, _clave(e["clave"]))] = e

    datos = []
    for partidos in partidos_por_temp.values():
        for p in partidos:
            li, lj = PAIS_LIGA.get(p["pl"]), PAIS_LIGA.get(p["pv"])
            if not li or not lj or li == lj:
                continue
            a = indice.get((li, _clave(p["local"])))
            b = indice.get((lj, _clave(p["visita"])))
            if a and b:
                datos.append((li, lj, a, b, p["gl"], p["gv"]))

    claves = ["premier", "laliga", "bundesliga", "seriea", "ligue1"]
    if len(datos) < 60:
        return {"n": len(datos), "niveles": {k: 1.0 for k in claves},
                "log_niveles": {k: 0.0 for k in claves}, "gamma": None}

    idx = {l: i for i, l in enumerate(claves)}

    def neg_ll(x):
        niveles = x[:5] - x[:5].mean()
        gamma = x[5]
        total = 0.0
        for li, lj, a, b, gl, gv in datos:
            d = niveles[idx[li]] - niveles[idx[lj]]
            lam = np.exp(a["atq"] - b["def"] + d + gamma)
            mu = np.exp(b["atq"] - a["def"] - d)
            total += poisson.logpmf(gl, lam) + poisson.logpmf(gv, mu)
        return -total

    res = minimize(neg_ll, np.array([0., 0., 0., 0., 0., 0.25]),
                   method="Nelder-Mead",
                   options={"maxiter": 4000, "xatol": 1e-4, "fatol": 1e-4})
    niveles = res.x[:5] - res.x[:5].mean()

    return {
        "n": len(datos),
        "niveles": {k: round(float(np.exp(niveles[idx[k]])), 4) for k in claves},
        "log_niveles": {k: round(float(niveles[idx[k]]), 5) for k in claves},
        "gamma": round(float(res.x[5]), 5),
    }


def resumen_torneos(partidos_por_temp: dict) -> list[dict]:
    """Campeón, finalista y goles de cada torneo y temporada."""
    salida = []
    for (temp, codigo), partidos in sorted(partidos_por_temp.items()):
        if not partidos:
            continue
        finales = [p for p in partidos
                   if p["fase"].lower().endswith("final")
                   and "semi" not in p["fase"].lower()
                   and "quarter" not in p["fase"].lower()]
        campeon = finalista = None
        if finales:
            f = finales[-1]
            gano_local = f["gl"] >= f["gv"]
            campeon = (f["local"] if gano_local else f["visita"],
                       f["pl"] if gano_local else f["pv"])
            finalista = (f["visita"] if gano_local else f["local"],
                         f["pv"] if gano_local else f["pl"])

        goles = sum(p["gl"] + p["gv"] for p in partidos)
        salida.append({
            "temp": temp, "torneo": TORNEOS[codigo], "codigo": codigo,
            "partidos": len(partidos), "goles": goles,
            "goles_partido": round(goles / len(partidos), 2),
            "campeon": campeon[0] if campeon else None,
            "campeon_pais": campeon[1] if campeon else None,
            "finalista": finalista[0] if finalista else None,
            "finalista_pais": finalista[1] if finalista else None,
        })
    return salida


def rendimiento_por_pais(partidos_por_temp: dict, minimo: int = 25) -> list[dict]:
    """Balance europeo de cada país: partidos, victorias y goles."""
    acc = {}
    for partidos in partidos_por_temp.values():
        for p in partidos:
            for pais, gf, gc in ((p["pl"], p["gl"], p["gv"]),
                                 (p["pv"], p["gv"], p["gl"])):
                c = acc.setdefault(pais, {"pj": 0, "v": 0, "e": 0, "d": 0,
                                          "gf": 0, "gc": 0})
                c["pj"] += 1
                c["gf"] += gf
                c["gc"] += gc
                c["v" if gf > gc else "e" if gf == gc else "d"] += 1

    salida = []
    for pais, c in acc.items():
        if c["pj"] < minimo:
            continue
        salida.append({
            "pais": pais, "nombre": PAIS_NOMBRE.get(pais, pais),
            "pj": c["pj"], "v": c["v"], "e": c["e"], "d": c["d"],
            "gf": c["gf"], "gc": c["gc"],
            "pct_v": round(c["v"] / c["pj"] * 100, 1),
            "pts_partido": round((c["v"] * 3 + c["e"]) / c["pj"], 2),
        })
    return sorted(salida, key=lambda x: -x["pts_partido"])


def _etapa(fase: str) -> str:
    """Clasifica la fase de un partido en una de las cuatro etapas del torneo.

    openfootball nombra la fase eliminatoria completa como «Finals», de modo que
    hay rondas llamadas «Finals, Round of 16» o «Finals, Semifinals». Sólo cuenta
    la parte posterior a la coma, que es la ronda de verdad.
    """
    f = fase.lower().split(",")[-1].strip()
    if f in ("final", "finals"):
        return "final"
    if "semifinal" in f:
        return "semifinal"
    if "quarterfinal" in f or "round of 16" in f or "playoff" in f:
        return "eliminatoria"
    return "regular"


def embudo_por_pais(partidos_por_temp: dict, minimo: int = 40) -> list[dict]:
    """Compara el rendimiento sostenido de cada país con los títulos que gana.

    Un país puede dominar la fase larga —donde se juegan la mayoría de los
    partidos y el mejor acaba imponiéndose— y aun así quedarse sin títulos,
    porque una final se decide en uno o dos partidos y ahí manda el azar. Esta
    función mide las dos cosas por separado para poder enfrentarlas.
    """
    reg = {}          # rendimiento en fase de grupos o liga
    llegadas = {}     # equipos distintos que alcanzan cada etapa, por edición
    titulos = {}

    for (temp, codigo), partidos in partidos_por_temp.items():
        vistos = {"semifinal": set(), "final": set(), "eliminatoria": set()}
        for p in partidos:
            etapa = _etapa(p["fase"])
            for pais, gf, gc, equipo in ((p["pl"], p["gl"], p["gv"], p["local"]),
                                         (p["pv"], p["gv"], p["gl"], p["visita"])):
                if etapa == "regular":
                    c = reg.setdefault(pais, {"pj": 0, "v": 0, "e": 0})
                    c["pj"] += 1
                    if gf > gc:
                        c["v"] += 1
                    elif gf == gc:
                        c["e"] += 1
                else:
                    vistos[etapa].add((pais, equipo))

            if etapa == "final":
                gano = p["pl"] if p["gl"] >= p["gv"] else p["pv"]
                titulos[gano] = titulos.get(gano, 0) + 1

        for etapa, conjunto in vistos.items():
            for pais, _ in conjunto:
                llegadas.setdefault(pais, {"eliminatoria": 0, "semifinal": 0,
                                           "final": 0})[etapa] += 1

    ediciones = len(partidos_por_temp)
    salida = []
    for pais, c in reg.items():
        if c["pj"] < minimo:
            continue
        ll = llegadas.get(pais, {"eliminatoria": 0, "semifinal": 0, "final": 0})
        salida.append({
            "pais": pais, "nombre": PAIS_NOMBRE.get(pais, pais),
            "pj_regular": c["pj"],
            "pct_v_regular": round(c["v"] / c["pj"] * 100, 1),
            "pts_regular": round((c["v"] * 3 + c["e"]) / c["pj"], 2),
            "eliminatorias": ll["eliminatoria"],
            "semifinales": ll["semifinal"],
            "finales": ll["final"],
            "titulos": titulos.get(pais, 0),
            "titulos_por_edicion": round(titulos.get(pais, 0) / max(ediciones, 1), 3),
            # Qué porcentaje de las finales que juega acaba ganando: la métrica
            # que separa a quien llega mucho de quien remata.
            "pct_finales": (round(titulos.get(pais, 0) / ll["final"] * 100, 1)
                            if ll["final"] else None),
        })
    return sorted(salida, key=lambda x: -x["pts_regular"])


def equipos_destacados(partidos_por_temp: dict, tope: int = 12) -> list[dict]:
    """Los clubes con mejor balance europeo en el periodo analizado."""
    acc = {}
    for partidos in partidos_por_temp.values():
        for p in partidos:
            for nombre, pais, gf, gc in ((p["local"], p["pl"], p["gl"], p["gv"]),
                                         (p["visita"], p["pv"], p["gv"], p["gl"])):
                c = acc.setdefault(nombre, {"pais": pais, "pj": 0, "v": 0,
                                            "e": 0, "gf": 0, "gc": 0})
                c["pj"] += 1
                c["gf"] += gf
                c["gc"] += gc
                if gf > gc:
                    c["v"] += 1
                elif gf == gc:
                    c["e"] += 1

    filas = [{"equipo": n, "pais": c["pais"],
              "nombre_pais": PAIS_NOMBRE.get(c["pais"], c["pais"]),
              "pj": c["pj"], "v": c["v"], "gf": c["gf"], "gc": c["gc"],
              "pts_partido": round((c["v"] * 3 + c["e"]) / c["pj"], 2)}
             for n, c in acc.items() if c["pj"] >= 20]
    return sorted(filas, key=lambda x: (-x["pts_partido"], -x["pj"]))[:tope]
