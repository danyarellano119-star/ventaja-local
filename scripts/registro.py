"""Guarda cada pronóstico publicado y lo compara con el resultado real.

La diferencia con el histórico de aciertos que ya había: aquél reconstruye a
posteriori qué habría dicho el modelo antes de cada partido. Es honesto, pero
alguien puede desconfiar, porque lo calcula el mismo programa que luego se
juzga a sí mismo.

Esto es otra cosa. Aquí se apunta la probabilidad **el día que se publicó**, con
su fecha y hora, antes de que se jugara el partido. Después, cuando el resultado
existe, se anota al lado. Nadie puede retocar un pronóstico a toro pasado porque
el archivo vive en el historial de Git: cada línea tiene su commit con su fecha.

Regla que hace que todo esto valga algo: **un pronóstico guardado no se
sobrescribe nunca**. Si el modelo cambia de opinión mañana, se respeta lo que
dijo ayer.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "pronosticos.json"

# Cuántos partidos resueltos se llevan a la web. Con más, el archivo crece sin
# que nadie los mire; el resto sigue en el historial para quien quiera auditarlo.
MOSTRAR = 300


def _clave(texto: str) -> str:
    s = unicodedata.normalize("NFKD", (texto or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def _id(liga: str, fecha: str, local: str, visita: str) -> str:
    return f"{liga}|{fecha}|{_clave(local)}|{_clave(visita)}"


def cargar() -> dict:
    if ARCHIVO.exists():
        try:
            return json.loads(ARCHIVO.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def guardar(registro: dict) -> None:
    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO.write_text(json.dumps(registro, ensure_ascii=False, indent=0,
                                  sort_keys=True), encoding="utf-8")


def anotar_pronosticos(registro: dict, clave_liga: str, nombre_liga: str,
                       partidos: list[dict], equipos: dict) -> int:
    """Apunta los pronósticos de los partidos que aún no estuvieran.

    Devuelve cuántos se han añadido. Los que ya existían se dejan intactos:
    ésa es toda la gracia del asunto.

    Y no se apunta nada de un partido que ya haya empezado. Sin esta regla, un
    fallo en el filtrado del calendario bastaría para colar «pronósticos»
    escritos después del pitido final, que es exactamente lo que este archivo
    existe para descartar.
    """
    nuevos = 0
    ahora_dt = datetime.now(timezone.utc)
    ahora = ahora_dt.strftime("%Y-%m-%dT%H:%MZ")
    hoy = ahora_dt.date().isoformat()

    for p in partidos:
        pr = p.get("prob")
        if not pr:
            continue

        # Con hora de saque conocida basta compararla; sin ella, se exige que
        # el partido sea de otro día, porque «hoy a las ocho» puede haber
        # pasado ya y no hay forma de saberlo.
        if p.get("utc"):
            try:
                saque = datetime.strptime(p["utc"], "%Y-%m-%dT%H:%MZ").replace(
                    tzinfo=timezone.utc)
                if saque <= ahora_dt:
                    continue
            except ValueError:
                if p["fecha"] <= hoy:
                    continue
        elif p["fecha"] <= hoy:
            continue
        local = equipos.get(p["l"], {}).get("nombre", p["l"])
        visita = equipos.get(p["v"], {}).get("nombre", p["v"])
        ident = _id(clave_liga, p["fecha"], local, visita)
        if ident in registro:
            continue
        registro[ident] = {
            "liga": nombre_liga, "clave_liga": clave_liga,
            "fecha": p["fecha"], "l": local, "v": visita,
            "pl": round(pr[0], 4), "pe": round(pr[1], 4), "pv": round(pr[2], 4),
            "publicado": ahora,
        }
        nuevos += 1
    return nuevos


def resolver(registro: dict, clave_liga: str, jugados: list[dict],
             bonito: dict) -> int:
    """Anota el resultado de los partidos que ya se jugaron.

    Sólo toca los que aún no tuvieran resultado; lo demás queda como estaba.
    """
    resueltos = 0
    for m in jugados:
        local = bonito.get(m["h"]["title"], m["h"]["title"])
        visita = bonito.get(m["a"]["title"], m["a"]["title"])
        ident = _id(clave_liga, m["datetime"][:10], local, visita)
        ficha = registro.get(ident)
        if not ficha or "real" in ficha:
            continue
        gl, gv = int(m["goals"]["h"]), int(m["goals"]["a"])
        ficha["gl"], ficha["gv"] = gl, gv
        ficha["real"] = "L" if gl > gv else "E" if gl == gv else "V"
        resueltos += 1
    return resueltos


def resumen(registro: dict) -> dict:
    """Lo que se enseña en la web: cuánto se acertó y con qué calibración."""
    pendientes = sum(1 for f in registro.values() if "real" not in f)
    filas = [f for f in registro.values() if "real" in f]
    if not filas:
        # Sin resultados todavía, pero conviene decir cuántos hay apuntados:
        # es la prueba de que los pronósticos existían antes de los partidos.
        return {"n": 0, "pendientes": pendientes}
    filas.sort(key=lambda f: (f["fecha"], f["l"]))

    def favorito(f):
        return max((("L", f["pl"]), ("E", f["pe"]), ("V", f["pv"])),
                   key=lambda x: x[1])

    aciertos = sum(1 for f in filas if favorito(f)[0] == f["real"])

    # Calibración: de los partidos donde dijimos «entre el 60 % y el 75 %»,
    # ¿cuántos ocurrieron de verdad? Es la prueba honesta de un pronóstico.
    tramos = []
    for lo, hi in [(0.0, .30), (.30, .45), (.45, .60), (.60, .75), (.75, 1.01)]:
        casos = []
        for f in filas:
            for etq, p in (("L", f["pl"]), ("E", f["pe"]), ("V", f["pv"])):
                if lo <= p < hi:
                    casos.append((p, f["real"] == etq))
        if len(casos) >= 10:
            tramos.append({
                "desde": round(lo * 100), "hasta": round(min(hi, 1.0) * 100),
                "n": len(casos),
                "dicho": round(sum(p for p, _ in casos) / len(casos) * 100, 1),
                "real": round(sum(1 for _, ok in casos if ok) / len(casos) * 100, 1),
            })

    ultimos = [{
        "f": f["fecha"], "liga": f["liga"], "l": f["l"], "v": f["v"],
        "gl": f["gl"], "gv": f["gv"], "real": f["real"],
        "pl": f["pl"], "pe": f["pe"], "pv": f["pv"],
        "ok": favorito(f)[0] == f["real"],
        "publicado": f.get("publicado", ""),
    } for f in filas[-MOSTRAR:]][::-1]

    return {
        "n": len(filas),
        "aciertos": aciertos,
        "pct": round(aciertos / len(filas) * 100, 1),
        "desde": filas[0]["fecha"],
        "tramos": tramos,
        "ultimos": ultimos,
        "pendientes": sum(1 for f in registro.values() if "real" not in f),
    }
