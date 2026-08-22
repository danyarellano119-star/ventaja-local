"""Empareja cada equipo con su escudo en un repositorio público de logos.

El repositorio sólo contiene los clubes que están **ahora** en cada liga, así que
los recién descendidos no aparecen; para ésos la web recurre a su distintivo de
colores. El emparejado es tolerante porque cada fuente escribe los nombres a su
manera: «Manchester Utd» aquí, «Manchester United» allí, «PSG» frente a «Paris
Saint-Germain».
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

import requests

# Si la última consulta salió bien, queda aquí. Sirve de red: la API de GitHub
# sin credenciales admite 60 peticiones por hora, y quedarse sin ellas dejaría
# la web entera sin escudos hasta la siguiente ejecución.
CACHE = Path(__file__).resolve().parent.parent / "datos" / "escudos.json"

REPO = "luukhopman/football-logos"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@master/logos"

CARPETAS = {
    "premier": "England - Premier League",
    "laliga": "Spain - LaLiga",
    "bundesliga": "Germany - Bundesliga",
    "seriea": "Italy - Serie A",
    "ligue1": "France - Ligue 1",
    # Ligas sin xG que sí tienen escudos en el repositorio. Brasil, Argentina,
    # Colombia y Turquía no están, y sus equipos usan el distintivo de colores.
    "eredivisie": "Netherlands - Eredivisie",
    "primeira": "Portugal - Liga Portugal",
    "superleague": "Greece - Super League 1",
    "premiership": "Scotland - Scottish Premiership",
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


_ARBOL: dict[str, dict[str, str]] | None = None


def _arbol() -> dict[str, dict[str, str]]:
    """Todos los archivos del repositorio, agrupados por carpeta.

    Se pide el árbol entero de una vez en lugar de listar carpeta por carpeta:
    la API de GitHub sin credenciales admite 60 peticiones por hora, y con una
    llamada por liga se agotaba a mitad de ejecución dejando equipos sin escudo.
    """
    global _ARBOL
    if _ARBOL is not None:
        return _ARBOL

    _ARBOL = {}
    try:
        # En GitHub Actions hay credenciales disponibles y el límite sube de 60
        # peticiones por hora a 5.000.
        cab = {}
        ficha = os.environ.get("GITHUB_TOKEN")
        if ficha:
            cab["Authorization"] = f"Bearer {ficha}"
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/git/trees/master?recursive=1",
            headers=cab, timeout=60)
        if r.status_code == 200:
            for hoja in r.json().get("tree", []):
                ruta = hoja.get("path", "")
                if not ruta.startswith("logos/") or not ruta.lower().endswith(".png"):
                    continue
                partes = ruta.split("/")
                if len(partes) != 3:
                    continue
                _, carpeta, archivo = partes
                _ARBOL.setdefault(carpeta, {})[_clave(archivo.rsplit(".", 1)[0])] = archivo
    except Exception as e:
        print(f"    [aviso] no se pudo leer el repositorio de escudos: {type(e).__name__}")

    if _ARBOL:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_ARBOL, ensure_ascii=False), encoding="utf-8")
    elif CACHE.exists():
        # Sin respuesta: se tira de lo guardado antes que dejarlo todo vacío
        try:
            _ARBOL = json.loads(CACHE.read_text(encoding="utf-8"))
            print("    [aviso] usando la lista de escudos guardada")
        except Exception:
            pass
    return _ARBOL


def _listar(carpeta: str) -> dict[str, str]:
    """Archivos de escudos de una liga, indexados por nombre normalizado."""
    return _arbol().get(carpeta, {})


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


# --------------------------------------------------------------------------- #
# Logos de las competiciones
# --------------------------------------------------------------------------- #

# Identificadores de TheSportsDB, que publica los escudos de cada competición
_ID_COMPETICION = {
    "premier": 4328, "laliga": 4335, "bundesliga": 4331,
    "seriea": 4332, "ligue1": 4334,
    "Champions League": 4480, "Europa League": 4481,
    "Conference League": 5071,
    "eredivisie": 4337, "primeira": 4344, "brasileirao": 4351,
    "superlig": 4339, "superleague": 4336, "premiership": 4330,
    "argentina": 4406, "colombia": 4497,
}

# Último enlace conocido de cada uno. Sirve de red por si la consulta falla:
# la web nunca se queda sin logos, aunque la fuente esté caída.
_RESPALDO = {
    "premier": "gasy9d1737743125", "laliga": "ja4it51687628717",
    "bundesliga": "teqh1b1679952008", "seriea": "67q3q21679951383",
    "ligue1": "9f7z9d1742983155", "Champions League": "facv1u1742998896",
    "Europa League": "mlsr7d1718774547", "Conference League": "ymfo5j1718775759",
}
_BASE = "https://r2.thesportsdb.com/images/media/league/badge/"


CACHE_LOGOS = CACHE.with_name("logos_competiciones.json")
DIAS_LOGOS = 7    # cada cuánto merece la pena volver a preguntar


def logos_competiciones() -> dict[str, str]:
    """Devuelve {clave de competición: dirección de su logo}.

    Los enlaces llevan una marca de tiempo y cambian si la fuente vuelve a subir
    la imagen, pero eso pasa como mucho una vez al año. Consultarlos en cada
    ejecución costaba nueve segundos para nada, así que se refrescan una vez por
    semana y el resto del tiempo se usa lo guardado.
    """
    import time as _t
    if CACHE_LOGOS.exists():
        edad = _t.time() - CACHE_LOGOS.stat().st_mtime
        if edad < DIAS_LOGOS * 86400:
            try:
                return json.loads(CACHE_LOGOS.read_text(encoding="utf-8"))
            except Exception:
                pass

    salida: dict[str, str] = {}
    for clave, ident in _ID_COMPETICION.items():
        url = ""
        try:
            r = requests.get(
                f"https://www.thesportsdb.com/api/v1/json/3/lookupleague.php?id={ident}",
                timeout=30)
            if r.status_code == 200:
                ficha = (r.json().get("leagues") or [{}])[0] or {}
                url = ficha.get("strBadge") or ficha.get("strLogo") or ""
        except Exception:
            pass
        if not url and clave in _RESPALDO:
            url = _BASE + _RESPALDO[clave] + ".png"
        if url:
            salida[clave] = url

    if salida:
        CACHE_LOGOS.parent.mkdir(parents=True, exist_ok=True)
        CACHE_LOGOS.write_text(json.dumps(salida, ensure_ascii=False, indent=0),
                               encoding="utf-8")
    return salida
