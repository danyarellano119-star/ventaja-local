"""Valida el modelo Dixon-Coles con backtest temporal sobre la Premier League.

Entrena sólo con partidos anteriores a cada predicción, de modo que ninguna
predicción usa información del futuro. Reporta poder predictivo (log-loss y
Brier contra una referencia) y, sobre todo, calibración: si el modelo dice 60 %,
¿ocurre el 60 % de las veces?
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from dixon_coles import DixonColes, backtest, calibracion, cargar_resultados, metricas

SALIDA = Path(__file__).resolve().parent.parent / "datos"


def main():
    partidos = cargar_resultados()
    print(f"{len(partidos)} partidos entre {partidos['fecha'].min():%Y-%m-%d} "
          f"y {partidos['fecha'].max():%Y-%m-%d}\n")

    # --- Elección de xi por validación -------------------------------------- #
    print("Comparando velocidades de olvido (xi):")
    resultados = {}
    for xi in [0.0, 0.0010, 0.0018, 0.0030]:
        t0 = time.time()
        pred = backtest(partidos, xi=xi, min_entrenamiento=760, paso=20)
        m = metricas(pred)
        resultados[xi] = (m, pred)
        vida_media = "sin olvido" if xi == 0 else f"{np.log(2)/xi:,.0f} días"
        print(f"  xi={xi:<7.4f} ({vida_media:>12s})  "
              f"log-loss={m['log_loss']:.4f}  Brier={m['brier']:.4f}  "
              f"acierto={m['acierto_%']:.1f}%  [{time.time()-t0:.0f}s]")

    mejor_xi = min(resultados, key=lambda k: resultados[k][0]["log_loss"])
    m, pred = resultados[mejor_xi]
    print(f"\nMejor xi: {mejor_xi}\n")

    # --- Poder predictivo --------------------------------------------------- #
    print("--- Poder predictivo (1X2) ---")
    print(f"  Partidos evaluados     : {m['n']}")
    print(f"  Log-loss del modelo    : {m['log_loss']:.4f}")
    print(f"  Log-loss de referencia : {m['log_loss_base']:.4f}  "
          f"(predecir siempre las frecuencias históricas)")
    print(f"  Mejora                 : {m['mejora_log_loss_%']:.1f} %")
    print(f"  Brier                  : {m['brier']:.4f} vs {m['brier_base']:.4f}")
    print(f"  Acierto del favorito   : {m['acierto_%']:.1f} %")

    # --- Calibración -------------------------------------------------------- #
    print("\n--- Calibración (1X2, todos los tramos) ---")
    cal = calibracion(pred)
    for _, r in cal.iterrows():
        barra = "▓" * int(abs(r["error"]) * 100)
        signo = "+" if r["error"] >= 0 else "−"
        print(f"  {str(r['tramo']):>14s}  n={r['n']:>4d}  "
              f"predicha={r['predicha']:.3f}  observada={r['observada']:.3f}  "
              f"error={signo}{abs(r['error']):.3f} {barra}")

    error_medio = (cal["error"].abs() * cal["n"]).sum() / cal["n"].sum()
    print(f"\n  Error de calibración medio ponderado: {error_medio:.4f}")

    # --- Rendimiento por mercado -------------------------------------------- #
    print("\n--- Otros mercados ---")
    for nombre, col, real in [
        ("Over 2.5", "p_over_25",
         (pred["goles_local_real"] + pred["goles_visitante_real"]) > 2.5),
        ("Ambos marcan", "p_btts",
         (pred["goles_local_real"] > 0) & (pred["goles_visitante_real"] > 0)),
    ]:
        p, y = pred[col].to_numpy(), real.to_numpy().astype(float)
        brier = np.mean((p - y) ** 2)
        base = np.mean((y.mean() - y) ** 2)
        print(f"  {nombre:<14s} predicha media={p.mean():.3f}  "
              f"observada={y.mean():.3f}  Brier={brier:.4f} (base {base:.4f})")

    # --- Modelo final y ejemplo --------------------------------------------- #
    modelo = DixonColes(xi=mejor_xi).ajustar(partidos)
    fuerzas = modelo.fuerzas()
    fuerzas.to_csv(SALIDA / "dc_fuerzas.csv", index=False, encoding="utf-8-sig")
    pred.to_csv(SALIDA / "dc_backtest.csv", index=False, encoding="utf-8-sig")

    print(f"\n--- Modelo final (todas las temporadas, xi={mejor_xi}) ---")
    print(f"  Ventaja de local: {np.exp(modelo.gamma):.3f}x   rho: {modelo.rho:.4f}")
    print("\n  Ejemplo — Manchester United vs Liverpool en Old Trafford:")
    ej = modelo.predecir("Manchester United FC", "Liverpool FC")
    print(f"    Local {ej['p_local']*100:.1f} %  |  Empate {ej['p_empate']*100:.1f} %  "
          f"|  Visitante {ej['p_visitante']*100:.1f} %")
    print(f"    Goles esperados: {ej['goles_esperados_local']:.2f} - "
          f"{ej['goles_esperados_visitante']:.2f}")
    print(f"    Marcador más probable: {ej['marcador_probable']} "
          f"(sólo {ej['p_marcador_probable']*100:.1f} % de probabilidad)")
    print(f"    Over 2.5: {ej['p_over_25']*100:.1f} %  |  "
          f"Ambos marcan: {ej['p_btts']*100:.1f} %")

    print(f"\nGuardados dc_fuerzas.csv y dc_backtest.csv en {SALIDA}")


if __name__ == "__main__":
    main()
