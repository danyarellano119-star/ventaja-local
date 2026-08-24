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

# Dos tamaños. El grande es el fondo del escaparate: en pantallas de alta
# densidad, un ancho de 1200 se ampliaba casi al doble y se veía borroso, que
# era justo el problema. El pequeño es para las tarjetas del carrusel, donde
# cargar la grande sería tirar megas por gusto.
ANCHO = 1600
ANCHO_MINI = 500
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
# El escaparate ocupa el ancho de la página y en pantallas de alta densidad
# eso son unos 2200 píxeles reales. Aceptar originales de 1200 obligaba al
# navegador a ampliarlos, y ése era el motivo de que se vieran mal.
ANCHO_MINIMO = 1600
ALTO_MINIMO = 900
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


def _miniaturas(urls: list[str]) -> dict[str, dict]:
    """Tamaño real y direcciones de miniatura de cada archivo.

    Se pregunta a la API de Commons en vez de construir la dirección a mano.
    ``Special:FilePath?width=N`` parecía servir, pero no respeta el ancho: salta
    a tamaños fijos y por encima de cierto punto devuelve algo que ni siquiera
    es una imagen. La API sí da la miniatura exacta, y de paso el tamaño del
    original, que hace falta para descartar las que quedarían borrosas.
    """
    fuera: dict[str, dict] = {}
    api = "https://commons.wikimedia.org/w/api.php"
    archivos = [urllib.parse.unquote(u.rsplit("/", 1)[-1].split("?")[0]) for u in urls]
    for i in range(0, len(archivos), 40):
        lote = archivos[i:i + 40]
        try:
            r = requests.get(api, headers=AGENTE, timeout=90, params={
                "action": "query", "format": "json", "prop": "imageinfo",
                "iiprop": "url|size", "iiurlwidth": ANCHO,
                "titles": "|".join("File:" + a for a in lote)})
            if r.status_code != 200:
                continue
            for pag in (r.json().get("query", {}).get("pages") or {}).values():
                info = (pag.get("imageinfo") or [{}])[0]
                if not info.get("thumburl"):
                    continue
                grande = info["thumburl"]
                # Las direcciones de miniatura llevan el ancho en el nombre, así
                # que la pequeña se obtiene cambiando ese número.
                mini = grande.replace(f"/{info['thumbwidth']}px-",
                                      f"/{ANCHO_MINI}px-")
                fuera[pag["title"].replace("File:", "").replace(" ", "_")] = {
                    "grande": grande, "mini": mini,
                    "w": info["width"], "h": info["height"],
                }
        except Exception:
            pass
        time.sleep(0.5)
    return fuera


def _sirve(datos: dict) -> bool:
    """¿Esta foto aguanta usarse como fondo a pantalla completa?"""
    w, h = datos.get("w", 0), datos.get("h", 0)
    return (w >= ANCHO_MINIMO and h >= ALTO_MINIMO
            and w / max(h, 1) <= PROPORCION_MAXIMA)


# Un estadio de fútbol acoge conciertos, tenis, rugby y mítines. Buscar sólo
# por su nombre traía la final de la Copa Davis del Pierre Mauroy: el recinto
# correcto, pero con una pista de tenis dentro.
_OTROS_USOS = (
    "tennis", "davis", "rugby", "concert", "konzert", "concierto", "boxing",
    "athletics", "atletismo", "nfl", "cricket", "speedway", "motocross",
    "ice hockey", "festival", "wrestling", "olympic", "handball", "snow",
    "construction", "bau", "obras", "maqueta", "model", "plan", "map", "mapa",
)


def _es_de_futbol(titulo: str) -> bool:
    """Descarta las fotos del mismo recinto usadas para otra cosa."""
    t = titulo.lower()
    return not any(palabra in t for palabra in _OTROS_USOS)


def _buscar_en_commons(sede: str) -> dict | None:
    """Busca en Commons la mejor foto de un estadio por su nombre.

    Wikidata guarda una sola imagen por estadio y a veces es pequeña o vieja.
    Commons tiene decenas: el Old Trafford que enlazaba Wikidata no llegaba a
    1600 píxeles, y buscando aparece una de 4995. Se toma la más grande que
    cumpla las mismas reglas de nitidez y proporción.
    """
    api = "https://commons.wikimedia.org/w/api.php"
    # Dos intentos: primero con «stadium», que empuja hacia fotos del recinto
    # entero; si no sale nada aprovechable, con el nombre a secas, porque hay
    # estadios cuyas mejores fotos no llevan esa palabra en el título.
    mejores = []
    for consulta in (f"{sede} stadium filetype:bitmap", f"{sede} filetype:bitmap"):
        if mejores:
            break
        try:
            r = requests.get(api, headers=AGENTE, timeout=90, params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": consulta, "gsrnamespace": 6,
                "gsrlimit": 14, "prop": "imageinfo",
                "iiprop": "size|url", "iiurlwidth": ANCHO})
            if r.status_code != 200:
                continue
            for pag in (r.json().get("query", {}).get("pages") or {}).values():
                i = (pag.get("imageinfo") or [{}])[0]
                if not i.get("thumburl") or not _es_de_futbol(pag["title"]):
                    continue
                datos = {"w": i["width"], "h": i["height"]}
                if not _sirve(datos):
                    continue
                mejores.append({
                    "img": i["thumburl"],
                    "mini": i["thumburl"].replace(f"/{i['thumbwidth']}px-",
                                                  f"/{ANCHO_MINI}px-"),
                    "px": f"{i['width']}x{i['height']}",
                    "orden": i["width"] * i["height"],
                })
        except Exception:
            continue
        time.sleep(0.4)

    if not mejores:
        return None
    mejor = max(mejores, key=lambda x: x["orden"])
    mejor.pop("orden")
    return mejor


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
        # Medir, quedarse con las nítidas y guardar sus dos tamaños
        candidatas = [v["img"] for n, v in cache.items()
                      if n in pendientes and v.get("img")]
        if candidatas:
            info = _miniaturas(candidatas)
            descartadas = 0
            for n in pendientes:
                v = cache.get(n) or {}
                if not v.get("img"):
                    continue
                archivo = urllib.parse.unquote(
                    v["img"].rsplit("/", 1)[-1].split("?")[0]).replace(" ", "_")
                datos = info.get(archivo)
                if not datos or not _sirve(datos) or not _es_de_futbol(archivo):
                    # Se guarda el nombre: con él se puede buscar otra foto
                    cache[n] = {"nombre": v.get("nombre", "")}
                    descartadas += 1
                    continue
                v["img"] = datos["grande"]
                v["mini"] = datos["mini"]
                v["px"] = f"{datos['w']}x{datos['h']}"
            if descartadas:
                print(f"    {descartadas} fotos descartadas por baja resolución")

            # Segunda oportunidad: buscar en Commons una foto mejor del mismo
            # estadio. Es lo que rescata a los estadios famosos, cuya imagen en
            # Wikidata suele ser antigua y pequeña.
            rescatadas = 0
            for n in pendientes:
                v = cache.get(n) or {}
                if v.get("img") or not v.get("nombre"):
                    continue
                mejor = _buscar_en_commons(v["nombre"])
                if mejor:
                    cache[n] = {**mejor, "nombre": v["nombre"]}
                    rescatadas += 1
                time.sleep(0.6)
            if rescatadas:
                print(f"    {rescatadas} recuperadas buscando en Commons")

        for n in pendientes:
            if n not in fallidos:
                cache.setdefault(n, {})
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0,
                                    sort_keys=True), encoding="utf-8")

    return {n: cache[n] for n in equipos if cache.get(n, {}).get("img")}
