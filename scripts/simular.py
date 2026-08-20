"""Simula la temporada completa para estimar quién gana la liga.

Un partido suelto se resuelve con la matriz de marcadores, pero una temporada
entera no: hay que jugarla. Se sortean los 380 partidos miles de veces con las
fuerzas del modelo y se cuenta en cuántas de esas temporadas cada equipo termina
campeón, entre los cuatro primeros o descendido.

Las probabilidades que salen son las que están detrás de los mercados de
«ganador de liga» o «clasificación a Champions», así que sirven para comparar
con lo que ofrece una casa de apuestas.
"""

from __future__ import annotations

import numpy as np

# Número de temporadas simuladas: con 10.000 el error de muestreo baja del 0,5 %
SIMULACIONES = 10000


def _matriz_goles(lam, mu, rho, maxg=8):
    """Probabilidad acumulada de cada marcador, para poder sortearlo."""
    from scipy.stats import poisson
    g = np.arange(maxg + 1)
    m = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    m[0, 0] *= 1 - lam * mu * rho
    m[0, 1] *= 1 + lam * rho
    m[1, 0] *= 1 + mu * rho
    m[1, 1] *= 1 - rho
    return m / m.sum()


def simular_liga(equipos: dict, gamma: float, rho: float,
                 plazas_europa: int = 4, descensos: int = 3,
                 simulaciones: int = SIMULACIONES, semilla: int = 7) -> list[dict]:
    """Juega la temporada muchas veces y cuenta cómo termina cada equipo.

    Se asume el formato habitual: todos contra todos, ida y vuelta. No hace
    falta el calendario real porque el orden de los partidos no cambia la
    clasificación final.

    Args:
        equipos: los de la liga, con sus fuerzas de ataque y defensa.
        gamma: ventaja de jugar en casa, en logaritmos.
        rho: corrección de marcadores bajos.
        plazas_europa: cuántos puestos dan acceso a Champions.
        descensos: cuántos equipos bajan.
    """
    claves = [k for k, e in equipos.items() if not e.get("nuevo") or e.get("atq") is not None]
    claves = [k for k in claves if equipos[k].get("atq") is not None]
    n = len(claves)
    if n < 6:
        return []

    rng = np.random.default_rng(semilla)
    atq = np.array([equipos[k]["atq"] for k in claves])
    dfn = np.array([equipos[k]["def"] for k in claves])

    # Se precalculan los marcadores posibles de cada emparejamiento: sortear de
    # una distribución ya construida es mucho más rápido que recalcularla.
    maxg = 8
    n_marcadores = (maxg + 1) ** 2
    acum = np.zeros((n, n, n_marcadores))
    puntos_l = np.zeros(n_marcadores, dtype=np.int8)
    puntos_v = np.zeros(n_marcadores, dtype=np.int8)
    dif = np.zeros(n_marcadores, dtype=np.int8)
    gf_l = np.zeros(n_marcadores, dtype=np.int8)

    for idx in range(n_marcadores):
        i, j = divmod(idx, maxg + 1)
        puntos_l[idx] = 3 if i > j else 1 if i == j else 0
        puntos_v[idx] = 3 if j > i else 1 if i == j else 0
        dif[idx] = i - j
        gf_l[idx] = i

    for a in range(n):
        for b in range(n):
            if a == b:
                continue
            lam = np.exp(atq[a] - dfn[b] + gamma)
            mu = np.exp(atq[b] - dfn[a])
            acum[a, b] = np.cumsum(_matriz_goles(lam, mu, rho, maxg).ravel())

    titulos = np.zeros(n)
    europa = np.zeros(n)
    descenso = np.zeros(n)
    puntos_tot = np.zeros(n)
    posiciones = np.zeros(n)

    for _ in range(simulaciones):
        pts = np.zeros(n)
        difgol = np.zeros(n)
        goles = np.zeros(n)

        for a in range(n):
            for b in range(n):
                if a == b:
                    continue
                idx = int(np.searchsorted(acum[a, b], rng.random()))
                idx = min(idx, n_marcadores - 1)
                pts[a] += puntos_l[idx]
                pts[b] += puntos_v[idx]
                difgol[a] += dif[idx]
                difgol[b] -= dif[idx]
                goles[a] += gf_l[idx]

        # Orden final: puntos, luego diferencia de goles, luego goles a favor
        orden = np.lexsort((-goles, -difgol, -pts))
        titulos[orden[0]] += 1
        europa[orden[:plazas_europa]] += 1
        if descensos:
            descenso[orden[-descensos:]] += 1
        puntos_tot += pts
        posiciones[orden] += np.arange(1, n + 1)

    return sorted([
        {
            "clave": claves[i],
            "nombre": equipos[claves[i]]["nombre"],
            "titulo": round(titulos[i] / simulaciones * 100, 1),
            "europa": round(europa[i] / simulaciones * 100, 1),
            "descenso": round(descenso[i] / simulaciones * 100, 1),
            "pts_esperados": round(puntos_tot[i] / simulaciones, 1),
            "pos_media": round(posiciones[i] / simulaciones, 1),
        }
        for i in range(n)
    ], key=lambda x: -x["pts_esperados"])


def favoritos_europeos(ligas: dict, niveles: dict, tope: int = 15) -> list[dict]:
    """Ordena los mejores clubes de las cinco grandes ligas en escala común.

    Las fuerzas están centradas dentro de cada liga, así que para compararlas
    entre países hay que sumarles el desnivel estimado con los cruces europeos.
    """
    filas = []
    for clave_liga, lg in ligas.items():
        nivel = (niveles or {}).get(clave_liga, 0.0)
        for e in lg["equipos"].values():
            if e.get("nuevo") or e.get("atq") is None:
                continue
            filas.append({
                "nombre": e["nombre"],
                "liga": lg["nombre"],
                "clave_liga": clave_liga,
                "clave": e["clave"],
                # Fuerza total: lo que genera más lo que evita, ya en escala europea
                "fuerza": round(e["atq"] + e["def"] + 2 * nivel, 4),
                "atq": round(np.exp(e["atq"] + nivel), 3),
                "def": round(np.exp(-(e["def"] + nivel)), 3),
            })
    filas.sort(key=lambda x: -x["fuerza"])
    return filas[:tope]
