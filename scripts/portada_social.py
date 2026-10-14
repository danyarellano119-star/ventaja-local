"""La imagen que sale cuando alguien comparte el enlace.

WhatsApp, Twitter o Telegram piden una imagen para dibujar la vista previa de
un enlace. Sin ella, el enlace aparece como una línea de texto gris y se pulsa
mucho menos. Se dibuja aquí en vez de usar una foto ajena: así no dependemos de
que un servidor de terceros siga sirviendo la imagen, y podemos poner el dato
que de verdad distingue a esta web —el acierto medido— en vez de una foto
genérica de un estadio.

Son 1200x630, que es la medida que esperan todas esas plataformas.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ANCHO, ALTO = 1200, 630
SALIDA = Path(__file__).resolve().parent.parent / "web" / "portada.png"

FONDO = (14, 18, 25)
VERDE = (61, 220, 132)
BLANCO = (247, 249, 251)
GRIS = (150, 161, 176)

# Tipografías del sistema; si no está la que se pide se usa la de reserva, que
# es fea pero nunca falla.
_CANDIDATAS = [
    "C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]
_SANS = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _fuente(rutas: list[str], tam: int):
    for r in rutas:
        try:
            return ImageFont.truetype(r, tam)
        except Exception:
            continue
    return ImageFont.load_default()


def generar(resumen: dict | None = None, ligas: int = 0) -> Path:
    """Dibuja la imagen y devuelve dónde quedó."""
    resumen = resumen or {}
    img = Image.new("RGB", (ANCHO, ALTO), FONDO)
    d = ImageDraw.Draw(img)

    # Un degradado suave en diagonal, para que no sea un rectángulo plano
    for y in range(ALTO):
        for tramo, (x0, x1) in [(0, (0, ANCHO))]:
            t = y / ALTO
            c = (int(FONDO[0] + 16 * (1 - t)), int(FONDO[1] + 22 * (1 - t)),
                 int(FONDO[2] + 30 * (1 - t)))
            d.line([(x0, y), (x1, y)], fill=c)

    # Franja de acento arriba
    d.rectangle([0, 0, ANCHO, 7], fill=VERDE)

    # El cuerpo se elige midiendo: con uno fijo el título se salía por la
    # derecha y quedaba cortado a media palabra.
    MARGEN, LINEAS = 70, ["Probabilidades y estadísticas", "de fútbol, explicadas"]
    cuerpo = 82
    while cuerpo > 40:
        f = _fuente(_CANDIDATAS, cuerpo)
        if max(d.textlength(l, font=f) for l in LINEAS) <= ANCHO - 2 * MARGEN:
            break
        cuerpo -= 2
    serif = _fuente(_CANDIDATAS, cuerpo)
    serif_p = _fuente(_CANDIDATAS, 40)
    sans = _fuente(_SANS, 27)
    sans_b = _fuente(_SANS, 23)
    mono_g = _fuente(_CANDIDATAS, 100)

    d.text((70, 92), "VENTAJA LOCAL", font=sans_b, fill=VERDE)
    d.text((MARGEN, 140), LINEAS[0], font=serif, fill=BLANCO)
    d.text((MARGEN, 140 + int(cuerpo * 1.18)), LINEAS[1], font=serif, fill=BLANCO)

    d.text((70, 340),
           "Cada pronóstico se publica antes del partido y se guarda.",
           font=sans, fill=GRIS)
    d.text((70, 380),
           "Después se enseña si se cumplió o no.", font=sans, fill=GRIS)

    # La cifra que distingue a esta web de las demás
    n = resumen.get("n") or 0
    if n:
        d.text((70, 455), f"{resumen.get('pct', 0)} %", font=mono_g, fill=VERDE)
        ancho_pct = d.textlength(f"{resumen.get('pct', 0)} %", font=mono_g)
        d.text((70 + ancho_pct + 24, 478),
               "de acierto", font=sans, fill=BLANCO)
        d.text((70 + ancho_pct + 24, 516),
               f"sobre {n} partidos ya resueltos", font=sans_b, fill=GRIS)
    elif ligas:
        d.text((70, 470), f"{ligas} competiciones", font=serif_p, fill=BLANCO)

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    img.save(SALIDA, "PNG", optimize=True)
    return SALIDA
