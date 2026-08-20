"""La prueba de fuego: ¿el modelo gana dinero contra cuotas reales?

Hasta ahora sabíamos que el modelo predice mejor que el azar. Eso no basta: la
pregunta que decide si esto sirve como producto es si sus probabilidades son
mejores que las que ya incorpora el mercado, porque el precio de una apuesta las
lleva dentro.

Se mide con tres cosas, de menos a más exigente:

1. **Log-loss frente al mercado.** Si el modelo acierta peor que la cuota, no hay
   nada que hacer.
2. **Rendimiento simulado.** Apostando sólo cuando el modelo ve valor, ¿se gana
   o se pierde a la larga?
3. **Valor sobre la línea de cierre (CLV).** Si conseguimos sistemáticamente
   cuotas mejores que la que acaba fijando el mercado, hay ventaja real aunque la
   muestra sea corta.

De paso se comprueba si añadir los días de descanso entre partidos mejora algo.

    python scripts/backtest_cuotas.py
"""

from __future__ import annotations

import io
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from experimento_historia import MAXG, RHO, probabilidades

RAIZ = Path(__file__).resolve().parent.parent
CACHE_CUOTAS = RAIZ / "datos" / "cuotas_historicas.csv"
BASE = "https://cdn.jsdelivr.net/gh/huhao930422-debug/football-odds-mirror@main/data"

LIGAS = {
    "premier-league": "EPL", "la-liga": "La_liga", "bundesliga": "Bundesliga",
    "serie-a": "Serie_A", "ligue-1": "Ligue_1",
}
TEMPORADAS = ["2021", "2122", "2223", "2324", "2425", "2526"]

XI = 0.0030
ANIOS = 4

# Nombres que football-data escribe distinto a Understat
ALIAS = {
    "man united": "manchester united", "man city": "manchester city",
    "nott'm forest": "nottingham forest", "sheffield united": "sheffield united",
    "spurs": "tottenham", "wolves": "wolverhampton wanderers",
    "ath madrid": "atletico madrid", "ath bilbao": "athletic club",
    "sociedad": "real sociedad", "betis": "real betis",
    "espanol": "espanyol", "vallecano": "rayo vallecano",
    "celta": "celta vigo", "la coruna": "deportivo la coruna",
    "ein frankfurt": "eintracht frankfurt", "fc koln": "fc cologne",
    "leverkusen": "bayer leverkusen", "bayern munich": "bayern munich",
    "dortmund": "borussia dortmund", "m'gladbach": "borussia m.gladbach",
    "hertha": "hertha berlin", "mainz": "mainz 05", "stuttgart": "vfb stuttgart",
    "hoffenheim": "hoffenheim", "rb leipzig": "rasenballsport leipzig",
    "heidenheim": "fc heidenheim", "st pauli": "st. pauli",
    "ac milan": "ac milan", "milan": "ac milan", "inter": "inter",
    "roma": "roma", "napoli": "napoli", "juventus": "juventus",
    "verona": "verona", "parma": "parma calcio 1913",
    "paris sg": "paris saint germain", "marseille": "marseille",
    "st etienne": "saint-etienne", "clermont": "clermont foot",
}


def clave(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", str(nombre).lower().strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = " ".join(s.split())
    return ALIAS.get(s, s)


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #

def descargar_cuotas() -> pd.DataFrame:
    """Resultados y cuotas de las cinco ligas, temporada por temporada."""
    if CACHE_CUOTAS.exists():
        return pd.read_csv(CACHE_CUOTAS, parse_dates=["fecha"])

    filas = []
    for carpeta, liga in LIGAS.items():
        for temp in TEMPORADAS:
            url = f"{BASE}/{carpeta}/season-{temp}.csv"
            try:
                r = requests.get(url, timeout=60)
                if r.status_code != 200:
                    continue
                d = pd.read_csv(io.StringIO(r.text))
            except Exception:
                continue
            if "HomeTeam" not in d.columns:
                continue

            # Pinnacle es la referencia del sector; si falta, se usa la media
            # del mercado y, en último caso, Bet365.
            def col(*nombres):
                for n in nombres:
                    if n in d.columns:
                        return d[n]
                return pd.Series([np.nan] * len(d))

            filas.append(pd.DataFrame({
                "liga": liga, "temporada": temp,
                "fecha": pd.to_datetime(d["Date"], dayfirst=True, errors="coerce"),
                "local": d["HomeTeam"].map(clave),
                "visitante": d["AwayTeam"].map(clave),
                "gl": d["FTHG"], "gv": d["FTAG"],
                "cl": col("PSH", "AvgH", "B365H"),
                "ce": col("PSD", "AvgD", "B365D"),
                "cv": col("PSA", "AvgA", "B365A"),
                "max_l": col("MaxH", "PSH", "B365H"),
                "max_e": col("MaxD", "PSD", "B365D"),
                "max_v": col("MaxA", "PSA", "B365A"),
            }))
            print(f"  {carpeta:16s} {temp}: {len(d)} partidos")

    df = pd.concat(filas).dropna(subset=["fecha", "gl", "gv", "cl", "ce", "cv"])
    df = df.sort_values("fecha").reset_index(drop=True)
    CACHE_CUOTAS.parent.mkdir(exist_ok=True)
    df.to_csv(CACHE_CUOTAS, index=False, encoding="utf-8")
    return df


def cargar_xg() -> pd.DataFrame:
    """Los partidos con xG que ya usa el modelo, con nombres normalizados."""
    from experimento_historia import cargar
    df = cargar()
    df["local"] = df["local"].map(clave)
    df["visitante"] = df["visitante"].map(clave)
    return df


def dias_descanso(df: pd.DataFrame) -> pd.DataFrame:
    """Días transcurridos desde el partido anterior de cada equipo."""
    largo = pd.concat([
        df[["fecha", "local"]].rename(columns={"local": "equipo"}),
        df[["fecha", "visitante"]].rename(columns={"visitante": "equipo"}),
    ]).sort_values("fecha")
    largo["previo"] = largo.groupby("equipo")["fecha"].shift(1)
    largo["descanso"] = (largo["fecha"] - largo["previo"]).dt.days

    mapa = {(f, e): d for f, e, d in
            zip(largo["fecha"], largo["equipo"], largo["descanso"])}
    df = df.copy()
    df["desc_l"] = [mapa.get((f, e), np.nan) for f, e in zip(df["fecha"], df["local"])]
    df["desc_v"] = [mapa.get((f, e), np.nan) for f, e in zip(df["fecha"], df["visitante"])]
    return df


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #

def ajustar(df, referencia, xi=XI, iteraciones=120, coef_descanso=0.0):
    """Fuerzas sobre log(xG), con ajuste opcional por días de descanso."""
    equipos = pd.unique(pd.concat([df["local"], df["visitante"]]))
    idx = {e: i for i, e in enumerate(equipos)}
    n = len(equipos)

    il = df["local"].map(idx).to_numpy()
    iv = df["visitante"].map(idx).to_numpy()
    w = np.exp(-xi * (referencia - df["fecha"]).dt.days.to_numpy().clip(min=0))

    yl = np.log(np.clip(df["xl"].to_numpy(), 0.05, None))
    yv = np.log(np.clip(df["xv"].to_numpy(), 0.05, None))

    atk = np.concatenate([il, iv])
    dfn = np.concatenate([iv, il])
    y = np.concatenate([yl, yv])
    loc = np.concatenate([np.ones(len(df)), np.zeros(len(df))])
    pesos = np.concatenate([w, w])

    a, d, gamma = np.zeros(n), np.zeros(n), 0.25
    s_atk = np.bincount(atk, weights=pesos, minlength=n)
    s_dfn = np.bincount(dfn, weights=pesos, minlength=n)
    s_atk[s_atk == 0] = 1e-9
    s_dfn[s_dfn == 0] = 1e-9
    peso_local = pesos[loc == 1].sum()

    for _ in range(iteraciones):
        gamma = float((pesos * (y - a[atk] + d[dfn]) * loc).sum() / peso_local)
        a = np.bincount(atk, weights=pesos * (y + d[dfn] - gamma * loc),
                        minlength=n) / s_atk
        a -= a.mean()
        d = np.bincount(dfn, weights=pesos * (a[atk] - y + gamma * loc),
                        minlength=n) / s_dfn

    return {e: (float(a[i]), float(d[i])) for e, i in idx.items()}, gamma


def efecto_descanso(desc, coef):
    """Penaliza jugar con pocos días de descanso. Sin dato, no penaliza."""
    if coef == 0 or not np.isfinite(desc):
        return 0.0
    # Se toma una semana como referencia; menos descanso resta rendimiento
    return coef * min(desc - 7, 0) / 7


# --------------------------------------------------------------------------- #
# Métricas de apuesta
# --------------------------------------------------------------------------- #

def sin_margen(cuotas):
    """Probabilidades que implica el mercado, ya quitado el margen de la casa."""
    inv = np.array([1 / c for c in cuotas])
    return inv / inv.sum()


def evaluar(pred: pd.DataFrame, umbral=0.03, usar_max=False):
    """Rendimiento de apostar sólo cuando el modelo ve valor suficiente."""
    P = pred[["p_local", "p_empate", "p_visitante"]].to_numpy()
    C = pred[["max_l", "max_e", "max_v"]].to_numpy() if usar_max \
        else pred[["cl", "ce", "cv"]].to_numpy()
    y = pred["real"].to_numpy()

    ev = P * C - 1                      # ganancia esperada por cada peso jugado
    apuesta = ev > umbral
    n = int(apuesta.sum())
    if n == 0:
        return {"n": 0}

    gano = np.zeros_like(ev, dtype=bool)
    gano[np.arange(len(y)), y] = True
    retorno = np.where(gano, C - 1, -1.0)
    beneficio = float(retorno[apuesta].sum())

    return {
        "n": n,
        "pct_apostado": n / (len(y) * 3) * 100,
        "roi": beneficio / n * 100,
        "beneficio": beneficio,
        "acierto": float(gano[apuesta].mean() * 100),
        "cuota_media": float(C[apuesta].mean()),
    }


def main():
    print("Descargando cuotas (la primera vez tarda)...")
    cuotas = descargar_cuotas()
    print(f"{len(cuotas):,} partidos con cuotas\n")

    xg = cargar_xg()
    df = xg.merge(cuotas[["fecha", "local", "visitante", "cl", "ce", "cv",
                          "max_l", "max_e", "max_v"]],
                  on=["fecha", "local", "visitante"], how="inner")
    df = dias_descanso(df).sort_values("fecha").reset_index(drop=True)
    print(f"{len(df):,} partidos emparejados con xG y cuota "
          f"({len(df) / len(cuotas) * 100:.0f} % de los disponibles)\n")

    inicio = pd.Timestamp("2023-08-01")

    for coef, etiqueta in [(0.0, "Modelo actual"), (0.12, "Con días de descanso")]:
        filas, fuerzas, gamma, ultimo = [], None, None, -1
        val = df[df["fecha"] >= inicio].reset_index(drop=True)

        for i, f in val.iterrows():
            if i - ultimo >= 25 or fuerzas is None:
                hist = df[(df["fecha"] < f["fecha"]) &
                          (df["fecha"] >= f["fecha"] - pd.Timedelta(days=365 * ANIOS))]
                if len(hist) < 400:
                    continue
                fuerzas, gamma = ajustar(hist, f["fecha"])
                ultimo = i

            l, v = f["local"], f["visitante"]
            if l not in fuerzas or v not in fuerzas:
                continue
            al, dl = fuerzas[l]
            av, dv = fuerzas[v]
            lam = np.exp(al - dv + gamma + efecto_descanso(f["desc_l"], coef))
            mu = np.exp(av - dl + efecto_descanso(f["desc_v"], coef))
            pl, pe, pv = probabilidades(lam, mu)
            real = 0 if f["gl"] > f["gv"] else 1 if f["gl"] == f["gv"] else 2
            filas.append({"p_local": pl, "p_empate": pe, "p_visitante": pv,
                          "cl": f["cl"], "ce": f["ce"], "cv": f["cv"],
                          "max_l": f["max_l"], "max_e": f["max_e"],
                          "max_v": f["max_v"], "real": real})

        pred = pd.DataFrame(filas)
        P = pred[["p_local", "p_empate", "p_visitante"]].to_numpy()
        C = pred[["cl", "ce", "cv"]].to_numpy()
        y = pred["real"].to_numpy()
        M = np.array([sin_margen(c) for c in C])

        ll_modelo = -np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-12, 1)))
        ll_mercado = -np.mean(np.log(np.clip(M[np.arange(len(y)), y], 1e-12, 1)))
        margen = float(np.mean((1 / C).sum(axis=1) - 1) * 100)

        print("=" * 70)
        print(f"{etiqueta.upper()}  ·  {len(pred):,} partidos")
        print("=" * 70)
        print(f"  Log-loss del modelo  : {ll_modelo:.4f}")
        print(f"  Log-loss del mercado : {ll_mercado:.4f}")
        dif = (ll_mercado - ll_modelo) / ll_mercado * 100
        print(f"  Diferencia           : {dif:+.2f} %  "
              f"({'el modelo gana' if dif > 0 else 'gana el mercado'})")
        print(f"  Margen medio de la casa: {margen:.1f} %")

        for usar_max, nombre in [(False, "cuota de Pinnacle"), (True, "mejor cuota del mercado")]:
            for umbral in (0.02, 0.05, 0.10):
                r = evaluar(pred, umbral, usar_max)
                if r["n"] == 0:
                    continue
                print(f"  Apostando con valor >{umbral:.0%} a la {nombre}: "
                      f"{r['n']:>4d} apuestas · acierto {r['acierto']:.1f} % · "
                      f"ROI {r['roi']:+.1f} %")
        print()


if __name__ == "__main__":
    main()
