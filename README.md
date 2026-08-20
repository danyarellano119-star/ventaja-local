# Ventaja Local

Probabilidades, cuotas y estadísticas de las cinco grandes ligas europeas, con
un modelo Dixon-Coles ajustado sobre ocasiones de gol (xG).

La web es **estática**: lleva los datos dentro y calcula todo en el navegador,
así que no necesita servidor ni cuesta nada mantenerla en marcha.

## Actualizarla a mano

```bash
pip install -r requirements.txt
python scripts/actualizar.py
```

Descarga el xG y las plantillas de Understat, recalcula las fuerzas de cada
equipo, detecta sancionados y ausentes, y regenera `web/datos_ligas.json` y
`web/index.html`.

## Que se actualice sola

El repositorio incluye un flujo de GitHub Actions que hace lo anterior cada tres
horas y publica el resultado en GitHub Pages. Para ponerlo en marcha:

1. Crea un repositorio en GitHub y sube esta carpeta.

   ```bash
   git init
   git add .
   git commit -m "Ventaja Local"
   git branch -M main
   git remote add origin https://github.com/TU-USUARIO/ventaja-local.git
   git push -u origin main
   ```

2. En el repositorio, entra en **Settings → Pages** y en «Source» elige
   **GitHub Actions**.

3. En **Settings → Actions → General**, dentro de «Workflow permissions», marca
   **Read and write permissions**. Sin esto el flujo no puede guardar los datos
   nuevos.

4. Ve a la pestaña **Actions**, elige *Actualizar Ventaja Local* y pulsa **Run
   workflow** para lanzarlo la primera vez. A partir de ahí corre solo.

La página queda en `https://TU-USUARIO.github.io/ventaja-local/`.

### Cada cuánto se actualiza

Cada tres horas. Los partidos europeos terminan como muy tarde sobre las 23:00
UTC, así que una jornada completa queda reflejada como mucho tres horas después
del último pitido. Si prefieres otra frecuencia, cambia la línea `cron` en
[`.github/workflows/actualizar.yml`](.github/workflows/actualizar.yml); ten en
cuenta que GitHub Actions es gratis en repositorios públicos, pero en privados
consume minutos de tu cuota.

## Trabajar sobre la página

La regla importante: **edita `web/plantilla.html`, nunca `web/index.html`.**
El segundo se genera a partir del primero en cada actualización y cualquier
cambio hecho ahí se pierde.

```
web/plantilla.html   ← aquí escribes tú (diseño, textos, gráficos)
        +
web/datos_ligas.json ← lo rellena el script con los datos
        =
web/index.html       ← se genera solo; es lo que ve la gente
```

Después de tocar la plantilla:

```bash
git add web/plantilla.html
git commit -m "Cambio en los textos de la ficha de equipo"
git push
```

El flujo de GitHub Actions detecta el cambio, regenera la web y la publica.
Tarda dos o tres minutos.

Si prefieres editar directamente en github.com, también vale: cualquier cambio
en `web/plantilla.html` o en `scripts/` dispara la publicación.

### Ver en local antes de subir

```bash
python scripts/actualizar.py
```

Regenera `web/index.html` con tus cambios y lo puedes abrir en el navegador
para revisarlo antes de hacer push.

### Traer los cambios que haya hecho el flujo automático

Como el robot hace commits con los datos nuevos, antes de ponerte a trabajar:

```bash
git pull
```

## De dónde salen los datos

| Dato | Fuente | Automático |
|------|--------|------------|
| xG partido a partido | Understat (`/getLeagueData`) | Sí |
| Plantillas, goles, tarjetas | Understat | Sí |
| Sancionados y ausentes | Understat (`/getMatchData`) | Sí |
| Calendario de la temporada | openfootball | Sí, cuando lo publiquen |

El calendario de 2026/27 se extrajo a mano de FBref y queda guardado como
respaldo: FBref responde 403 a cualquier petición automatizada. En cuanto
openfootball publique la temporada, el flujo lo recoge solo. Mientras tanto,
ejecutar el script nunca borra el calendario que ya hubiera.

## Estructura

```
scripts/actualizar.py     descarga, modelo y generación de la web
scripts/dixon_coles.py    modelo base y herramientas de validación
web/plantilla.html        la página, con /*__DATOS__*/ como marcador
web/index.html            la página ya generada (no editar a mano)
web/datos_ligas.json      datos de las cinco ligas
```

Para cambiar el diseño o los textos edita **`web/plantilla.html`** y vuelve a
ejecutar el script; `index.html` se sobrescribe en cada actualización.

## Sobre el modelo

Dixon-Coles (1997) sobre log(xG), con ponderación temporal (vida media de unos
385 días) y ventaja de campo estimada por liga. Validado con backtest temporal:
50,4 % de acierto en 1X2 y un error medio de calibración de 2,2 puntos
porcentuales.

No ha sido contrastado contra cuotas de mercado, así que no hay evidencia de que
pueda batirlas. Las cuotas estimadas de la web son la probabilidad del modelo
con un margen aplicado encima, no las de ninguna casa concreta.
