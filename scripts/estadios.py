"""La foto del estadio de cada equipo, para la portada de su partido.

Se usa como imagen de fondo en la ficha del partido, al estilo de las carátulas
de las plataformas de vídeo. La fuente es Wikidata, que enlaza el club con su
estadio y el estadio con una foto de Wikimedia Commons: licencia libre y autoría
conocida, que es la única forma de poner imágenes sin meterse en un lío.

No hay fotos de los partidos en sí. Ninguna fuente abierta las publica, y las
agencias que las tienen cobran por ellas. El estadio es lo más cercano y además
tiene sentido: es el sitio donde se va a jugar.

Cuando un club no tenga foto, la web dibuja un degradado con los colores de las
dos camisetas. Nunca se queda en blanco.
"""

from __future__ import annotations

import json
import time
import unicodedata
import urllib.parse
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "datos" / "estadios.json"
SPARQL = "https://query.wikidata.org/sparql"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"
AGENTE = {"User-Agent": "VentajaLocal/1.0 (estadisticas de futbol; proyecto personal)"}

# Ancho de la imagen que se pide. Es un fondo a pantalla completa, así que
# necesita más resolución que un escudo, pero pedirla original traería 8 MB.
ANCHO = 1200
LOTE = 20          # clubes por consulta; con más, la consulta expira (504)
DIAS = 30          # un estadio no cambia de foto todas las semanas

# Ya no hace falta acotar por país: se pregunta por el nombre oficial exacto,
# que es único. Acotarlo daba problemas porque los clubes ingleses figuran en
# Wikidata como del Reino Unido, no de Inglaterra, y la consulta expiraba al
# incluir un país tan grande. Se conserva la tabla por si vuelve a hacer falta.
PAISES = {
    "Inglaterra": ["Q21"],              # sólo Inglaterra: con el Reino Unido
                                        # entero la consulta expiraba
    "España": ["Q29"], "Alemania": ["Q183"], "Italia": ["Q38"],
    "Francia": ["Q142"], "Países Bajos": ["Q55"], "Portugal": ["Q45"],
    "Brasil": ["Q155"], "Turquía": ["Q43"], "Grecia": ["Q41"],
    "Escocia": ["Q22"], "Argentina": ["Q414"], "Colombia": ["Q739"],
    "Noruega": ["Q20"], "Suecia": ["Q34"], "Finlandia": ["Q33"],
    "Irlanda": ["Q27"], "Islandia": ["Q189"], "Estonia": ["Q191"],
    "Letonia": ["Q211"], "Lituania": ["Q37"], "Georgia": ["Q230"],
    "Ecuador": ["Q736"], "Paraguay": ["Q733"], "Japón": ["Q17"],
    "China": ["Q148"], "Nigeria": ["Q1033"],
}


# Nuestros nombres son los cortos de las estadísticas; Wikidata usa los
# oficiales. Para los clubes conocidos se dice explícitamente cuál es cuál, que
# es más fiable que confiar en los alias: los de Wikidata traen erratas.
OFICIAL = {
    "Arsenal": "Arsenal F.C.", "Chelsea": "Chelsea F.C.",
    "Liverpool": "Liverpool F.C.", "Manchester Utd": "Manchester United F.C.",
    "Manchester City": "Manchester City F.C.", "Tottenham": "Tottenham Hotspur F.C.",
    "Newcastle": "Newcastle United F.C.", "Everton": "Everton F.C.",
    "Aston Villa": "Aston Villa F.C.", "West Ham": "West Ham United F.C.",
    "Brighton": "Brighton & Hove Albion F.C.", "Fulham": "Fulham F.C.",
    "Nottingham": "Nottingham Forest F.C.", "Wolves": "Wolverhampton Wanderers F.C.",
    "Crystal Palace": "Crystal Palace F.C.", "Brentford": "Brentford F.C.",
    "Leeds United": "Leeds United F.C.", "Sunderland": "Sunderland A.F.C.",
    "Bournemouth": "AFC Bournemouth", "Burnley": "Burnley F.C.",
    "Real Madrid": "Real Madrid CF", "Barcelona": "FC Barcelona",
    "Atlético Madrid": "Atlético Madrid", "Sevilla": "Sevilla FC",
    "Valencia": "Valencia CF", "Villarreal": "Villarreal CF",
    "Athletic Club": "Athletic Bilbao", "Real Sociedad": "Real Sociedad",
    "Real Betis": "Real Betis", "Celta Vigo": "RC Celta de Vigo",
    "Bayern Munich": "FC Bayern Munich", "Dortmund": "Borussia Dortmund",
    "Leverkusen": "Bayer 04 Leverkusen", "RB Leipzig": "RB Leipzig",
    "Frankfurt": "Eintracht Frankfurt", "Stuttgart": "VfB Stuttgart",
    "Inter": "Inter Milan", "AC Milan": "AC Milan", "Juventus": "Juventus FC",
    "Napoli": "SSC Napoli", "Roma": "AS Roma", "Lazio": "SS Lazio",
    "Atalanta": "Atalanta BC", "Fiorentina": "ACF Fiorentina",
    "PSG": "Paris Saint-Germain F.C.", "Marseille": "Olympique de Marseille",
    "Lyon": "Olympique Lyonnais", "Monaco": "AS Monaco FC",
    "Lille": "Lille OSC", "Nice": "OGC Nice",
    "PSV": "PSV Eindhoven", "Ajax": "AFC Ajax", "Feyenoord": "Feyenoord",
    "Sporting Clube de Portugal": "Sporting CP", "FC Porto": "FC Porto",
    "SL Benfica": "S.L. Benfica",
    "CR Flamengo": "CR Flamengo", "SE Palmeiras": "Sociedade Esportiva Palmeiras",
}

# Un estadio de primera división tiene decenas de miles de asientos. Con este
# mínimo se caen solas las ciudades deportivas y los pabellones, que es lo que
# hacía aparecer al Barcelona con la Ciutat Esportiva en vez del Camp Nou.
AFORO_MINIMO = 10000

# Una foto que se estira a lo ancho de la pantalla necesita píxeles de sobra.
# Y no basta el ancho: el Turf Moor del Burnley venía a 1280x296, un panorama
# tan aplastado que al recortarlo para la portada quedaba irreconocible.
ANCHO_MINIMO = 1000
ALTO_MINIMO = 500
PROPORCION_MAXIMA = 2.6      # más ancha que esto, se descarta


def _clave(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", (nombre or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def _miniatura(url: str) -> str:
    return f"{COMMONS}{url.rsplit('/', 1)[-1]}?width={ANCHO}"


def _cargar() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _consultar(nombres: list[str], pais: str, fallidos: set) -> dict:
    """Pregunta por un lote de clubes de un mismo país.

    Se buscan tanto por el nombre oficial como por los alternativos, porque las
    estadísticas dicen «Arsenal» donde Wikidata dice «Arsenal Fútbol Club». Y se
    exige que el club sea de ese país, o se cuelan homónimos de otro continente.
    """
    hallados: dict[str, dict] = {}
    for i in range(0, len(nombres), LOTE):
        lote = nombres[i:i + LOTE]
        # Se pregunta por el nombre oficial cuando se conoce, y por el nuestro
        # en los demás casos; luego se traduce de vuelta.
        pedido = {OFICIAL.get(n, n): n for n in lote}
        valores = " ".join('"%s"@en' % n.replace('"', "") for n in pedido)
        # Se pide el aforo para quedarse con el recinto grande: muchos clubes
        # tienen también su ciudad deportiva declarada como sede, y el Barcelona
        # aparecía con la Ciutat Esportiva en lugar del Camp Nou.
        consulta = f"""SELECT ?l ?club ?nombreEstadio ?img ?aforo WHERE {{
          VALUES ?l {{ {valores} }}
          ?c wdt:P115 ?e .
          ?c rdfs:label ?l .
          ?e wdt:P18 ?img .
          OPTIONAL {{ ?e wdt:P1083 ?aforo }}
          OPTIONAL {{ ?e rdfs:label ?nombreEstadio .
                      FILTER(lang(?nombreEstadio) = "es") }}
          BIND(STR(?c) AS ?club)
        }}"""
        bien = False
        for intento in range(3):
            try:
                r = requests.get(SPARQL, params={"query": consulta, "format": "json"},
                                 headers=AGENTE, timeout=150)
                if r.status_code != 200:
                    time.sleep(4 * (intento + 1))
                    continue
                # Sólo el nombre oficial, no los alternativos: los alias de
                # Wikidata traen erratas —«Real Madrid» figura también como
                # alias del Barcelona— y con ellos se colaba el estadio ajeno.
                # De los recintos de un mismo club manda el de más aforo.
                for fila in r.json()["results"]["bindings"]:
                    nombre = fila["l"]["value"]
                    aforo = int(float((fila.get("aforo") or {}).get("value", 0) or 0))
                    if aforo < AFORO_MINIMO:
                        continue
                    nombre = pedido.get(nombre, nombre)
                    previo = hallados.get(nombre)
                    if previo and previo["aforo"] >= aforo:
                        continue
                    hallados[nombre] = {
                        "img": _miniatura(fila["img"]["value"]),
                        "nombre": (fila.get("nombreEstadio") or {}).get("value", ""),
                        "aforo": aforo,
                    }
                bien = True
                break
            except Exception:
                time.sleep(4 * (intento + 1))
        if not bien:
            print(f"    [aviso] estadios de {pais}: la consulta no respondió; "
                  f"se reintentará en la próxima ejecución")
            fallidos.update(lote)
        time.sleep(1.0)
    return hallados


def _medir(urls: list[str]) -> dict[str, tuple]:
    """Tamaño real de cada archivo, preguntando a Commons en bloque.

    Commons no amplía las imágenes: si el original mide menos de lo que se le
    pide, devuelve el original y el navegador lo estira. Por eso hay que mirar
    el tamaño antes de aceptar una foto, no después.
    """
    medidas: dict[str, tuple] = {}
    api = "https://commons.wikimedia.org/w/api.php"
    archivos = [u.rsplit("/", 1)[-1].split("?")[0] for u in urls]
    for i in range(0, len(archivos), 40):
        lote = archivos[i:i + 40]
        try:
            r = requests.get(api, headers=AGENTE, timeout=90, params={
                "action": "query", "format": "json", "prop": "imageinfo",
                "iiprop": "size",
                "titles": "|".join("File:" + urllib.parse.unquote(a) for a in lote)})
            if r.status_code != 200:
                continue
            for pag in (r.json().get("query", {}).get("pages") or {}).values():
                info = (pag.get("imageinfo") or [{}])[0]
                if info.get("width"):
                    titulo = pag["title"].replace("File:", "").replace(" ", "_")
                    medidas[titulo] = (info["width"], info["height"])
        except Exception:
            pass
        time.sleep(0.5)
    return medidas


def _sirve(url: str, medidas: dict) -> bool:
    """¿Esta foto aguanta usarse como fondo a pantalla completa?"""
    archivo = urllib.parse.unquote(url.rsplit("/", 1)[-1].split("?")[0])
    w, h = medidas.get(archivo.replace(" ", "_"), (0, 0))
    if not w:
        return True          # sin dato, se le da el beneficio de la duda
    return (w >= ANCHO_MINIMO and h >= ALTO_MINIMO
            and w / h <= PROPORCION_MAXIMA)


def mapear(ligas: dict) -> dict[str, dict]:
    """Devuelve {equipo: {img, nombre del estadio}}.

    Se refresca una vez al mes: las fotos de los estadios no cambian, y
    preguntar en cada ejecución sería gastar por gusto.
    """
    cache = _cargar()
    fresca = CACHE.exists() and time.time() - CACHE.stat().st_mtime < DIAS * 86400

    # Agrupados por país, que es lo que permite descartar homónimos
    por_pais: dict[str, list[str]] = {}
    equipos: list[str] = []
    vistos = set()
    for lg in ligas.values():
        for e in lg["equipos"].values():
            n = e["nombre"]
            if n not in vistos:
                vistos.add(n)
                equipos.append(n)
                por_pais.setdefault(lg["pais"], []).append(n)

    pendientes = [n for n in equipos if n not in cache]
    if pendientes and not fresca:
        faltan = set(pendientes)
        fallidos: set[str] = set()
        for pais, lista in por_pais.items():
            sueltos = [n for n in lista if n in faltan]
            if sueltos:
                cache.update(_consultar(sueltos, pais, fallidos))
        # Sólo se da por «sin foto» a quien la fuente contestó y no tenía. Si la
        # consulta falló, el club queda pendiente: marcarlo dejaría a una liga
        # entera sin fondo por un corte de un minuto.
        # Descartar las que quedarían borrosas al usarlas de fondo
        candidatas = [v["img"] for n, v in cache.items()
                      if n in pendientes and v.get("img")]
        if candidatas:
            medidas = _medir(candidatas)
            descartadas = 0
            for n in pendientes:
                v = cache.get(n) or {}
                if v.get("img") and not _sirve(v["img"], medidas):
                    cache[n] = {}
                    descartadas += 1
            if descartadas:
                print(f"    {descartadas} fotos descartadas por baja resolución")

        for n in pendientes:
            if n not in fallidos:
                cache.setdefault(n, {})
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0,
                                    sort_keys=True), encoding="utf-8")

    return {n: cache[n] for n in equipos if cache.get(n, {}).get("img")}
