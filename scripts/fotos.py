"""Busca la foto de cada jugador y la guarda en caché.

La fuente es Wikidata, que enlaza a imágenes de Wikimedia Commons. Se eligió por
tres motivos: permite preguntar por cientos de jugadores en una sola consulta,
no corta por exceso de peticiones, y las imágenes tienen licencia libre y autoría
conocida, cosa que no ocurre con las bases de datos deportivas al uso.

El emparejado va en dos pasadas. La primera pide los nombres tal cual; la
segunda busca los que quedaron fuera, casi siempre porque llevan acentos que la
fuente de estadísticas no escribe (``Krstovic`` frente a ``Krstović``).

Sólo se acepta una foto cuando el nombre coincide. Es preferible dejar a alguien
sin foto que ponerle la cara de otro.
"""

from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "datos" / "fotos_jugadores.json"
CERROJO = CACHE.with_suffix(".lock")
SPARQL = "https://query.wikidata.org/sparql"
BUSCAR = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"

# Wikimedia pide identificarse; sin esto rechaza las peticiones automatizadas.
AGENTE = {"User-Agent": "VentajaLocal/1.0 (estadisticas de futbol; proyecto personal)"}

FUTBOLISTA = "wd:Q937857"   # «jugador de fútbol» en Wikidata
LOTE = 150                  # nombres por consulta; más allá la consulta expira
ANCHO = 200                 # las imágenes originales pesan megas: se piden en pequeño


def _clave(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre.lower().strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.replace("-", " ").replace("'", " ").replace(".", " ").split())


def _miniatura(url_commons: str) -> str:
    """Convierte el enlace al archivo original en uno a una miniatura."""
    archivo = url_commons.rsplit("/", 1)[-1]
    return f"{COMMONS}{archivo}?width={ANCHO}"


def _cargar() -> dict[str, str]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _por_lotes(nombres: list[str]) -> dict[str, str]:
    """Primera pasada: una consulta por cada lote de nombres."""
    hallados: dict[str, str] = {}
    for i in range(0, len(nombres), LOTE):
        lote = nombres[i:i + LOTE]
        valores = " ".join('"%s"@en' % n.replace('"', "") for n in lote)
        consulta = f"""SELECT ?l ?img WHERE {{
          VALUES ?l {{ {valores} }}
          ?p wdt:P106 {FUTBOLISTA} ; wdt:P18 ?img .
          {{ ?p rdfs:label ?l }} UNION {{ ?p skos:altLabel ?l }}
        }}"""
        try:
            r = requests.get(SPARQL, params={"query": consulta, "format": "json"},
                             headers=AGENTE, timeout=120)
            if r.status_code != 200:
                print(f"    [aviso] consulta {i // LOTE + 1}: HTTP {r.status_code}")
                continue
            for fila in r.json()["results"]["bindings"]:
                hallados.setdefault(fila["l"]["value"], _miniatura(fila["img"]["value"]))
        except Exception as e:
            print(f"    [aviso] consulta {i // LOTE + 1}: {type(e).__name__}")
        time.sleep(1.0)
    return hallados


def _rebuscar(nombre: str) -> str | None:
    """Segunda pasada: buscador de Wikidata, que sí ignora los acentos.

    Devuelve la dirección de la foto, "" si el jugador no tiene, o None si la
    consulta falló y conviene reintentarla en otra ejecución.
    """
    try:
        r = requests.get(BUSCAR, headers=AGENTE, timeout=45, params={
            "action": "wbsearchentities", "search": nombre, "language": "en",
            "type": "item", "limit": 5, "format": "json"})
        if r.status_code != 200:
            return None
        # Sólo sirve si el nombre coincide de verdad, ignorando acentos
        ids = [x["id"] for x in r.json().get("search", [])
               if _clave(x.get("label", "")) == _clave(nombre)]
        if not ids:
            return ""

        r2 = requests.get(BUSCAR, headers=AGENTE, timeout=45, params={
            "action": "wbgetentities", "ids": "|".join(ids[:5]),
            "props": "claims", "format": "json"})
        if r2.status_code != 200:
            return None

        candidatos = []
        for datos in (r2.json().get("entities") or {}).values():
            cl = datos.get("claims") or {}
            oficios = [c["mainsnak"]["datavalue"]["value"]["id"]
                       for c in cl.get("P106", [])
                       if c.get("mainsnak", {}).get("datavalue")]
            if "Q937857" not in oficios:
                continue
            for c in cl.get("P18", []):
                v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
                if v:
                    candidatos.append(v)
                    break
        # Varios futbolistas con el mismo nombre: sin forma segura de elegir
        if len(candidatos) != 1:
            return ""
        return _miniatura(candidatos[0].replace(" ", "_"))
    except Exception:
        return None


def mapear(ligas: dict) -> dict[str, str]:
    """Devuelve {nombre del jugador: dirección de su foto}."""
    cache = _cargar()

    # Dos ejecuciones a la vez se pisarían la caché: la segunda la cargaría a
    # medias y luego la reescribiría entera, borrando lo que llevara la primera.
    if CERROJO.exists() and time.time() - CERROJO.stat().st_mtime < 3600:
        print("    ya hay otra actualización en marcha; se usa la caché tal cual")
        return {n: cache[n] for lg in ligas.values()
                for equipo in lg["equipos"].values()
                for j in (equipo.get("jug") or [])
                for n in [j.get("n")] if n and cache.get(n)}

    nombres: list[str] = []
    vistos = set()
    for lg in ligas.values():
        for equipo in lg["equipos"].values():
            for j in equipo.get("jug") or []:
                n = j.get("n")
                if n and n not in vistos:
                    vistos.add(n)
                    nombres.append(n)

    pendientes = [n for n in nombres if n not in cache]
    if pendientes:
        CERROJO.parent.mkdir(parents=True, exist_ok=True)
        CERROJO.write_text("en marcha", encoding="utf-8")
        try:
            hallados = _por_lotes(pendientes)
            cache.update(hallados)
            print(f"    primera pasada: {len(hallados)} de {len(pendientes)}")

            faltan = [n for n in pendientes if n not in cache]
            recuperados = 0
            for n in faltan:
                url = _rebuscar(n)
                if url is None:      # falló: se deja pendiente para otra vez
                    continue
                cache[n] = url
                if url:
                    recuperados += 1
                time.sleep(0.4)
            if faltan:
                print(f"    segunda pasada: {recuperados} de {len(faltan)} recuperados")

            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0,
                                        sort_keys=True), encoding="utf-8")
        finally:
            CERROJO.unlink(missing_ok=True)

    quedan = len([n for n in nombres if n not in cache])
    if quedan:
        print(f"    quedan {quedan} para la próxima ejecución")

    return {n: cache[n] for n in nombres if cache.get(n)}
