"""Empareja cada equipo con su escudo en un repositorio público de logos.

El repositorio sólo contiene los clubes que están **ahora** en cada liga, así que
los recién descendidos no aparecen; para ésos la web recurre a su distintivo de
colores. El emparejado es tolerante porque cada fuente escribe los nombres a su
manera: «Manchester Utd» aquí, «Manchester United» allí, «PSG» frente a «Paris
Saint-Germain».
"""

from __future__ import annotations

import re
import unicodedata

import requests

REPO = "luukhopman/football-logos"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@master/logos"

CARPETAS = {
    "premier": "England - Premier League",
    "laliga": "Spain - LaLiga",
    "bundesliga": "Germany - Bundesliga",
    "seriea": "Italy - Serie A",
    "ligue1": "France - Ligue 1",
}

# Palabras que sobran al comparar nombres de clubes
_RUIDO = {"fc", "cf", "afc", "sk", "ac", "as", "ss", "ssc", "sc", "rc", "cd",
          "club", "de", "the", "calcio", "cp", "ud", "sd", "rcd", "cfc", "bc",
          "fk", "vfb", "vfl", "tsg", "sv", "bsc", "fsv", "borussia", "olympique",
          "stade", "ogc", "rb", "1899", "1904", "1913", "1846", "05", "04", "1"}

# Casos donde las dos fuentes usan nombres que no se parecen lo suficiente
_ALIAS = {
    "Manchester Utd": "Manchester United",
    "PSG": "Paris Saint-Germain",
    "Lyon": "Olympique Lyon",
    "Nice": "OGC Nice",
    "Marseille": "Olympique Marseille",
    "Dep. A Coruña": "Deportivo A Coruña",
    "Alavés": "Deportivo Alavés",
    "Colonia": "1.FC Köln",
    "M'gladbach": "Borussia Mönchengladbach",
    "Leverkusen": "Bayer 04 Leverkusen",
    "Dortmund": "Borussia Dortmund",
    "Frankfurt": "Eintracht Frankfurt",
    "Stuttgart": "VfB Stuttgart",
    "RB Leipzig": "RB Leipzig",
    "Verona": "Hellas Verona",
    "AC Milan": "AC Milan",
    "Inter": "Inter Milan",
    "Racing Sant": "Racing Santander",
}


def _clave(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(p for p in s.split() if p not in _RUIDO and len(p) > 1)


def _listar(carpeta: str) -> dict[str, str]:
    """Archivos de escudos de una liga, indexados por nombre normalizado."""
    url = f"https://api.github.com/repos/{REPO}/contents/logos/{carpeta}"
    try:
        r = requests.get(url, timeout=45)
        if r.status_code != 200:
            return {}
        return {_clave(x["name"].rsplit(".", 1)[0]): x["name"]
                for x in r.json() if x["name"].lower().endswith(".png")}
    except Exception:
        return {}


def mapear(ligas: dict) -> dict[str, str]:
    """Devuelve {nombre del equipo: dirección de su escudo}.

    Los equipos que el repositorio no incluye simplemente no aparecen en el
    resultado, y la web les pinta su distintivo de colores.
    """
    salida: dict[str, str] = {}

    for clave_liga, carpeta in CARPETAS.items():
        if clave_liga not in ligas:
            continue
        archivos = _listar(carpeta)
        if not archivos:
            continue

        for equipo in ligas[clave_liga]["equipos"].values():
            nombre = equipo["nombre"]
            candidatos = [_ALIAS.get(nombre, nombre), nombre, equipo["clave"]]

            elegido = None
            for candidato in candidatos:
                k = _clave(candidato)
                if k in archivos:
                    elegido = archivos[k]
                    break
                # Coincidencia parcial: una de las dos contiene a la otra
                for ka, archivo in archivos.items():
                    if not ka or not k:
                        continue
                    if ka == k or ka.startswith(k) or k.startswith(ka) or \
                       (len(k) > 4 and k in ka) or (len(ka) > 4 and ka in k):
                        elegido = archivo
                        break
                if elegido:
                    break

            if elegido:
                # jsDelivr necesita los espacios codificados
                ruta = f"{carpeta}/{elegido}".replace(" ", "%20")
                salida[nombre] = f"{CDN}/{ruta}"

    return salida
