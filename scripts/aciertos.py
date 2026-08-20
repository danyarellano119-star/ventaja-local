"""Historial verificable: qué predijo el modelo y qué pasó de verdad.

Para que alguien confíe en un pronóstico tiene que poder comprobarlo. Aquí se
reconstruye, partido a partido, lo que el modelo habría dicho **antes** de cada
encuentro —usando sólo información anterior a esa fecha— y se compara con el
resultado real.

No es una simulación optimista: es exactamente el mismo procedimiento que se usa
para predecir los partidos futuros, aplicado hacia atrás.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np


def _ajustar(partidos, referencia, xi, iteraciones=90):
    """Fuerzas de ataque y defensa a una fecha dada, en versión ligera."""
    equipos = sorted({m["h"]["title"] for m in partidos} |
                     {m["a"]["title"] for m in partidos})
    idx = {e: i for i, e in enumerate(equipos)}
    n = len(equipos)
    if n < 4:
        return None, None

    il = np.array([idx[m["h"]["title"]] for m in partidos])
    iv = np.array([idx[m["a"]["title"]] for m in partidos])
    dias = np.array([(referencia - datetime.strptime(m["datetime"][:10],
                                                     "%Y-%m-%d").date()).days
                     for m in partidos]).clip(min=0)
    w = np.exp(-xi * dias)

    yl = np.log(np.clip([float(m["xG"]["h"]) for m in partidos], 0.05, None))
    yv = np.log(np.clip([float(m["xG"]["a"]) for m in partidos], 0.05, None))

    atk = np.concatenate([il, iv])
    dfn = np.concatenate([iv, il])
    y = np.concatenate([yl, yv])
    loc = np.concatenate([np.ones(len(partidos)), np.zeros(len(partidos))])
    pesos = np.concatenate([w, w])

    a, d, gamma = np.zeros(n), np.zeros(n), 0.25
    s_atk = np.bincount(atk, weights=pesos, minlength=n)
    s_dfn = np.bincount(dfn, weights=pesos, minlength=n)
    s_atk[s_atk == 0] = 1e-9
    s_dfn[s_dfn == 0] = 1e-9
    peso_local = pesos[loc == 1].sum() or 1e-9

    for _ in range(iteraciones):
        gamma = float((pesos * (y - a[atk] + d[dfn]) * loc).sum() / peso_local)
        a = np.bincount(atk, weights=pesos * (y + d[dfn] - gamma * loc),
                        minlength=n) / s_atk
        a -= a.mean()
        d = np.bincount(dfn, weights=pesos * (a[atk] - y + gamma * loc),
                        minlength=n) / s_dfn

    return {e: (float(a[i]), float(d[i])) for e, i in idx.items()}, gamma


def _prob_1x2(lam, mu, rho, maxg=8):
    """Probabilidad de victoria local, empate y victoria visitante."""
    def pois(k, l):
        return math.exp(-l + k * math.log(l) - math.lgamma(k + 1))

    m = [[pois(i, lam) * pois(j, mu) for j in range(maxg + 1)]
         for i in range(maxg + 1)]
    m[0][0] *= 1 - lam * mu * rho
    m[0][1] *= 1 + lam * rho
    m[1][0] *= 1 + mu * rho
    m[1][1] *= 1 - rho
    total = sum(sum(f) for f in m)

    pl = sum(m[i][j] for i in range(maxg + 1) for j in range(i)) / total
    pe = sum(m[i][i] for i in range(maxg + 1)) / total
    return pl, pe, 1 - pl - pe


def historial_aciertos(partidos_previos: list, partidos_temporada: list,
                       xi: float, rho: float, bonito: dict,
                       paso: int = 10, minimo: int = 200) -> dict:
    """Predice cada partido de la temporada con lo que se sabía antes de jugarlo.

    Args:
        partidos_previos: temporadas anteriores, el punto de partida del modelo.
        partidos_temporada: los partidos a evaluar, en orden.
        paso: cada cuántos partidos se reajustan las fuerzas.
        minimo: partidos necesarios antes de empezar a predecir.
    """
    if not partidos_temporada:
        return {}

    orden = sorted(partidos_temporada, key=lambda m: m["datetime"])
    fuerzas = gamma = None
    ultimo = -paso - 1
    filas = []

    for i, m in enumerate(orden):
        fecha = datetime.strptime(m["datetime"][:10], "%Y-%m-%d").date()
        if fuerzas is None or i - ultimo >= paso:
            historico = partidos_previos + orden[:i]
            if len(historico) < minimo:
                continue
            fuerzas, gamma = _ajustar(historico, fecha, xi)
            ultimo = i
        if not fuerzas:
            continue

        h, a = m["h"]["title"], m["a"]["title"]
        if h not in fuerzas or a not in fuerzas:
            continue

        al, dl = fuerzas[h]
        av, dv = fuerzas[a]
        pl, pe, pv = _prob_1x2(math.exp(al - dv + gamma), math.exp(av - dl), rho)

        gh, ga = int(m["goals"]["h"]), int(m["goals"]["a"])
        real = "L" if gh > ga else "E" if gh == ga else "V"
        probs = {"L": pl, "E": pe, "V": pv}
        favorito = max(probs, key=probs.get)

        filas.append({
            "f": m["datetime"][:10],
            "l": bonito.get(h, h), "v": bonito.get(a, a),
            "gl": gh, "gv": ga,
            "pl": round(pl, 3), "pe": round(pe, 3), "pv": round(pv, 3),
            "real": real, "ok": favorito == real,
            "p_dada": round(probs[real], 3),
        })

    if not filas:
        return {}

    n = len(filas)
    aciertos = sum(1 for f in filas if f["ok"])

    # Calibración por tramos: de los partidos donde dijo entre 50 % y 60 %,
    # ¿cuántos ocurrieron realmente?
    tramos = []
    for lo, hi in [(0.0, 0.30), (0.30, 0.45), (0.45, 0.60), (0.60, 0.75), (0.75, 1.01)]:
        casos = []
        for f in filas:
            for clave, p in (("L", f["pl"]), ("E", f["pe"]), ("V", f["pv"])):
                if lo <= p < hi:
                    casos.append((p, f["real"] == clave))
        if len(casos) >= 15:
            tramos.append({
                "desde": round(lo * 100), "hasta": round(min(hi, 1.0) * 100),
                "n": len(casos),
                "dicho": round(sum(p for p, _ in casos) / len(casos) * 100, 1),
                "real": round(sum(1 for _, ok in casos if ok) / len(casos) * 100, 1),
            })

    return {
        "n": n,
        "aciertos": aciertos,
        "pct": round(aciertos / n * 100, 1),
        "tramos": tramos,
        # Los últimos partidos, para poder mirarlos uno a uno
        "ultimos": filas[-40:][::-1],
    }
