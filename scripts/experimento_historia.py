"""¿Mejora el modelo con más años de historia? Se mide en lugar de suponerlo.

Compara ventanas de entrenamiento de distinta profundidad (una temporada frente
a doce) y velocidades de olvido, siempre sobre los mismos partidos de validación
y prediciendo cada uno sólo con lo anterior a su fecha.

    python scripts/experimento_historia.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parent))
from actualizar import bajar_understat

RAIZ = Path(__file__).resolve().parent.parent
CACHE = RAIZ / "datos" / "understat_historico.csv"

MAXG = 8
RHO = -0.109


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #

def cargar(ligas=("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1"),
           desde=2014, hasta=2026) -> pd.DataFrame:
    """Descarga (y cachea) todas las temporadas con xG disponibles."""
    if CACHE.exists():
        return pd.read_csv(CACHE, parse_dates=["fecha"])

    filas = []
    for liga in ligas:
        for anio in range(desde, hasta + 1):
            partidos, _ = bajar_understat(liga, anio)
            for m in partidos:
                filas.append({
                    "liga": liga, "temporada": anio,
                    "fecha": pd.to_datetime(m["datetime"][:10]),
                    "local": m["h"]["title"], "visitante": m["a"]["title"],
                    "gl": int(m["goals"]["h"]), "gv": int(m["goals"]["a"]),
                    "xl": float(m["xG"]["h"]), "xv": float(m["xG"]["a"]),
                })
            print(f"  {liga:12s} {anio}: {len(partidos):3d}")
    df = pd.DataFrame(filas).sort_values("fecha").reset_index(drop=True)
    CACHE.parent.mkdir(exist_ok=True)
    df.to_csv(CACHE, index=False, encoding="utf-8")
    return df


# --------------------------------------------------------------------------- #
# Ajuste vectorizado
# --------------------------------------------------------------------------- #

def ajustar(df: pd.DataFrame, referencia, xi: float, iteraciones: int = 120):
    """Fuerzas de ataque y defensa sobre log(xG), por iteración alternada.

    Versión vectorizada con numpy: sobre miles de partidos, la implementación
    equipo a equipo tardaría minutos por ajuste y aquí hacen falta cientos.
    """
    equipos = pd.unique(pd.concat([df["local"], df["visitante"]]))
    idx = {e: i for i, e in enumerate(equipos)}
    n = len(equipos)

    il = df["local"].map(idx).to_numpy()
    iv = df["visitante"].map(idx).to_numpy()
    dias = (referencia - df["fecha"]).dt.days.to_numpy().clip(min=0)
    w = np.exp(-xi * dias)

    yl = np.log(np.clip(df["xl"].to_numpy(), 0.05, None))
    yv = np.log(np.clip(df["xv"].to_numpy(), 0.05, None))

    # Cada partido aporta dos observaciones: el ataque del local contra la
    # defensa del visitante (con ventaja de campo) y la simétrica sin ella.
    atk = np.concatenate([il, iv])
    dfn = np.concatenate([iv, il])
    y = np.concatenate([yl, yv])
    loc = np.concatenate([np.ones(len(df)), np.zeros(len(df))])
    pesos = np.concatenate([w, w])

    a = np.zeros(n)
    d = np.zeros(n)
    gamma = 0.25

    suma_atk = np.bincount(atk, weights=pesos, minlength=n)
    suma_dfn = np.bincount(dfn, weights=pesos, minlength=n)
    suma_atk[suma_atk == 0] = 1e-9
    suma_dfn[suma_dfn == 0] = 1e-9
    peso_local = pesos[loc == 1].sum()

    for _ in range(iteraciones):
        res = y - a[atk] + d[dfn]
        gamma = float((pesos * res * loc).sum() / peso_local)

        num = np.bincount(atk, weights=pesos * (y + d[dfn] - gamma * loc), minlength=n)
        a = num / suma_atk
        a -= a.mean()

        num = np.bincount(dfn, weights=pesos * (a[atk] - y + gamma * loc), minlength=n)
        d = num / suma_dfn

    return {e: (float(a[i]), float(d[i])) for e, i in idx.items()}, gamma


def probabilidades(lam, mu):
    """Reparte la probabilidad entre victoria local, empate y visitante."""
    g = np.arange(MAXG + 1)
    m = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    m[0, 0] *= 1 - lam * mu * RHO
    m[0, 1] *= 1 + lam * RHO
    m[1, 0] *= 1 + mu * RHO
    m[1, 1] *= 1 - RHO
    m /= m.sum()
    return np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #

def backtest(df: pd.DataFrame, inicio_val, anios_historia, xi, paso=25):
    """Predice los partidos desde `inicio_val` usando sólo el pasado.

    `anios_historia` limita cuántos años hacia atrás se miran; None usa todo.
    """
    val = df[df["fecha"] >= inicio_val].reset_index(drop=True)
    resultados, fuerzas, gamma, ultimo_ajuste = [], None, None, -1

    for i, fila in val.iterrows():
        if i - ultimo_ajuste >= paso or fuerzas is None:
            historico = df[df["fecha"] < fila["fecha"]]
            if anios_historia is not None:
                corte = fila["fecha"] - pd.Timedelta(days=365 * anios_historia)
                historico = historico[historico["fecha"] >= corte]
            if len(historico) < 200:
                continue
            fuerzas, gamma = ajustar(historico, fila["fecha"], xi)
            ultimo_ajuste = i

        l, v = fila["local"], fila["visitante"]
        if l not in fuerzas or v not in fuerzas:
            continue
        al, dl = fuerzas[l]
        av, dv = fuerzas[v]
        pl, pe, pv = probabilidades(np.exp(al - dv + gamma), np.exp(av - dl))
        real = 0 if fila["gl"] > fila["gv"] else 1 if fila["gl"] == fila["gv"] else 2
        resultados.append((pl, pe, pv, real))

    if not resultados:
        return None
    P = np.array([[r[0], r[1], r[2]] for r in resultados])
    y = np.array([r[3] for r in resultados])
    ll = -np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-12, 1)))
    brier = np.mean(np.sum((P - np.eye(3)[y]) ** 2, axis=1))
    acierto = np.mean(P.argmax(axis=1) == y)
    return {"n": len(y), "log_loss": ll, "brier": brier, "acierto": acierto * 100}


def main():
    print("Cargando datos (la primera vez descarga y tarda unos minutos)...")
    df = cargar()
    print(f"{len(df):,} partidos · {df['fecha'].min():%Y-%m-%d} a {df['fecha'].max():%Y-%m-%d}")
    print(f"{df['liga'].nunique()} ligas · {df['temporada'].nunique()} temporadas\n")

    # Se valida sobre las dos últimas temporadas completas de todas las ligas
    inicio_val = pd.Timestamp("2024-08-01")

    print("=" * 74)
    print("EXPERIMENTO 1 · ¿Cuántos años de historia conviene mirar?")
    print("=" * 74)
    print(f"  {'Historia':<22s} {'LogLoss':>9s} {'Brier':>8s} {'Acierto':>9s} {'seg':>6s}")

    XI_BASE = 0.0018
    resultados = {}
    for etiqueta, anios in [("1 temporada", 1), ("2 temporadas", 2),
                            ("4 temporadas", 4), ("8 temporadas", 8),
                            ("todo (12 temp.)", None)]:
        t0 = time.time()
        r = backtest(df, inicio_val, anios, XI_BASE)
        if r:
            resultados[etiqueta] = r
            print(f"  {etiqueta:<22s} {r['log_loss']:>9.4f} {r['brier']:>8.4f} "
                  f"{r['acierto']:>8.1f}% {time.time() - t0:>6.0f}")

    mejor = min(resultados, key=lambda k: resultados[k]["log_loss"])
    print(f"\n  Mejor: {mejor}")

    print()
    print("=" * 74)
    print("EXPERIMENTO 2 · ¿Cuánto conviene olvidar el pasado?")
    print("=" * 74)
    print(f"  {'xi':<10s} {'vida media':<14s} {'LogLoss':>9s} {'Brier':>8s} {'Acierto':>9s}")

    mejor_xi = None
    for xi in [0.0, 0.0010, 0.0018, 0.0030, 0.0045, 0.0060, 0.0090]:
        r = backtest(df, inicio_val, None, xi)
        if not r:
            continue
        vida = "sin olvido" if xi == 0 else f"{np.log(2) / xi:,.0f} días"
        print(f"  {xi:<10.4f} {vida:<14s} {r['log_loss']:>9.4f} {r['brier']:>8.4f} "
              f"{r['acierto']:>8.1f}%")
        if mejor_xi is None or r["log_loss"] < mejor_xi[1]:
            mejor_xi = (xi, r["log_loss"])

    print(f"\n  Mejor xi: {mejor_xi[0]} (log-loss {mejor_xi[1]:.4f})")


if __name__ == "__main__":
    main()
