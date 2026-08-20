"""Descarga las estadísticas de equipo de la Premier League 2025/26 desde FBref.

FBref publica varias de sus tablas dentro de comentarios HTML para dificultar el
scraping ingenuo; este script las descomenta antes de parsear. Cada tabla se
guarda como CSV en ``datos/`` usando los atributos ``data-stat`` de FBref como
nombres de columna, que son estables entre temporadas.

Uso:
    python scripts/descargar_fbref.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup, Comment

URL = "https://fbref.com/en/comps/9/2025-2026/2025-2026-Premier-League-Stats"

# FBref rechaza peticiones sin un User-Agent de navegador.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# id de tabla en FBref -> nombre de archivo de salida
TABLAS = {
    "results2025-202691_overall":     "clasificacion",
    "results2025-202691_home_away":   "clasificacion_local_visitante",
    "stats_squads_standard_for":      "standard_favor",
    "stats_squads_standard_against":  "standard_contra",
    "stats_squads_keeper_for":        "portero_favor",
    "stats_squads_keeper_against":    "portero_contra",
    "stats_squads_shooting_for":      "tiros_favor",
    "stats_squads_shooting_against":  "tiros_contra",
    "stats_squads_playing_time_for":  "minutos_favor",
    "stats_squads_playing_time_against": "minutos_contra",
    "stats_squads_misc_for":          "misc_favor",
    "stats_squads_misc_against":      "misc_contra",
}

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "datos"


def descargar(url: str, reintentos: int = 3) -> str:
    """Descarga el HTML, reintentando con espera creciente ante rate limiting."""
    for intento in range(1, reintentos + 1):
        r = requests.get(url, headers=HEADERS, timeout=45)
        if r.status_code == 200:
            return r.text
        if r.status_code == 429:
            espera = 20 * intento
            print(f"  429 rate limit; esperando {espera}s...", file=sys.stderr)
            time.sleep(espera)
            continue
        r.raise_for_status()
    raise RuntimeError(f"No se pudo descargar {url} tras {reintentos} intentos")


def sopa_completa(html: str) -> BeautifulSoup:
    """Parsea el HTML incorporando las tablas ocultas en comentarios."""
    sopa = BeautifulSoup(html, "lxml")
    for c in sopa.find_all(string=lambda t: isinstance(t, Comment)):
        if "<table" in c:
            c.replace_with(BeautifulSoup(c, "lxml"))
    return sopa


def tabla_a_df(tabla) -> pd.DataFrame:
    """Convierte una <table> de FBref en DataFrame usando los data-stat como columnas."""
    filas = []
    for tr in tabla.select("tbody tr"):
        # FBref intercala filas de cabecera dentro del tbody
        if "thead" in (tr.get("class") or []):
            continue
        celdas = tr.find_all(["th", "td"])
        if not celdas:
            continue
        fila = {}
        for celda in celdas:
            clave = celda.get("data-stat")
            if not clave:
                continue
            fila[clave] = celda.get_text(strip=True)
        if fila:
            filas.append(fila)
    df = pd.DataFrame(filas)

    # Conversión numérica: todo lo que no sea texto identificativo
    texto = {"team", "squad", "comp_level", "lg_finish", "notes", "top_team_scorers",
             "top_keeper", "attendance_per_g"}
    for col in df.columns:
        if col in texto:
            continue
        limpia = df[col].str.replace(",", "", regex=False).str.replace("%", "", regex=False)
        convertida = pd.to_numeric(limpia, errors="coerce")
        # Sólo se sustituye si la conversión no destruye información
        if convertida.notna().sum() >= df[col].str.strip().ne("").sum():
            df[col] = convertida
    return df


def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    print(f"Descargando {URL}")
    sopa = sopa_completa(descargar(URL))

    guardadas, faltantes = [], []
    for id_tabla, nombre in TABLAS.items():
        tabla = sopa.find("table", id=id_tabla)
        if tabla is None:
            faltantes.append(id_tabla)
            continue
        df = tabla_a_df(tabla)
        destino = SALIDA / f"{nombre}.csv"
        df.to_csv(destino, index=False, encoding="utf-8-sig")
        guardadas.append((nombre, df.shape))
        print(f"  [ok] {nombre:32s} {df.shape[0]:3d} filas x {df.shape[1]:3d} col")

    if faltantes:
        print("\nNo encontradas:", ", ".join(faltantes), file=sys.stderr)

    print(f"\n{len(guardadas)} tablas guardadas en {SALIDA}")


if __name__ == "__main__":
    main()
