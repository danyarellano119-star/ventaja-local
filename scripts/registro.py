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


def _id(liga: str, fecha: str, local: str, visita: str, norm=None) -> str:
    """Identificador estable de un partido.

    Las dos fuentes escriben los clubes distinto —«Hull» frente a «Hull City
    AFC»—, así que el nombre se reduce con el mismo normalizador que usa el
    resto del programa. Sin eso, el pronóstico y el resultado del mismo partido
    acababan en dos fichas separadas y ninguna se resolvía nunca.
    """
    f = norm or _clave
    return f"{liga}|{fecha}|{f(local)}|{f(visita)}"


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
                       partidos: list[dict], equipos: dict, norm=None) -> int:
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
        # Para mostrar se guarda el nombre bonito; para identificar, la clave.
        local = equipos.get(p["l"], {}).get("nombre", p["l"])
        visita = equipos.get(p["v"], {}).get("nombre", p["v"])
        ident = _id(clave_liga, p["fecha"], p["l"], p["v"], norm)
        if ident in registro:
            continue
        registro[ident] = {
            "liga": nombre_liga, "clave_liga": clave_liga,
            "fecha": p["fecha"], "l": local, "v": visita,
            "clave_l": p["l"], "clave_v": p["v"],
            "pl": round(pr["pl"], 4), "pe": round(pr["pe"], 4),
            "pv": round(pr["pv"], 4),
            # Los demás mercados que la ficha del partido enseña, para poder
            # comprobarlos uno a uno cuando haya resultado.
            "o15": round(pr["o15"], 4), "o25": round(pr["o25"], 4),
            "o35": round(pr["o35"], 4), "btts": round(pr["btts"], 4),
            "marcador": pr["marcador"], "p_marcador": round(pr["p_marcador"], 4),
            "publicado": ahora,
        }
        nuevos += 1
    return nuevos


def resolver(registro: dict, clave_liga: str, jugados: list[dict],
             norm=None, mismo=None) -> int:
    """Anota el resultado de los partidos que ya se jugaron.

    Sólo toca los que aún no tuvieran resultado; lo demás queda como estaba.

    Si el identificador exacto no aparece se busca por fecha con el comparador
    tolerante, porque el pronóstico pudo guardarse con el nombre del calendario
    («Hull City AFC») y el resultado llega con el de las estadísticas («Hull»).
    """
    pendientes_por_dia: dict[str, list] = {}
    if mismo:
        for k, f in registro.items():
            if f.get("clave_liga") == clave_liga and "real" not in f:
                pendientes_por_dia.setdefault(f["fecha"], []).append(k)

    resueltos = 0
    for m in jugados:
        ident = _id(clave_liga, m["datetime"][:10],
                    m["h"]["title"], m["a"]["title"], norm)
        ficha = registro.get(ident)
        if ficha is None and mismo:
            for k in pendientes_por_dia.get(m["datetime"][:10], []):
                cand = registro[k]
                if (mismo(cand["clave_l"], m["h"]["title"])
                        and mismo(cand["clave_v"], m["a"]["title"])):
                    ficha = cand
                    break
        if not ficha or "real" in ficha:
            continue
        gl, gv = int(m["goals"]["h"]), int(m["goals"]["a"])
        ficha["gl"], ficha["gv"] = gl, gv
        ficha["real"] = "L" if gl > gv else "E" if gl == gv else "V"
        # Qué ocurrió de verdad en cada mercado. Se guarda resuelto y no se
        # recalcula en la web: así lo que se enseña es lo que se apuntó.
        ficha["ok_o15"] = (gl + gv) > 1.5
        ficha["ok_o25"] = (gl + gv) > 2.5
        ficha["ok_o35"] = (gl + gv) > 3.5
        ficha["ok_btts"] = gl > 0 and gv > 0
        ficha["ok_marcador"] = ficha.get("marcador") == f"{gl}-{gv}"
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
        "o15": f.get("o15"), "o25": f.get("o25"), "o35": f.get("o35"),
        "btts": f.get("btts"), "marcador": f.get("marcador"),
        "p_marcador": f.get("p_marcador"),
        "ok_o15": f.get("ok_o15"), "ok_o25": f.get("ok_o25"),
        "ok_o35": f.get("ok_o35"), "ok_btts": f.get("ok_btts"),
        "ok_marcador": f.get("ok_marcador"),
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
