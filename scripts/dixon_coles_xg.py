"""Dos mejoras sobre el Dixon-Coles base: calibración isotónica y entrada de xG.

**Calibración isotónica.** El backtest del modelo base mostró que sobreestima a
los favoritos claros: en el tramo 62-75 % de probabilidad dice 67,6 % y ocurre
62,1 %. La regresión isotónica aprende esa curva de sesgo sobre datos de
validación y la corrige, sin imponer ninguna forma funcional más allá de ser
monótona.

**Entrada de xG.** El modelo base aprende de goles, que son un recuento escaso y
muy ruidoso: un rebote afortunado cuenta igual que media hora de dominio. El xG
mide la calidad de las ocasiones y es un predictor más estable del rendimiento
futuro. Como el xG es continuo, no se puede meter en la verosimilitud Poisson
directamente: se estiman las fuerzas por mínimos cuadrados ponderados sobre
log(xG) y esas fuerzas alimentan después la misma matriz Poisson-Dixon-Coles.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, str(Path(__file__).parent))
from dixon_coles import DixonColes, MAX_GOLES, calibracion, metricas

DATOS = Path(__file__).resolve().parent.parent / "datos"


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #

def cargar_xg(temporadas=("2025-26",)) -> pd.DataFrame:
    """Carga los partidos con xG extraídos de Understat."""
    trozos = []
    for t in temporadas:
        ruta = DATOS / f"xg_{t}.csv"
        if ruta.exists():
            d = pd.read_csv(ruta, parse_dates=["fecha"])
            d["temporada"] = t
            trozos.append(d)
    df = pd.concat(trozos).sort_values("fecha").reset_index(drop=True)
    df["resultado"] = np.select(
        [df["goles_local"] > df["goles_visitante"],
         df["goles_local"] == df["goles_visitante"]],
        ["L", "E"], default="V")
    return df


# --------------------------------------------------------------------------- #
# Modelo con entrada de xG
# --------------------------------------------------------------------------- #

class DixonColesXG:
    """Fuerzas estimadas sobre xG; predicción con la misma matriz Poisson-DC.

    Args:
        xi: decaimiento temporal, igual que en el modelo base.
        rho: corrección de marcadores bajos. Se toma del modelo de goles, porque
            describe la dependencia entre los goles reales, no entre los xG.
    """

    def __init__(self, xi: float = 0.003, rho: float = -0.109):
        self.xi = xi
        self.rho = rho

    def ajustar(self, partidos: pd.DataFrame, referencia=None) -> "DixonColesXG":
        p = partidos.dropna(subset=["xg_local", "xg_visitante"])
        self.equipos = sorted(set(p["local"]) | set(p["visitante"]))
        n = len(self.equipos)
        idx = {e: i for i, e in enumerate(self.equipos)}

        referencia = referencia or p["fecha"].max()
        dias = (referencia - p["fecha"]).dt.days.to_numpy()
        w = np.exp(-self.xi * dias)

        # Cada partido aporta dos ecuaciones sobre log(xG):
        #   log(xg_local)     = atq[local]     - def[visitante] + gamma
        #   log(xg_visitante) = atq[visitante] - def[local]
        filas, y, pesos = [], [], []
        for (_, m), peso in zip(p.iterrows(), w):
            il, iv = idx[m["local"]], idx[m["visitante"]]

            f = np.zeros(2 * n + 1)
            f[il] = 1; f[n + iv] = -1; f[2 * n] = 1
            filas.append(f); y.append(np.log(max(m["xg_local"], 0.05))); pesos.append(peso)

            f = np.zeros(2 * n + 1)
            f[iv] = 1; f[n + il] = -1
            filas.append(f); y.append(np.log(max(m["xg_visitante"], 0.05))); pesos.append(peso)

        X = np.array(filas)
        y = np.array(y)
        sw = np.sqrt(np.array(pesos))

        # Restricción de identificabilidad: la media de los ataques es cero.
        # Se impone como una ecuación más, con peso alto.
        restriccion = np.zeros((1, 2 * n + 1))
        restriccion[0, :n] = 1
        X = np.vstack([X * sw[:, None], restriccion * 1000])
        y = np.concatenate([y * sw, [0]])

        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        self.ataque = dict(zip(self.equipos, beta[:n]))
        self.defensa = dict(zip(self.equipos, beta[n:2 * n]))
        self.gamma = float(beta[2 * n])
        return self

    def predecir(self, local: str, visitante: str) -> dict:
        lam = np.exp(self.ataque[local] - self.defensa[visitante] + self.gamma)
        mu = np.exp(self.ataque[visitante] - self.defensa[local])

        goles = np.arange(MAX_GOLES + 1)
        m = np.outer(poisson.pmf(goles, lam), poisson.pmf(goles, mu))
        m[0, 0] *= 1 - lam * mu * self.rho
        m[0, 1] *= 1 + lam * self.rho
        m[1, 0] *= 1 + mu * self.rho
        m[1, 1] *= 1 - self.rho
        m /= m.sum()

        total = goles[:, None] + goles[None, :]
        return {
            "p_local": float(np.tril(m, -1).sum()),
            "p_empate": float(np.trace(m)),
            "p_visitante": float(np.triu(m, 1).sum()),
            "p_over_25": float(m[total > 2.5].sum()),
            "p_btts": float(m[1:, 1:].sum()),
            "xg_esperado_local": float(lam),
            "xg_esperado_visitante": float(mu),
        }


# --------------------------------------------------------------------------- #
# Calibración isotónica
# --------------------------------------------------------------------------- #

class CalibradorIsotonico:
    """Corrige el sesgo de las probabilidades predichas.

    Entrena una regresión isotónica por cada salida (local, empate, visitante)
    sobre pares (probabilidad predicha, ocurrió sí/no), y renormaliza para que
    las tres vuelvan a sumar 1.
    """

    def __init__(self):
        self.modelos = {}

    def ajustar(self, pred: pd.DataFrame) -> "CalibradorIsotonico":
        for col, clase in [("p_local", "L"), ("p_empate", "E"), ("p_visitante", "V")]:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
            iso.fit(pred[col].to_numpy(),
                    (pred["resultado_real"] == clase).to_numpy().astype(float))
            self.modelos[col] = iso
        return self

    def aplicar(self, pred: pd.DataFrame) -> pd.DataFrame:
        d = pred.copy()
        bruto = np.column_stack([
            self.modelos[c].predict(d[c].to_numpy())
            for c in ("p_local", "p_empate", "p_visitante")])
        bruto /= bruto.sum(axis=1, keepdims=True)   # vuelven a sumar 1
        d[["p_local", "p_empate", "p_visitante"]] = bruto
        return d


def error_calibracion(pred: pd.DataFrame) -> float:
    """Error medio de calibración ponderado por tamaño de tramo."""
    cal = calibracion(pred)
    return float((cal["error"].abs() * cal["n"]).sum() / cal["n"].sum())
