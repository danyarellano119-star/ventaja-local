"""Compara el Dixon-Coles base con las dos mejoras: calibración isotónica y xG.

Las tres variantes se entrenan y validan sobre exactamente los mismos partidos
para que la comparación sea justa. La calibración se aprende en un tramo y se
aplica en otro distinto, de modo que nunca se evalúa sobre datos ya vistos.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from dixon_coles import DixonColes, metricas, calibracion
from dixon_coles_xg import (CalibradorIsotonico, DixonColesXG, cargar_xg,
                            error_calibracion)

DATOS = Path(__file__).resolve().parent.parent / "datos"


def backtest_generico(partidos, constructor, inicio, paso=10):
    """Predice cada partido desde `inicio` reentrenando cada `paso` partidos."""
    filas, modelo = [], None
    for i in range(inicio, len(partidos)):
        if modelo is None or (i - inicio) % paso == 0:
            modelo = constructor().ajustar(partidos.iloc[:i],
                                           referencia=partidos.iloc[i]["fecha"])
        p = partidos.iloc[i]
        if p["local"] not in modelo.ataque or p["visitante"] not in modelo.ataque:
            continue
        filas.append({**modelo.predecir(p["local"], p["visitante"]),
                      "fecha": p["fecha"],
                      "resultado_real": p["resultado"],
                      "goles_local_real": p["goles_local"],
                      "goles_visitante_real": p["goles_visitante"]})
    return pd.DataFrame(filas)


def linea(nombre, m, err_cal):
    print(f"  {nombre:<34s} {m['log_loss']:.4f}   {m['brier']:.4f}   "
          f"{m['acierto_%']:5.1f} %   {err_cal:.4f}")


def main():
    partidos = cargar_xg()
    print(f"{len(partidos)} partidos con xG "
          f"({partidos['fecha'].min():%Y-%m-%d} a {partidos['fecha'].max():%Y-%m-%d})\n")

    # Se entrena con los primeros 150 partidos; el resto se parte en dos: la
    # primera mitad sirve para aprender la calibración y la segunda para evaluar.
    INICIO = 150
    print(f"Entrenamiento inicial: {INICIO} partidos")

    pred_goles = backtest_generico(partidos, lambda: DixonColes(xi=0.003), INICIO)
    pred_xg    = backtest_generico(partidos, lambda: DixonColesXG(xi=0.003), INICIO)

    corte = len(pred_goles) // 2
    cal_train_g, evalu_g = pred_goles.iloc[:corte], pred_goles.iloc[corte:]
    cal_train_x, evalu_x = pred_xg.iloc[:corte], pred_xg.iloc[corte:]
    print(f"Ajuste de calibración: {corte} partidos | "
          f"Evaluación final: {len(evalu_g)} partidos\n")

    # Calibradores aprendidos sólo con el primer tramo
    cal_g = CalibradorIsotonico().ajustar(cal_train_g)
    cal_x = CalibradorIsotonico().ajustar(cal_train_x)

    variantes = {
        "1. Base (goles)":                   evalu_g,
        "2. Goles + calibración isotónica":  cal_g.aplicar(evalu_g),
        "3. xG":                             evalu_x,
        "4. xG + calibración isotónica":     cal_x.aplicar(evalu_x),
    }

    print("--- Comparación sobre los mismos partidos ---")
    print(f"  {'Variante':<34s} {'LogLoss':>7s}   {'Brier':>6s}   "
          f"{'Acierto':>7s}   {'ErrCal':>6s}")
    resultados = {}
    for nombre, pred in variantes.items():
        m = metricas(pred)
        e = error_calibracion(pred)
        resultados[nombre] = (m, e)
        linea(nombre, m, e)

    base_ll = resultados["1. Base (goles)"][0]["log_loss"]
    print("\n--- Mejora relativa en log-loss frente a la variante 1 ---")
    for nombre, (m, _) in resultados.items():
        if nombre == "1. Base (goles)":
            continue
        delta = (base_ll - m["log_loss"]) / base_ll * 100
        signo = "mejor" if delta > 0 else "PEOR"
        print(f"  {nombre:<34s} {delta:+.2f} %  ({signo})")

    # Efecto concreto de la calibración en el tramo problemático
    print("\n--- El tramo que estaba mal calibrado (62-75 %) ---")
    for nombre in ["1. Base (goles)", "2. Goles + calibración isotónica"]:
        pred = variantes[nombre]
        cal = calibracion(pred)
        tramo = cal[(cal["predicha"] > 0.60) & (cal["predicha"] < 0.78)]
        if len(tramo):
            r = tramo.iloc[0]
            print(f"  {nombre:<34s} predicha={r['predicha']:.3f}  "
                  f"observada={r['observada']:.3f}  error={r['error']:+.3f}  n={r['n']}")
        else:
            print(f"  {nombre:<34s} sin partidos en ese tramo")

    # Guardado del modelo xG final para la web
    modelo_xg = DixonColesXG(xi=0.003).ajustar(partidos)
    fuerzas = pd.DataFrame({
        "equipo": modelo_xg.equipos,
        "ataque": [modelo_xg.ataque[e] for e in modelo_xg.equipos],
        "defensa": [modelo_xg.defensa[e] for e in modelo_xg.equipos],
    })
    fuerzas["mult_ataque"] = np.exp(fuerzas["ataque"])
    fuerzas["mult_defensa"] = np.exp(-fuerzas["defensa"])
    fuerzas["fuerza_neta"] = fuerzas["ataque"] + fuerzas["defensa"]
    fuerzas = fuerzas.sort_values("fuerza_neta", ascending=False)
    fuerzas.to_csv(DATOS / "dc_xg_fuerzas.csv", index=False, encoding="utf-8-sig")

    print(f"\n--- Fuerzas según xG (top 6) — ventaja local "
          f"{np.exp(modelo_xg.gamma):.3f}x ---")
    print(fuerzas.head(6).to_string(index=False))
    print(f"\nGuardado dc_xg_fuerzas.csv")


if __name__ == "__main__":
    main()
