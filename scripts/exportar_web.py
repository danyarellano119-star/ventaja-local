"""Exporta a JSON todo lo que la página web necesita para calcular en el navegador.

La web no llama a ningún servidor: lleva embebidas las fuerzas del modelo y las
estadísticas de contexto, y recalcula las probabilidades en JavaScript. Eso la
hace autocontenida y gratuita de operar, a cambio de que los datos sean los del
cierre de temporada.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from dixon_coles_xg import DixonColesXG, cargar_xg

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"

# Understat y FBref nombran distinto a algunos equipos.
EQUIV = {
    "Wolverhampton Wanderers": "Wolves", "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nottingham", "Leeds": "Leeds United",
    "Manchester United": "Manchester Utd",
}


def main():
    partidos = cargar_xg()
    modelo = DixonColesXG(xi=0.003).ajustar(partidos)

    liga = (pd.read_csv(DATOS / "clasificacion.csv")
            .merge(pd.read_csv(DATOS / "tiros_favor.csv")[
                ["team", "shots", "shots_on_target", "shots_on_target_pct"]], on="team")
            .merge(pd.read_csv(DATOS / "tiros_contra.csv")[
                ["team", "shots", "shots_on_target"]], on="team",
                suffixes=("", "_contra"))
            .merge(pd.read_csv(DATOS / "portero_favor.csv")[
                ["team", "gk_save_pct", "gk_clean_sheets", "gk_saves"]], on="team")
            .merge(pd.read_csv(DATOS / "standard_favor.csv")[
                ["team", "possession", "assists", "avg_age"]], on="team")
            .merge(pd.read_csv(DATOS / "misc_favor.csv")[
                ["team", "fouls", "cards_yellow", "cards_red", "crosses",
                 "tackles_won", "interceptions", "offsides"]], on="team"))

    # xG agregado por equipo desde los partidos de Understat
    filas = []
    for _, m in partidos.iterrows():
        filas.append({"equipo": m["local"], "xg": m["xg_local"],
                      "xga": m["xg_visitante"], "gf": m["goles_local"],
                      "gc": m["goles_visitante"]})
        filas.append({"equipo": m["visitante"], "xg": m["xg_visitante"],
                      "xga": m["xg_local"], "gf": m["goles_visitante"],
                      "gc": m["goles_local"]})
    xg_eq = pd.DataFrame(filas).groupby("equipo", as_index=False).sum()
    xg_eq["team"] = xg_eq["equipo"].replace(EQUIV)

    liga = liga.merge(xg_eq[["team", "xg", "xga", "gf", "gc"]], on="team", how="left")

    inverso = {v: k for k, v in EQUIV.items()}
    equipos = []
    for _, r in liga.iterrows():
        nombre_us = inverso.get(r["team"], r["team"])
        if nombre_us not in modelo.ataque:
            print(f"  [aviso] sin fuerzas para {r['team']} ({nombre_us})")
            continue
        equipos.append({
            "nombre": r["team"],
            "clave": nombre_us,
            "pos": int(r["rank"]),
            "pts": int(r["points"]),
            "atq": round(float(modelo.ataque[nombre_us]), 5),
            "def": round(float(modelo.defensa[nombre_us]), 5),
            "gf": int(r["goals_for"]), "gc": int(r["goals_against"]),
            "xg": round(float(r["xg"]), 1), "xga": round(float(r["xga"]), 1),
            "tiros": int(r["shots"]), "sot": int(r["shots_on_target"]),
            "sot_pct": float(r["shots_on_target_pct"]),
            "tiros_c": int(r["shots_contra"]), "sot_c": int(r["shots_on_target_contra"]),
            "paradas_pct": float(r["gk_save_pct"]),
            "vallas": int(r["gk_clean_sheets"]),
            "posesion": float(r["possession"]),
            "faltas": int(r["fouls"]), "ta": int(r["cards_yellow"]),
            "tr": int(r["cards_red"]), "centros": int(r["crosses"]),
            "entradas": int(r["tackles_won"]), "intercep": int(r["interceptions"]),
            "fjuego": int(r["offsides"]),
        })

    salida = {
        "temporada": "2025/26",
        "liga": "Premier League",
        "gamma": round(float(modelo.gamma), 5),
        "rho": round(float(modelo.rho), 5),
        "n_partidos": int(len(partidos)),
        "equipos": sorted(equipos, key=lambda e: e["pos"]),
        "validacion": {
            "n_evaluados": 115,
            "log_loss": 1.0045,
            "log_loss_base": 1.0805,
            "brier": 0.5994,
            "acierto_pct": 50.4,
            "error_calibracion": 0.0217,
        },
    }

    destino = RAIZ / "web" / "datos_modelo.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(equipos)} equipos exportados a {destino}")
    print(f"gamma={salida['gamma']} (ventaja local {np.exp(salida['gamma']):.3f}x)  "
          f"rho={salida['rho']}")


if __name__ == "__main__":
    main()
