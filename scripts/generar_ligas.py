"""Construye el dataset de las cinco grandes ligas para la web.

Cruza dos fuentes con nomenclaturas distintas:

- **Understat** aporta las fuerzas de ataque y defensa estimadas sobre el xG de
  la temporada 2025/26, más los agregados de cada equipo.
- **FBref** aporta el calendario de la temporada 2026/27.

Los equipos recién ascendidos no tienen historial en la categoría, así que
reciben el perfil medio de los tres peores de la temporada anterior, que es la
aproximación habitual. Quedan marcados como tales para poder avisarlo en la
ficha del partido.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "web" / "datos_ligas.json"

# ── Fuerzas por liga: equipo;atq;def;pts;pj;gf;gc;xg;xga ─────────────────── #
FUERZAS = {
"premier": ("Premier League", "Inglaterra", 0.2726, """
Arsenal;0.2953;0.5802;85;38;71;27;77.5;33.1
Aston Villa;-0.0182;-0.1411;65;38;56;49;56.2;56.7
Bournemouth;0.2243;-0.0213;57;38;58;54;66.8;56.8
Brentford;0.1260;-0.1468;53;38;55;52;66.3;59.1
Brighton;0.0814;-0.0213;53;38;52;46;58.4;53.8
Burnley;-0.4374;-0.5391;22;38;38;75;36.9;82.6
Chelsea;0.2445;-0.1221;52;38;58;52;72.2;58.1
Crystal Palace;0.0968;-0.1872;45;38;41;51;62.5;60.0
Everton;-0.0999;-0.1808;49;38;47;50;49.9;60.0
Fulham;-0.1005;-0.1620;52;38;47;51;49.6;60.8
Leeds;-0.0356;-0.0334;47;38;49;56;59.8;56.3
Liverpool;0.2088;0.0439;60;38;63;53;67.3;53.9
Manchester City;0.4097;0.0442;78;38;77;35;79.4;46.5
Manchester United;0.2750;0.0257;71;38;69;50;72.2;50.3
Newcastle United;-0.0377;-0.0916;49;38;53;55;60.6;57.3
Nottingham Forest;-0.1457;-0.2312;44;38;48;51;48.9;64.7
Sunderland;-0.2322;-0.1226;54;38;42;48;43.1;60.3
Tottenham;-0.2311;-0.0618;41;38;48;57;46.8;55.6
West Ham;-0.1510;-0.2723;39;38;46;65;49.6;68.1
Wolverhampton Wanderers;-0.4725;-0.2804;20;38;27;68;38.4;68.3
"""),
"laliga": ("LaLiga", "España", 0.3852, """
Alaves;-0.0259;0.0273;43;38;44;56;53.6;53.0
Athletic Club;-0.0074;0.2052;45;38;43;58;55.7;47.1
Atletico Madrid;0.2309;0.0650;69;38;62;44;68.1;53.1
Barcelona;0.6363;0.0974;94;38;95;36;99.7;50.7
Celta Vigo;-0.0620;0.0443;54;38;53;48;52.0;56.3
Elche;-0.1899;-0.1436;43;38;49;57;48.2;68.9
Espanyol;-0.0338;-0.1919;46;38;43;55;52.4;62.7
Getafe;-0.5774;0.2012;51;38;32;38;34.0;48.3
Girona;-0.0423;-0.2341;41;38;39;55;52.1;65.8
Levante;0.0624;-0.1852;42;38;47;61;59.6;66.7
Mallorca;-0.2812;-0.2139;42;38;47;57;47.5;65.8
Osasuna;-0.0845;0.0729;42;38;44;50;51.5;51.6
Rayo Vallecano;0.1072;-0.0473;50;38;41;44;60.0;56.3
Real Betis;0.1628;0.0633;60;38;59;48;61.3;50.3
Real Madrid;0.4412;0.1537;86;38;77;35;81.5;45.0
Real Oviedo;-0.2956;-0.2202;29;38;26;60;41.5;67.7
Real Sociedad;0.0977;-0.0346;46;38;59;61;59.7;60.5
Sevilla;-0.3472;-0.0527;43;38;46;60;39.6;61.3
Valencia;-0.0401;0.0131;49;38;46;55;54.0;55.5
Villarreal;0.2486;0.0371;72;38;72;46;68.8;53.7
"""),
"bundesliga": ("Bundesliga", "Alemania", 0.3280, """
Augsburg;-0.0486;-0.3503;43;34;45;61;51.3;65.5
Bayer Leverkusen;0.3061;-0.0542;59;34;68;47;75.3;51.0
Bayern Munich;0.7100;0.0930;89;34;122;36;104.8;44.7
Borussia Dortmund;0.1990;0.1355;73;34;70;34;67.8;42.8
Borussia M.Gladbach;-0.2055;-0.1011;38;34;42;53;46.7;56.6
Eintracht Frankfurt;-0.0800;-0.0867;44;34;61;65;51.0;55.6
FC Cologne;-0.0118;-0.3113;32;34;49;63;53.8;61.8
FC Heidenheim;-0.2075;-0.4415;26;34;41;72;50.0;71.6
Freiburg;-0.1701;-0.0841;47;34;51;57;50.1;53.6
Hamburger SV;-0.2185;-0.2991;38;34;40;54;43.8;66.1
Hoffenheim;0.1474;-0.1402;61;34;65;52;63.1;54.1
Mainz 05;0.0351;-0.2596;40;34;44;53;63.3;61.9
RasenBallsport Leipzig;0.3521;-0.0353;65;34;66;47;75.2;53.5
St. Pauli;-0.5308;-0.3355;26;34;29;60;34.0;66.8
Union Berlin;-0.1906;-0.0532;39;34;44;58;48.4;56.1
VfB Stuttgart;0.2393;-0.0490;62;34;71;49;68.3;53.4
Werder Bremen;-0.2501;-0.2402;32;34;37;60;42.3;61.9
Wolfsburg;-0.0753;-0.3516;29;34;45;69;52.6;65.1
"""),
"seriea": ("Serie A", "Italia", 0.1972, """
AC Milan;0.2692;0.1248;70;38;53;35;64.8;46.1
Atalanta;0.3373;-0.0509;59;38;51;36;68.6;51.7
Bologna;-0.0258;0.1025;56;38;49;46;50.0;50.9
Cagliari;-0.1556;-0.1819;43;38;40;53;43.7;60.4
Como;0.2905;0.2135;71;38;65;29;68.0;39.5
Cremonese;-0.3438;-0.1722;34;38;32;57;37.5;66.5
Fiorentina;0.0233;-0.1801;42;38;41;50;52.2;57.5
Genoa;-0.0235;-0.0895;41;38;41;51;48.1;55.7
Inter;0.5906;0.3604;87;38;89;35;87.1;35.8
Juventus;0.3711;0.3376;69;38;61;34;74.2;36.1
Lazio;-0.1855;-0.0048;54;38;41;40;46.2;49.0
Lecce;-0.3565;-0.2548;38;38;28;50;37.2;64.8
Napoli;0.1387;0.2464;76;38;58;36;57.8;41.6
Parma Calcio 1913;-0.4137;-0.1524;45;38;28;46;36.2;61.8
Pisa;-0.2734;-0.2805;18;38;26;71;42.2;66.5
Roma;0.2508;0.2037;73;38;59;31;62.6;42.6
Sassuolo;-0.0260;-0.1943;49;38;46;50;48.0;61.1
Torino;0.0118;-0.2012;45;38;44;63;51.6;60.4
Udinese;-0.0986;-0.1530;50;38;45;48;47.3;57.0
Verona;-0.3808;-0.0627;21;38;25;61;37.4;55.6
"""),
"ligue1": ("Ligue 1", "Francia", 0.3148, """
Angers;-0.4011;-0.2078;36;34;29;48;33.5;60.3
Auxerre;-0.1705;-0.0766;34;34;34;44;40.3;50.0
Brest;-0.0480;-0.1396;39;34;43;55;46.4;56.2
Le Havre;-0.2498;-0.1130;35;34;32;44;38.7;55.7
Lens;0.4309;0.1110;70;34;66;35;74.4;43.0
Lille;0.0833;0.2868;61;34;52;37;57.2;38.6
Lorient;-0.0196;-0.0863;45;34;48;51;49.1;52.9
Lyon;0.1077;-0.0334;60;34;53;40;55.2;48.0
Marseille;0.3890;-0.0106;59;34;63;45;69.4;49.7
Metz;-0.5259;-0.3157;17;34;32;76;33.6;65.0
Monaco;0.2475;-0.0728;54;34;60;54;61.4;51.2
Nantes;-0.4138;-0.0859;24;34;29;52;37.9;53.1
Nice;-0.0471;-0.2952;32;34;37;60;46.8;60.7
Paris FC;0.0415;-0.2162;44;34;47;50;49.7;56.6
Paris Saint Germain;0.4610;0.5648;76;34;74;29;75.6;33.6
Rennes;0.1070;-0.2782;59;34;59;50;54.5;60.2
Strasbourg;0.0904;0.0601;53;34;58;47;56.8;47.7
Toulouse;-0.0826;0.2045;45;34;47;46;46.6;44.4
"""),
}

# ── Calendario 2026/27: jornada;fecha;hora;local;visitante ───────────────── #
CALENDARIO = {
"premier": """
1;2026-08-21;20:00;Arsenal;Coventry City
1;2026-08-22;12:30;Hull City;Manchester Utd
1;2026-08-22;15:00;Ipswich Town;Sunderland
1;2026-08-22;15:00;Nottingham;Leeds United
1;2026-08-22;15:00;Everton;Crystal Palace
1;2026-08-22;17:30;Brentford;Tottenham
1;2026-08-23;14:00;Manchester City;Bournemouth
1;2026-08-23;14:00;Brighton;Aston Villa
1;2026-08-23;16:30;Newcastle;Liverpool
1;2026-08-24;20:00;Fulham;Chelsea
2;2026-08-28;20:00;Crystal Palace;Manchester City
2;2026-08-29;12:30;Liverpool;Nottingham
2;2026-08-29;15:00;Coventry City;Hull City
2;2026-08-29;15:00;Bournemouth;Everton
2;2026-08-29;17:30;Tottenham;Newcastle
2;2026-08-30;14:00;Chelsea;Brighton
2;2026-08-30;14:00;Sunderland;Fulham
2;2026-08-30;14:00;Leeds United;Brentford
2;2026-08-30;16:30;Manchester Utd;Ipswich Town
2;2026-08-31;20:00;Aston Villa;Arsenal
3;2026-09-04;20:00;Ipswich Town;Liverpool
3;2026-09-05;12:30;Newcastle;Bournemouth
3;2026-09-05;15:00;Nottingham;Tottenham
3;2026-09-05;15:00;Fulham;Crystal Palace
3;2026-09-05;15:00;Brentford;Sunderland
3;2026-09-05;15:00;Manchester City;Coventry City
3;2026-09-05;15:00;Brighton;Leeds United
3;2026-09-05;17:30;Hull City;Aston Villa
3;2026-09-06;14:00;Everton;Manchester Utd
3;2026-09-06;16:30;Arsenal;Chelsea
4;2026-09-12;15:00;Crystal Palace;Ipswich Town
4;2026-09-12;15:00;Aston Villa;Nottingham
4;2026-09-12;15:00;Liverpool;Fulham
4;2026-09-12;15:00;Chelsea;Hull City
4;2026-09-12;15:00;Bournemouth;Brentford
4;2026-09-12;17:30;Tottenham;Everton
4;2026-09-12;20:00;Sunderland;Arsenal
4;2026-09-13;14:00;Coventry City;Brighton
4;2026-09-13;16:30;Manchester Utd;Manchester City
4;2026-09-14;20:00;Leeds United;Newcastle
""",
"laliga": """
1;2026-08-15;19:30;Alavés;Getafe
1;2026-08-15;21:30;Sevilla;Rayo Vallecano
1;2026-08-16;17:00;Racing Sant;Villarreal
1;2026-08-16;19:00;Espanyol;Levante
1;2026-08-17;21:00;Dep. A Coruña;Elche
1;2026-08-19;21:00;Atlético Madrid;Málaga
1;2026-08-25;21:00;Valencia;Real Betis
1;2026-08-26;21:00;Real Madrid;Real Sociedad
1;2026-08-27;20:30;Celta Vigo;Osasuna
1;2026-08-27;21:00;Barcelona;Athletic Club
2;2026-08-20;21:00;Rayo Vallecano;Alavés
2;2026-08-21;21:00;Real Betis;Real Sociedad
2;2026-08-22;17:00;Athletic Club;Sevilla
2;2026-08-22;19:30;Valencia;Celta Vigo
2;2026-08-22;21:30;Espanyol;Real Madrid
2;2026-08-23;17:00;Atlético Madrid;Villarreal
2;2026-08-23;19:30;Getafe;Racing Sant
2;2026-08-23;21:30;Elche;Barcelona
2;2026-08-24;19:30;Osasuna;Levante
2;2026-08-24;21:30;Málaga;Dep. A Coruña
3;2026-08-28;19:00;Racing Sant;Elche
3;2026-08-28;21:30;Alavés;Villarreal
3;2026-08-29;17:00;Levante;Real Betis
3;2026-08-29;19:00;Real Sociedad;Espanyol
3;2026-08-29;21:30;Sevilla;Atlético Madrid
3;2026-08-30;17:00;Real Madrid;Málaga
3;2026-08-30;19:30;Dep. A Coruña;Valencia
3;2026-08-30;21:30;Celta Vigo;Athletic Club
3;2026-08-31;19:30;Osasuna;Getafe
3;2026-08-31;21:30;Barcelona;Rayo Vallecano
""",
"bundesliga": """
1;2026-08-28;20:30;Bayern Munich;Stuttgart
1;2026-08-29;15:30;RB Leipzig;Gladbach
1;2026-08-29;15:30;Elversberg;Leverkusen
1;2026-08-29;15:30;Mainz 05;Paderborn 07
1;2026-08-29;15:30;Union Berlin;Frankfurt
1;2026-08-29;15:30;Köln;Hoffenheim
1;2026-08-29;18:30;Dortmund;Hamburger SV
1;2026-08-30;15:30;Freiburg;Werder Bremen
1;2026-08-30;17:30;Augsburg;Schalke 04
2;2026-09-04;20:30;Stuttgart;Köln
2;2026-09-05;15:30;Hoffenheim;Dortmund
2;2026-09-05;15:30;Werder Bremen;RB Leipzig
2;2026-09-05;15:30;Leverkusen;Union Berlin
2;2026-09-05;15:30;Gladbach;Elversberg
2;2026-09-05;15:30;Paderborn 07;Freiburg
2;2026-09-05;18:30;Schalke 04;Bayern Munich
2;2026-09-06;15:30;Hamburger SV;Mainz 05
2;2026-09-06;17:30;Frankfurt;Augsburg
3;2026-09-11;20:30;Union Berlin;Schalke 04
3;2026-09-12;15:30;Mainz 05;Frankfurt
3;2026-09-12;15:30;Freiburg;Gladbach
3;2026-09-12;15:30;Dortmund;Paderborn 07
3;2026-09-12;15:30;Augsburg;Leverkusen
3;2026-09-12;15:30;Hoffenheim;Stuttgart
3;2026-09-12;18:30;Köln;Werder Bremen
3;2026-09-13;15:30;RB Leipzig;Hamburger SV
3;2026-09-13;17:30;Elversberg;Bayern Munich
""",
"seriea": """
1;2026-08-22;18:30;Udinese;Como
1;2026-08-22;18:30;Inter;Monza
1;2026-08-22;20:45;Parma;Cagliari
1;2026-08-22;20:45;Genoa;Napoli
1;2026-08-23;18:30;Frosinone;Juventus
1;2026-08-23;18:30;Venezia;Lecce
1;2026-08-23;20:45;Torino;Milan
1;2026-08-23;20:45;Atalanta;Sassuolo
1;2026-08-24;18:30;Bologna;Lazio
1;2026-08-24;20:45;Roma;Fiorentina
2;2026-08-28;20:45;Milan;Venezia
2;2026-08-29;18:30;Sassuolo;Torino
2;2026-08-29;18:30;Monza;Udinese
2;2026-08-29;18:30;Fiorentina;Frosinone
2;2026-08-29;20:45;Juventus;Parma
2;2026-08-30;18:30;Napoli;Como
2;2026-08-30;20:45;Cagliari;Inter
2;2026-08-30;20:45;Lazio;Genoa
2;2026-08-31;18:30;Lecce;Roma
2;2026-08-31;20:45;Atalanta;Bologna
3;2026-09-04;20:45;Genoa;Como
3;2026-09-05;15:00;Fiorentina;Torino
3;2026-09-05;18:00;Inter;Napoli
3;2026-09-05;20:45;Roma;Atalanta
3;2026-09-06;15:00;Parma;Monza
3;2026-09-06;15:00;Frosinone;Venezia
3;2026-09-06;18:00;Bologna;Sassuolo
3;2026-09-06;20:45;Juventus;Milan
3;2026-09-07;18:00;Cagliari;Lecce
3;2026-09-07;20:45;Udinese;Lazio
""",
"ligue1": """
1;2026-08-21;20:45;Marseille;Strasbourg
1;2026-08-22;17:15;Lens;Auxerre
1;2026-08-22;20:45;Nice;Lorient
1;2026-08-22;20:45;Le Mans;Brest
1;2026-08-22;20:45;Troyes;Paris FC
1;2026-08-22;20:45;Toulouse;Lyon
1;2026-08-23;15:00;Angers;Lille
1;2026-08-23;17:15;Le Havre;Monaco
1;2026-08-23;20:45;PSG;Rennes
2;2026-08-28;20:45;Lille;PSG
2;2026-08-29;17:15;Strasbourg;Lens
2;2026-08-29;20:45;Auxerre;Angers
2;2026-08-29;20:45;Brest;Toulouse
2;2026-08-29;20:45;Lorient;Troyes
2;2026-08-29;20:45;Lyon;Le Havre
2;2026-08-30;15:00;Paris FC;Nice
2;2026-08-30;17:15;Rennes;Le Mans
2;2026-08-30;20:45;Monaco;Marseille
3;2026-09-03;20:45;Toulouse;Lille
3;2026-09-04;19:00;Lyon;Auxerre
3;2026-09-04;21:05;PSG;Monaco
3;2026-09-05;17:15;Lens;Lorient
3;2026-09-05;20:45;Le Havre;Brest
3;2026-09-05;20:45;Nice;Le Mans
3;2026-09-06;15:00;Troyes;Strasbourg
3;2026-09-06;17:15;Angers;Rennes
3;2026-09-06;20:45;Marseille;Paris FC
""",
}

# FBref (calendario) -> Understat (fuerzas). Sólo los que difieren.
ALIAS = {
    "premier": {"Manchester Utd": "Manchester United", "Newcastle": "Newcastle United",
                "Nottingham": "Nottingham Forest", "Leeds United": "Leeds",
                "Wolves": "Wolverhampton Wanderers"},
    "laliga": {"Alavés": "Alaves", "Atlético Madrid": "Atletico Madrid"},
    "bundesliga": {"Dortmund": "Borussia Dortmund", "Gladbach": "Borussia M.Gladbach",
                   "Leverkusen": "Bayer Leverkusen", "RB Leipzig": "RasenBallsport Leipzig",
                   "Frankfurt": "Eintracht Frankfurt", "Köln": "FC Cologne",
                   "Stuttgart": "VfB Stuttgart"},
    "seriea": {"Milan": "AC Milan", "Parma": "Parma Calcio 1913"},
    "ligue1": {"PSG": "Paris Saint Germain"},
}

# Nombre bonito para mostrar (algunos de Understat quedan poco naturales)
BONITO = {
    "Wolverhampton Wanderers": "Wolves", "RasenBallsport Leipzig": "RB Leipzig",
    "Borussia M.Gladbach": "M'gladbach", "FC Cologne": "Colonia",
    "Parma Calcio 1913": "Parma", "Paris Saint Germain": "PSG",
    "Atletico Madrid": "Atlético Madrid", "Alaves": "Alavés",
    "Manchester United": "Manchester Utd", "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nottingham", "Borussia Dortmund": "Dortmund",
    "Bayer Leverkusen": "Leverkusen", "Eintracht Frankfurt": "Frankfurt",
    "VfB Stuttgart": "Stuttgart", "FC Heidenheim": "Heidenheim",
    "Leeds": "Leeds United", "St. Pauli": "St. Pauli",
}


def parsear_fuerzas(bloque):
    equipos = {}
    for linea in bloque.strip().splitlines():
        p = linea.split(";")
        equipos[p[0]] = {
            "nombre": BONITO.get(p[0], p[0]), "clave": p[0],
            "atq": float(p[1]), "def": float(p[2]),
            "pts": int(p[3]), "pj": int(p[4]),
            "gf": int(p[5]), "gc": int(p[6]),
            "xg": float(p[7]), "xga": float(p[8]),
            "nuevo": False,
        }
    return equipos


def perfil_ascendido(equipos):
    """Media de los tres peores de la temporada anterior, por puntos."""
    peores = sorted(equipos.values(), key=lambda e: e["pts"])[:3]
    n = len(peores)
    return {
        "atq": sum(e["atq"] for e in peores) / n,
        "def": sum(e["def"] for e in peores) / n,
        "pts": round(sum(e["pts"] for e in peores) / n),
        "pj": peores[0]["pj"],
        "gf": round(sum(e["gf"] for e in peores) / n),
        "gc": round(sum(e["gc"] for e in peores) / n),
        "xg": round(sum(e["xg"] for e in peores) / n, 1),
        "xga": round(sum(e["xga"] for e in peores) / n, 1),
    }


def main():
    salida = {"generado": "2026-08-17", "ligas": {}}

    for clave, (nombre, pais, gamma, bloque) in FUERZAS.items():
        equipos = parsear_fuerzas(bloque)
        alias = ALIAS.get(clave, {})
        base_nuevo = perfil_ascendido(equipos)

        partidos, ascendidos = [], set()
        for linea in CALENDARIO[clave].strip().splitlines():
            j, fecha, hora, local, visita = linea.split(";")
            ids = []
            for eq in (local, visita):
                key = alias.get(eq, eq)
                if key not in equipos:
                    equipos[key] = {"nombre": eq, "clave": key, "nuevo": True, **base_nuevo}
                    ascendidos.add(eq)
                ids.append(key)
            partidos.append({"j": int(j), "fecha": fecha, "hora": hora,
                             "l": ids[0], "v": ids[1]})

        salida["ligas"][clave] = {
            "nombre": nombre, "pais": pais, "gamma": gamma, "rho": -0.109,
            "equipos": {k: v for k, v in sorted(equipos.items())},
            "partidos": sorted(partidos, key=lambda p: (p["fecha"], p["hora"])),
            "ascendidos": sorted(ascendidos),
        }
        print(f"  {nombre:14s} {len(equipos):2d} equipos, {len(partidos):2d} partidos, "
              f"{len(ascendidos)} ascendidos: {', '.join(sorted(ascendidos))}")

    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")),
                      encoding="utf-8")
    print(f"\nGuardado en {SALIDA} ({SALIDA.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
