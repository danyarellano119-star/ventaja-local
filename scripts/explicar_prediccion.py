"""Prototipo de predicción explicada: de dónde sale cada número.

La diferencia frente a un sitio de pronósticos convencional no es el modelo —es
el mismo Dixon-Coles— sino poder responder "¿por qué?" en cada nivel: qué fuerzas
tiene cada equipo, cómo se convierten en goles esperados, cómo se reparte la
probabilidad entre marcadores y qué haría falta para que la predicción cambiara.

Uso:
    python scripts/explicar_prediccion.py "Manchester United FC" "Liverpool FC"
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from dixon_coles import DixonColes, cargar_resultados


def barra(valor, minimo, maximo, ancho=22):
    """Barra de texto para situar un valor dentro de un rango."""
    pos = int(np.clip((valor - minimo) / (maximo - minimo), 0, 1) * (ancho - 1))
    return "·" * pos + "●" + "·" * (ancho - 1 - pos)


def forma_reciente(partidos: pd.DataFrame, equipo: str, n: int = 6) -> dict:
    """Resultados, goles y racha del equipo en sus últimos n partidos."""
    d = partidos[(partidos["local"] == equipo) | (partidos["visitante"] == equipo)].tail(n)
    gf = np.where(d["local"] == equipo, d["goles_local"], d["goles_visitante"])
    gc = np.where(d["local"] == equipo, d["goles_visitante"], d["goles_local"])
    res = ["V" if a > b else "E" if a == b else "D" for a, b in zip(gf, gc)]
    return {"racha": " ".join(res), "gf": gf.sum(), "gc": gc.sum(),
            "puntos": sum(3 if r == "V" else 1 if r == "E" else 0 for r in res),
            "n": len(d)}


def explicar(modelo: DixonColes, partidos: pd.DataFrame, local: str, visitante: str):
    pred = modelo.predecir(local, visitante)
    m = modelo.matriz_marcadores(local, visitante)
    fz = modelo.fuerzas().set_index("equipo")

    ancho = 74
    print("=" * ancho)
    print(f"  {local}  vs  {visitante}".center(ancho))
    print("=" * ancho)

    # --- 1. El resultado -------------------------------------------------- #
    print("\n[1] LO QUE DICE EL MODELO\n")
    for etiqueta, p in [(f"Gana {local}", pred["p_local"]),
                        ("Empate", pred["p_empate"]),
                        (f"Gana {visitante}", pred["p_visitante"])]:
        bloques = "█" * int(p * 40)
        print(f"    {etiqueta:<34s} {p*100:5.1f} %  {bloques}")

    print(f"\n    Cuota justa (sin margen de casa):")
    for etiqueta, p in [("1", pred["p_local"]), ("X", pred["p_empate"]),
                        ("2", pred["p_visitante"])]:
        print(f"      {etiqueta}: {1/p:.2f}", end="   ")
    print("\n    Una cuota por encima de esas tres tiene valor según el modelo.")

    # --- 2. De dónde sale ------------------------------------------------- #
    print("\n[2] DE DÓNDE SALE ESE NÚMERO\n")
    print("    El modelo resume cada equipo en dos fuerzas, comparadas con un")
    print("    equipo medio de la liga (1.00 = exactamente la media):\n")

    mn_a, mx_a = fz["mult_ataque"].min(), fz["mult_ataque"].max()
    mn_d, mx_d = fz["mult_defensa"].min(), fz["mult_defensa"].max()

    for eq in (local, visitante):
        a, d = fz.loc[eq, "mult_ataque"], fz.loc[eq, "mult_defensa"]
        pos_a = int((fz["mult_ataque"] > a).sum()) + 1
        pos_d = int((fz["mult_defensa"] < d).sum()) + 1
        print(f"    {eq}")
        print(f"      Ataque   {a:.2f}x  [{barra(a, mn_a, mx_a)}]  "
              f"{pos_a}.º de {len(fz)}   marca un {(a-1)*100:+.0f} % que la media")
        print(f"      Defensa  {d:.2f}x  [{barra(d, mn_d, mx_d)}]  "
              f"{pos_d}.º de {len(fz)}   encaja un {(d-1)*100:+.0f} % que la media")
        print()

    gl, gv = pred["goles_esperados_local"], pred["goles_esperados_visitante"]
    print(f"    Esas fuerzas se cruzan y dan los goles esperados:\n")
    print(f"      {local:<32s} {gl:.2f} goles")
    print(f"      {visitante:<32s} {gv:.2f} goles")
    print(f"\n      (incluida la ventaja de jugar en casa, que en esta liga vale")
    print(f"       un {(np.exp(modelo.gamma)-1)*100:.0f} % más de goles para el local)")

    # --- 3. El marcador --------------------------------------------------- #
    print("\n[3] POR QUÉ EL MARCADOR MÁS PROBABLE ENGAÑA\n")
    plano = [(f"{i}-{j}", m[i, j]) for i in range(6) for j in range(6)]
    plano.sort(key=lambda x: -x[1])
    print(f"    El más probable es {pred['marcador_probable']}, pero sólo tiene un "
          f"{pred['p_marcador_probable']*100:.1f} % :\n")
    for marcador, p in plano[:6]:
        print(f"      {marcador}   {p*100:5.1f} %  {'▪' * int(p * 200)}")
    acumulado = sum(p for _, p in plano[:6])
    print(f"\n    Esos seis marcadores juntos suman {acumulado*100:.0f} %. El otro "
          f"{(1-acumulado)*100:.0f} % se\n    reparte entre decenas de resultados. "
          f"Acertar el marcador exacto es\n    improbable por definición, y por eso "
          f"esa cifra no debe leerse\n    como una predicción.")

    # --- 4. Otros mercados ------------------------------------------------ #
    print("\n[4] OTROS MERCADOS\n")
    print(f"      Más de 2.5 goles     {pred['p_over_25']*100:5.1f} %   "
          f"cuota justa {1/pred['p_over_25']:.2f}")
    print(f"      Menos de 2.5 goles   {pred['p_under_25']*100:5.1f} %   "
          f"cuota justa {1/pred['p_under_25']:.2f}")
    print(f"      Ambos marcan         {pred['p_btts']*100:5.1f} %   "
          f"cuota justa {1/pred['p_btts']:.2f}")
    print("\n    Aviso: en el backtest el modelo NO superó a la simple media")
    print("    histórica en estos dos mercados. Úsalos como contexto, no como señal.")

    # --- 5. Contexto reciente --------------------------------------------- #
    print("\n[5] CONTEXTO QUE EL MODELO NO VE\n")
    for eq in (local, visitante):
        f = forma_reciente(partidos, eq)
        print(f"      {eq:<32s} {f['racha']}   "
              f"{f['puntos']}/{f['n']*3} pts   {f['gf']}-{f['gc']} goles")
    print("\n    El modelo ya pondera lo reciente, pero no sabe de lesiones,")
    print("    sanciones, rotaciones ni calendario europeo. Eso lo pones tú.")

    # --- 6. Fiabilidad ---------------------------------------------------- #
    print("\n[6] CUÁNTO FIARSE\n")
    fav = max(pred["p_local"], pred["p_empate"], pred["p_visitante"])
    print(f"      Calibración global del modelo: error medio de 1.5 puntos")
    print(f"      porcentuales sobre 756 partidos verificados.")
    if 0.625 < fav <= 0.75:
        print(f"\n      ATENCIÓN: esta predicción cae en el tramo 62-75 %, donde el")
        print(f"      modelo SOBREESTIMA sistemáticamente (dice 67.6 % y acierta")
        print(f"      62.1 %). Descuenta unos 5 puntos a ese {fav*100:.1f} %.")
    print(f"\n      El modelo mejora un 7.2 % sobre predecir siempre la media,")
    print(f"      pero NO ha sido validado contra cuotas reales de mercado.")
    print("=" * ancho)


if __name__ == "__main__":
    local = sys.argv[1] if len(sys.argv) > 2 else "Manchester United FC"
    visitante = sys.argv[2] if len(sys.argv) > 2 else "Liverpool FC"

    partidos = cargar_resultados()
    modelo = DixonColes(xi=0.003).ajustar(partidos)
    explicar(modelo, partidos, local, visitante)
