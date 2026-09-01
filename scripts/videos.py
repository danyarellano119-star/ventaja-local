"""Vídeos oficiales de cada competición, para poder verlos desde la web.

La fuente son los canales de YouTube de las propias ligas, leídos por el RSS
público que YouTube publica de cada canal. Se eligió así por tres motivos:

* **No hace falta ninguna clave.** La API de datos de YouTube exige una y tiene
  cupo diario; este RSS es abierto y no lo tiene.
* **Es contenido oficial.** Nada de reediciones de terceros: el vídeo lo sube
  la liga, y es ella quien decide si permite incrustarlo. Enlazar a otra cosa
  sería colgar en la web material de dudosa procedencia.
* **Se puede comprobar.** Cada canal se verificó leyendo su RSS: los que
  llevaban años sin publicar —había uno de «championsleague» cuyo último vídeo
  era de 2006— se descartaron.

El RSS sólo devuelve los quince vídeos más recientes, así que un solo vistazo
da poca cosa. Como la web se actualiza cada hora, lo que se lee se **acumula**
en un archivo propio: en unos días hay biblioteca, y casi nada se escapa.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "videos.json"

# Canal oficial de cada competición. Cada identificador se comprobó leyendo el
# título y las fechas de su RSS: todos publican a diario. Las competiciones que
# no están aquí no tienen canal verificado y en la web caen en el buscador.
CANALES = {
    "premier":    ("UCG5qGWdu8nIRZqJ_GgDwQ-w", "Premier League"),
    "laliga":     ("UCTv-XvfzLX3i4IGWAm4sbmA", "LALIGA EA SPORTS"),
    "bundesliga": ("UC6UL29enLNe4mqwTfAyeNuw", "Bundesliga"),
    "seriea":     ("UCBJeMCIeLQos7wacox4hmLQ", "Serie A"),
    "ligue1":     ("UCQsH5XtIc9hONE1BQjucM0g", "Ligue 1"),
}

RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
DIAS = 150      # cuánto se guarda un vídeo antes de caducar
TOPE = 900      # y cuántos como mucho, para no engordar la página


def _texto(bruto: str) -> str:
    """Deshace las entidades XML del título."""
    for a, b in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")]:
        bruto = bruto.replace(a, b)
    return bruto.strip()


def clave(texto: str) -> str:
    """Título sin acentos ni signos, para poder buscar equipos dentro."""
    s = unicodedata.normalize("NFKD", (texto or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _leer_canal(cid: str) -> list[dict]:
    try:
        r = requests.get(RSS.format(cid), timeout=45)
        if r.status_code != 200:
            return []
    except Exception:
        return []

    fuera = []
    for trozo in r.text.split("<entry>")[1:]:
        vid = re.search(r"<yt:videoId>([\w-]+)</yt:videoId>", trozo)
        tit = re.search(r"<title>(.*?)</title>", trozo, re.S)
        pub = re.search(r"<published>([^<]+)</published>", trozo)
        if not (vid and tit and pub):
            continue
        fuera.append({"id": vid.group(1), "t": _texto(tit.group(1)),
                      "f": pub.group(1)[:10]})
    return fuera


def recolectar(hoy: date | None = None) -> dict:
    """Lee los canales y devuelve la biblioteca acumulada, por competición."""
    hoy = hoy or datetime.now(timezone.utc).date()
    corte = (hoy - timedelta(days=DIAS)).isoformat()

    try:
        guardado = json.loads(ARCHIVO.read_text(encoding="utf-8")) \
            if ARCHIVO.exists() else {}
    except Exception:
        guardado = {}

    nuevos = 0
    for liga, (cid, canal) in CANALES.items():
        por_id = {v["id"]: v for v in guardado.get(liga, [])}
        for v in _leer_canal(cid):
            if v["id"] not in por_id:
                nuevos += 1
            por_id[v["id"]] = {**v, "c": canal}
        # Los más recientes primero, sin lo caducado y sin pasarse de tamaño
        vivos = [v for v in por_id.values() if v["f"] >= corte]
        vivos.sort(key=lambda v: v["f"], reverse=True)
        guardado[liga] = vivos[:TOPE]

    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO.write_text(json.dumps(guardado, ensure_ascii=False, indent=0,
                                  sort_keys=True), encoding="utf-8")
    total = sum(len(v) for v in guardado.values())
    print(f"    {total} vídeos oficiales guardados ({nuevos} nuevos)")
    return guardado
