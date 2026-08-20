"""Prueba variantes del modelo para ver cuáles rinden más.

Parte de lo aprendido en `experimento_historia.py` —cuatro años de historia y un
olvido de xi=0,003— y mide tres ideas encima:

1. **Mezclar xG con goles.** El xG describe mejor el juego, pero los goles son
   lo que finalmente cuenta; una mezcla puede batir a cualquiera de los dos.
2. **Encoger las fuerzas hacia la media.** Un equipo con pocos partidos recibe
   estimaciones exageradas; acercarlas a la media evita pasarse.
3. **Ventaja de campo propia de cada equipo.** No todos los estadios pesan igual.

    python scripts/experimento_mejoras.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parent))
from experimento_historia import MAXG, RHO, cargar, probabilidades

XI = 0.0030
ANIOS = 4


def ajustar(df, referencia, xi=XI, iteraciones=120, peso_xg=1.0,
            shrink=0.0, gamma_equipo=False):
    """Estima fuerzas de ataque y defensa con las variantes a comparar.

    Args:
        peso_xg: 1,0 usa sólo xG; 0,0 sólo goles; los valores intermedios mezclan.
        shrink: fuerza del encogimiento hacia la media. 0 lo desactiva; con
            valores mayores, los equipos con pocos partidos quedan más cerca del
            promedio de la liga.
        gamma_equipo: si es True, cada equipo tiene su propia ventaja de campo,
            encogida hacia la media general para que no se dispare.
    """
    equipos = pd.unique(pd.concat([df["local"], df["visitante"]]))
    idx = {e: i for i, e in enumerate(equipos)}
    n = len(equipos)

    il = df["local"].map(idx).to_numpy()
    iv = df["visitante"].map(idx).to_numpy()
    dias = (referencia - df["fecha"]).dt.days.to_numpy().clip(min=0)
    w = np.exp(-xi * dias)

    # Mezcla de xG y goles antes de pasar a logaritmos
    ml = peso_xg * df["xl"].to_numpy() + (1 - peso_xg) * df["gl"].to_numpy()
    mv = peso_xg * df["xv"].to_numpy() + (1 - peso_xg) * df["gv"].to_numpy()
    yl = np.log(np.clip(ml, 0.05, None))
    yv = np.log(np.clip(mv, 0.05, None))

    atk = np.concatenate([il, iv])
    dfn = np.concatenate([iv, il])
    y = np.concatenate([yl, yv])
    loc = np.concatenate([np.ones(len(df)), np.zeros(len(df))])
    pesos = np.concatenate([w, w])

    a = np.zeros(n)
    d = np.zeros(n)
    g_eq = np.zeros(n)
    gamma = 0.25

    suma_atk = np.bincount(atk, weights=pesos, minlength=n)
    suma_dfn = np.bincount(dfn, weights=pesos, minlength=n)
    suma_loc = np.bincount(il, weights=w, minlength=n)
    suma_atk[suma_atk == 0] = 1e-9
    suma_dfn[suma_dfn == 0] = 1e-9
    peso_local = pesos[loc == 1].sum()

    # Factor de encogimiento: cuantos menos partidos efectivos, más se acerca
    # la estimación a la media de la liga.
    if shrink > 0:
        k_atk = suma_atk / (suma_atk + shrink)
        k_dfn = suma_dfn / (suma_dfn + shrink)
    else:
        k_atk = k_dfn = np.ones(n)

    ceros = np.zeros(len(df))
    for _ in range(iteraciones):
        # Desvío propio de cada estadio; cero cuando la variante no lo usa.
        extra = np.concatenate([g_eq[il] if gamma_equipo else ceros, ceros])

        gamma = float((pesos * (y - a[atk] + d[dfn] - extra) * loc).sum() / peso_local)
        ajuste = extra + gamma * loc

        num = np.bincount(atk, weights=pesos * (y + d[dfn] - ajuste), minlength=n)
        a = (num / suma_atk) * k_atk
        a -= a.mean()

        num = np.bincount(dfn, weights=pesos * (a[atk] - y + ajuste), minlength=n)
        d = (num / suma_dfn) * k_dfn

        if gamma_equipo:
            resid = yl - a[il] + d[iv] - gamma
            bruto = np.bincount(il, weights=w * resid, minlength=n) / np.maximum(suma_loc, 1e-9)
            # Encogido con fuerza: la ventaja por equipo es muy ruidosa
            g_eq = bruto * (suma_loc / (suma_loc + 40))

    return ({e: (float(a[i]), float(d[i]), float(g_eq[i])) for e, i in idx.items()},
            gamma)


def backtest(df, inicio_val, paso=25, **kw):
    val = df[df["fecha"] >= inicio_val].reset_index(drop=True)
    filas, fuerzas, gamma, ultimo = [], None, None, -1

    for i, fila in val.iterrows():
        if i - ultimo >= paso or fuerzas is None:
            hist = df[(df["fecha"] < fila["fecha"]) &
                      (df["fecha"] >= fila["fecha"] - pd.Timedelta(days=365 * ANIOS))]
            if len(hist) < 200:
                continue
            fuerzas, gamma = ajustar(hist, fila["fecha"], **kw)
            ultimo = i

        l, v = fila["local"], fila["visitante"]
        if l not in fuerzas or v not in fuerzas:
            continue
        al, dl, gl_eq = fuerzas[l]
        av, dv, _ = fuerzas[v]
        lam = np.exp(al - dv + gamma + gl_eq)
        mu = np.exp(av - dl)
        pl, pe, pv = probabilidades(lam, mu)
        real = 0 if fila["gl"] > fila["gv"] else 1 if fila["gl"] == fila["gv"] else 2
        filas.append((pl, pe, pv, real))

    P = np.array([[f[0], f[1], f[2]] for f in filas])
    y = np.array([f[3] for f in filas])
    return {
        "n": len(y),
        "log_loss": -np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-12, 1))),
        "brier": np.mean(np.sum((P - np.eye(3)[y]) ** 2, axis=1)),
        "acierto": np.mean(P.argmax(axis=1) == y) * 100,
    }


def main():
    df = cargar()
    inicio = pd.Timestamp("2024-08-01")
    print(f"{len(df):,} partidos · validando desde {inicio:%Y-%m-%d}\n")

    variantes = [
        ("Base: sólo xG",                    dict()),
        ("Sólo goles",                       dict(peso_xg=0.0)),
        ("Mezcla 80 % xG + 20 % goles",      dict(peso_xg=0.8)),
        ("Mezcla 65 % xG + 35 % goles",      dict(peso_xg=0.65)),
        ("Mezcla 50 % xG + 50 % goles",      dict(peso_xg=0.5)),
        ("Encogimiento suave",               dict(shrink=8)),
        ("Encogimiento medio",               dict(shrink=20)),
        ("Ventaja de campo por equipo",      dict(gamma_equipo=True)),
    ]

    print(f"  {'Variante':<32s} {'LogLoss':>9s} {'Brier':>8s} {'Acierto':>9s} {'vs base':>9s}")
    base = None
    resultados = {}
    for nombre, kw in variantes:
        r = backtest(df, inicio, **kw)
        resultados[nombre] = r
        if base is None:
            base = r["log_loss"]
        delta = (base - r["log_loss"]) / base * 100
        marca = "  <-- mejor" if r["log_loss"] < base else ""
        print(f"  {nombre:<32s} {r['log_loss']:>9.4f} {r['brier']:>8.4f} "
              f"{r['acierto']:>8.1f}% {delta:>+8.2f}%{marca}")

    mejor = min(resultados, key=lambda k: resultados[k]["log_loss"])
    print(f"\n  Mejor variante: {mejor}")

    # Combinación de las dos ideas que más aportaron
    print("\n  Combinaciones:")
    for nombre, kw in [
        ("Mezcla 80 % + encogimiento 8",  dict(peso_xg=0.8, shrink=8)),
        ("Mezcla 65 % + encogimiento 8",  dict(peso_xg=0.65, shrink=8)),
        ("Mezcla 80 % + encogimiento 20", dict(peso_xg=0.8, shrink=20)),
    ]:
        r = backtest(df, inicio, **kw)
        delta = (base - r["log_loss"]) / base * 100
        print(f"  {nombre:<32s} {r['log_loss']:>9.4f} {r['brier']:>8.4f} "
              f"{r['acierto']:>8.1f}% {delta:>+8.2f}%")


if __name__ == "__main__":
    main()
