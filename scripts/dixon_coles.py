"""Modelo Dixon-Coles para la Premier League.

Implementa el modelo de Dixon y Coles (1997): dos Poisson independientes para los
goles de local y visitante, con dos añadidos que corrigen sus defectos conocidos:

1. **Corrección tau para marcadores bajos.** Los Poisson independientes
   subestiman los 0-0 y 1-1 y sobreestiman los 1-0 y 0-1. El parámetro ``rho``
   reajusta esas cuatro celdas.
2. **Ponderación temporal exponencial.** Un partido de hace dos años dice menos
   sobre el equipo de hoy que uno de hace un mes. El parámetro ``xi`` controla la
   velocidad del olvido.

Cada equipo tiene una fuerza de ataque y una de defensa; ``gamma`` recoge la
ventaja de jugar en casa, común a toda la liga.

Uso:
    from dixon_coles import DixonColes, cargar_resultados
    partidos = cargar_resultados()
    modelo = DixonColes(xi=0.0018).ajustar(partidos)
    modelo.predecir("Manchester United", "Liverpool")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"

MAX_GOLES = 10  # trunca la matriz de marcadores; P(>10 goles) es despreciable


# --------------------------------------------------------------------------- #
# Carga de datos
# --------------------------------------------------------------------------- #

def _marcador_final(m: dict):
    """Extrae [goles_local, goles_visitante] del partido.

    openfootball no es consistente entre temporadas: unas veces ``score`` es
    ``{"ft": [x, y]}`` y otras la lista ``[x, y]`` directamente. Algunas
    temporadas antiguas usan además las claves ``score1``/``score2``.
    """
    s = m.get("score")
    if isinstance(s, dict):
        ft = s.get("ft")
        if isinstance(ft, dict):          # {"ft": {"1": x, "2": y}}
            return [ft.get("1"), ft.get("2")]
        return ft
    if isinstance(s, list) and len(s) >= 2:
        return s[:2]
    if m.get("score1") is not None:
        return [m["score1"], m["score2"]]
    return None


def cargar_resultados(temporadas=("2022-23", "2023-24", "2024-25", "2025-26")) -> pd.DataFrame:
    """Lee los JSON de openfootball y devuelve un DataFrame de partidos jugados."""
    filas = []
    for temporada in temporadas:
        ruta = DATOS / f"resultados_{temporada}.json"
        if not ruta.exists():
            continue
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for m in datos["matches"]:
            marcador = _marcador_final(m)
            if not marcador:
                continue
            filas.append({
                "temporada": temporada,
                "fecha": pd.to_datetime(m["date"]),
                "local": m["team1"],
                "visitante": m["team2"],
                "goles_local": int(marcador[0]),
                "goles_visitante": int(marcador[1]),
            })
    df = pd.DataFrame(filas).sort_values("fecha").reset_index(drop=True)
    df["resultado"] = np.select(
        [df["goles_local"] > df["goles_visitante"],
         df["goles_local"] == df["goles_visitante"]],
        ["L", "E"], default="V")
    return df


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #

def tau(gl, gv, lam, mu, rho):
    """Corrección de Dixon-Coles para los cuatro marcadores bajos."""
    t = np.ones_like(lam, dtype=float)
    t = np.where((gl == 0) & (gv == 0), 1 - lam * mu * rho, t)
    t = np.where((gl == 0) & (gv == 1), 1 + lam * rho, t)
    t = np.where((gl == 1) & (gv == 0), 1 + mu * rho, t)
    t = np.where((gl == 1) & (gv == 1), 1 - rho, t)
    return t


@dataclass
class DixonColes:
    """Ajusta y consulta un modelo Dixon-Coles.

    Args:
        xi: velocidad del decaimiento temporal (0 = sin decaimiento). Valores
            típicos entre 0.001 y 0.003 para datos diarios.
    """

    xi: float = 0.0018
    equipos: list[str] = field(default_factory=list, init=False)
    ataque: dict[str, float] = field(default_factory=dict, init=False)
    defensa: dict[str, float] = field(default_factory=dict, init=False)
    gamma: float = field(default=0.0, init=False)   # ventaja de local (log)
    rho: float = field(default=0.0, init=False)
    _ajustado: bool = field(default=False, init=False)

    # -- ajuste ------------------------------------------------------------- #

    def ajustar(self, partidos: pd.DataFrame, referencia=None) -> "DixonColes":
        """Estima los parámetros por máxima verosimilitud ponderada.

        Args:
            partidos: DataFrame con local, visitante, goles_local, goles_visitante, fecha.
            referencia: fecha desde la que se mide el decaimiento temporal.
                Por defecto, la del último partido del conjunto.
        """
        p = partidos.dropna(subset=["goles_local", "goles_visitante"])
        self.equipos = sorted(set(p["local"]) | set(p["visitante"]))
        n = len(self.equipos)
        idx = {e: i for i, e in enumerate(self.equipos)}

        il = p["local"].map(idx).to_numpy()
        iv = p["visitante"].map(idx).to_numpy()
        gl = p["goles_local"].to_numpy()
        gv = p["goles_visitante"].to_numpy()

        referencia = referencia or p["fecha"].max()
        dias = (referencia - p["fecha"]).dt.days.to_numpy()
        peso = np.exp(-self.xi * dias)

        # Parámetros: n ataques, n defensas, gamma, rho
        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])

        def neg_log_verosimilitud(x):
            atq, dfn = x[:n], x[n:2 * n]
            gamma, rho = x[2 * n], x[2 * n + 1]
            lam = np.exp(atq[il] - dfn[iv] + gamma)
            mu = np.exp(atq[iv] - dfn[il])
            t = tau(gl, gv, lam, mu, rho)
            t = np.clip(t, 1e-10, None)   # evita log(0) en la frontera de rho
            ll = (np.log(t)
                  + poisson.logpmf(gl, lam)
                  + poisson.logpmf(gv, mu))
            return -np.sum(peso * ll)

        # El modelo es invariante si se suma una constante a todos los ataques,
        # así que se fija la media de ataque en cero para que sea identificable.
        restricciones = [{"type": "eq", "fun": lambda x: np.sum(x[:n])}]
        limites = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.3, 0.3)]

        res = minimize(neg_log_verosimilitud, x0, method="SLSQP",
                       constraints=restricciones, bounds=limites,
                       options={"maxiter": 400, "ftol": 1e-9})

        self.ataque = dict(zip(self.equipos, res.x[:n]))
        self.defensa = dict(zip(self.equipos, res.x[n:2 * n]))
        self.gamma = float(res.x[2 * n])
        self.rho = float(res.x[2 * n + 1])
        self.convergio = bool(res.success)
        self.log_verosimilitud = float(-res.fun)
        self._ajustado = True
        return self

    # -- predicción --------------------------------------------------------- #

    def matriz_marcadores(self, local: str, visitante: str) -> np.ndarray:
        """Probabilidad de cada marcador exacto, con filas = goles del local."""
        if not self._ajustado:
            raise RuntimeError("El modelo no está ajustado; llama antes a .ajustar()")
        for e in (local, visitante):
            if e not in self.ataque:
                raise KeyError(f"Equipo desconocido: {e!r}")

        lam = np.exp(self.ataque[local] - self.defensa[visitante] + self.gamma)
        mu = np.exp(self.ataque[visitante] - self.defensa[local])

        goles = np.arange(MAX_GOLES + 1)
        m = np.outer(poisson.pmf(goles, lam), poisson.pmf(goles, mu))

        # Corrección tau sobre las cuatro celdas bajas
        m[0, 0] *= 1 - lam * mu * self.rho
        m[0, 1] *= 1 + lam * self.rho
        m[1, 0] *= 1 + mu * self.rho
        m[1, 1] *= 1 - self.rho

        return m / m.sum()

    def predecir(self, local: str, visitante: str) -> dict:
        """Devuelve las probabilidades de los mercados habituales."""
        m = self.matriz_marcadores(local, visitante)
        goles = np.arange(MAX_GOLES + 1)
        total = goles[:, None] + goles[None, :]

        i, j = np.unravel_index(m.argmax(), m.shape)
        return {
            "local": local,
            "visitante": visitante,
            "p_local":    float(np.tril(m, -1).sum()),
            "p_empate":   float(np.trace(m)),
            "p_visitante": float(np.triu(m, 1).sum()),
            "p_over_25":  float(m[total > 2.5].sum()),
            "p_under_25": float(m[total <= 2.5].sum()),
            "p_btts":     float(m[1:, 1:].sum()),
            "marcador_probable": f"{i}-{j}",
            "p_marcador_probable": float(m[i, j]),
            "goles_esperados_local": float((m.sum(axis=1) * goles).sum()),
            "goles_esperados_visitante": float((m.sum(axis=0) * goles).sum()),
        }

    def fuerzas(self) -> pd.DataFrame:
        """Fuerzas de ataque y defensa por equipo, en escala interpretable."""
        d = pd.DataFrame({
            "equipo": self.equipos,
            "ataque": [self.ataque[e] for e in self.equipos],
            "defensa": [self.defensa[e] for e in self.equipos],
        })
        # exp(ataque) = multiplicador sobre los goles de un equipo medio
        d["mult_ataque"] = np.exp(d["ataque"])
        d["mult_defensa"] = np.exp(-d["defensa"])   # <1 = concede menos
        d["fuerza_neta"] = d["ataque"] + d["defensa"]
        return d.sort_values("fuerza_neta", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Evaluación
# --------------------------------------------------------------------------- #

def backtest(partidos: pd.DataFrame, xi: float = 0.0018,
             min_entrenamiento: int = 380, paso: int = 10) -> pd.DataFrame:
    """Validación temporal: predice cada partido usando sólo el pasado.

    Reajusta el modelo cada ``paso`` partidos en lugar de en cada uno, que es
    computacionalmente inviable y apenas cambia el resultado.
    """
    filas = []
    modelo = None
    for i in range(min_entrenamiento, len(partidos)):
        if modelo is None or (i - min_entrenamiento) % paso == 0:
            historico = partidos.iloc[:i]
            modelo = DixonColes(xi=xi).ajustar(
                historico, referencia=partidos.iloc[i]["fecha"])

        p = partidos.iloc[i]
        if p["local"] not in modelo.ataque or p["visitante"] not in modelo.ataque:
            continue   # recién ascendido sin historial en la ventana
        pred = modelo.predecir(p["local"], p["visitante"])
        filas.append({**pred,
                      "fecha": p["fecha"],
                      "temporada": p["temporada"],
                      "goles_local_real": p["goles_local"],
                      "goles_visitante_real": p["goles_visitante"],
                      "resultado_real": p["resultado"]})
    return pd.DataFrame(filas)


def metricas(pred: pd.DataFrame) -> dict:
    """Log-loss y Brier multiclase sobre el mercado 1X2, con dos referencias."""
    P = pred[["p_local", "p_empate", "p_visitante"]].to_numpy()
    y = pd.get_dummies(pred["resultado_real"])[["L", "E", "V"]].to_numpy().astype(float)

    logloss = float(-np.mean(np.sum(y * np.log(np.clip(P, 1e-12, 1)), axis=1)))
    brier = float(np.mean(np.sum((P - y) ** 2, axis=1)))

    # Referencia 1: frecuencias históricas de la propia muestra
    base = y.mean(axis=0)
    ll_base = float(-np.mean(np.sum(y * np.log(base), axis=1)))
    brier_base = float(np.mean(np.sum((base - y) ** 2, axis=1)))

    acierto = float((P.argmax(axis=1) == y.argmax(axis=1)).mean())

    return {
        "n": len(pred),
        "log_loss": logloss,
        "log_loss_base": ll_base,
        "mejora_log_loss_%": (ll_base - logloss) / ll_base * 100,
        "brier": brier,
        "brier_base": brier_base,
        "acierto_%": acierto * 100,
    }


def calibracion(pred: pd.DataFrame, bins: int = 8) -> pd.DataFrame:
    """Compara probabilidad predicha con frecuencia observada, por tramos.

    Es la comprobación que decide si las probabilidades son creíbles: en los
    partidos donde el modelo dice 60 %, ¿ocurre el 60 % de las veces?
    """
    largo = pd.concat([
        pd.DataFrame({"p": pred["p_local"],     "ok": pred["resultado_real"] == "L"}),
        pd.DataFrame({"p": pred["p_empate"],    "ok": pred["resultado_real"] == "E"}),
        pd.DataFrame({"p": pred["p_visitante"], "ok": pred["resultado_real"] == "V"}),
    ])
    largo["tramo"] = pd.cut(largo["p"], np.linspace(0, 1, bins + 1))
    d = (largo.groupby("tramo", observed=True)
              .agg(n=("ok", "size"), predicha=("p", "mean"), observada=("ok", "mean"))
              .reset_index())
    d["error"] = d["observada"] - d["predicha"]
    return d


if __name__ == "__main__":
    partidos = cargar_resultados()
    print(f"{len(partidos)} partidos, de {partidos['fecha'].min():%Y-%m-%d} "
          f"a {partidos['fecha'].max():%Y-%m-%d}")

    modelo = DixonColes().ajustar(partidos)
    print(f"\nConvergió: {modelo.convergio} | "
          f"ventaja de local: exp({modelo.gamma:.3f}) = {np.exp(modelo.gamma):.3f}x | "
          f"rho = {modelo.rho:.4f}")

    print("\n--- Fuerzas (top 8) ---")
    print(modelo.fuerzas().head(8).to_string(index=False))
