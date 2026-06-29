# download-youtube

Descarga **solo los videos de YouTube** de las páginas de las comunidades (estilo
seniorlivingnearme). Escanea la página **Gallery** y la **Home** de cada comunidad,
encuentra los videos de YouTube embebidos (iframes, links, `data-*` y JSON-LD) y los
baja en la mejor calidad disponible con `yt-dlp`. **No descarga fotos.**

Es la versión "solo YouTube" de la herramienta `../download-images`.

## Forma recomendada de usarlo: Google Colab (sin instalar nada)

Pensado para que un compañero lo use con **un solo link**, sin descargar el código ni
instalar Python/FFmpeg. Todo corre en la nube de Google.

### Para el compañero (uso diario)

1. Abrí el link del notebook en Google Colab (ver más abajo cómo se arma el link).
2. Menú **Entorno de ejecución → Ejecutar todo** (`Runtime → Run all`).
3. En la celda **"PASO 1"**, pegá las URLs de las comunidades (una por línea) y volvé
   a ejecutar esa celda.
4. Al final se descarga automáticamente **`videos_youtube.zip`** con todos los videos.

Necesita una cuenta de Google (gratis). La primera celda tarda 1–2 minutos en preparar
el entorno.

> **Máxima calidad (1080p/4K):** desde Colab, YouTube solo entrega la alta resolución
> si hay un *PO token*. La primera celda levanta sola un pequeño servidor que lo genera
> (opción `MAXIMA_CALIDAD`, activada por defecto), así que no hay que hacer nada. Si ese
> servidor no llega a iniciar, los videos igual se descargan, pero pueden bajar en baja
> resolución (~360p). En ese caso, volvé a ejecutar la primera celda. Si cambiás el
> notebook, acordate de **Entorno de ejecución → Desconectar y eliminar entorno** antes
> de volver a ejecutarlo, para que Colab tome la versión nueva.

### Para vos (publicarlo una sola vez)

El notebook es `YouTube_Downloader_Colab.ipynb` y es **autocontenido**: lleva el código
adentro, así que el compañero no necesita nada más que ese archivo.

Opción A — **GitHub (recomendada):**

1. Subí esta carpeta a un repositorio de GitHub.
2. El link de Colab se arma así (reemplazá `USUARIO/REPO` y la rama):
   ```
   https://colab.research.google.com/github/USUARIO/REPO/blob/main/download-youtube/YouTube_Downloader_Colab.ipynb
   ```
3. Pasale ese link al compañero. Listo.

> Nota: GitHub **Pages** no sirve para esto (solo publica HTML estático y no puede
> ejecutar Python ni descargar de YouTube). Lo que se usa es GitHub como almacenamiento
> del notebook + Google Colab para ejecutarlo.

Opción B — **Google Drive (sin GitHub):**

1. Subí `YouTube_Downloader_Colab.ipynb` a tu Google Drive.
2. Abrilo con Google Colab (clic derecho → Abrir con → Google Colaboratory).
3. Compartí el archivo con el compañero (permiso de edición o "crear copia").

## Uso local (alternativo, sin Colab)

Requiere Python 3.10+ y, para los screenshots, **FFmpeg** en el PATH.

```sh
cd download-youtube
python -m pip install -r requirements.txt
python -m playwright install chromium   # opcional, fallback para páginas con JS

# Una sola URL:
python youtube_downloader.py "https://ejemplo.seniorlivingnearme.com/gallery"

# Varias comunidades desde un archivo:
python youtube_downloader.py --links links.txt
```

En Windows podés hacer doble clic en **`RUN_YOUTUBE_DOWNLOADER.bat`** (instala
dependencias y corre el modo por lotes con `links.txt`).

### Opciones de línea de comandos

| Opción | Qué hace |
| --- | --- |
| `url` (posicional) | Procesa una sola URL de galería. |
| `--links archivo.txt` | Procesa varias comunidades (una por línea: `URL \| Nombre`). |
| `--out CARPETA` | Carpeta de salida (por defecto `downloads`). |
| `--no-home` | No escanea la Home, solo la URL dada. |
| `--no-playwright` | Desactiva el fallback de Playwright (más rápido, menos cobertura). |
| `--no-screenshots` | No genera los screenshots de los videos. |

## Entrada

- **`links.txt`** — una comunidad por línea. El nombre es opcional:
  ```text
  https://ejemplo.seniorlivingnearme.com/gallery | Nombre de la Comunidad
  https://otra.seniorlivingnearme.com/gallery
  ```
  Las líneas vacías y las que empiezan con `#` se ignoran.

## Salida

```text
downloads/
  Nombre de la Comunidad/
    001 - Walkthrough Tour/
      video.mp4
      screenshot.jpg
    002 - Otro Video/
      video.mp4
      screenshot.jpg
    manifest.csv        # detalle por video de esa comunidad
  batch_manifest.csv    # resumen de todas las comunidades
```

Cada `manifest.csv` registra: URL de YouTube, estado de descarga, tamaño, posibles
errores y el estado del screenshot.

## Notas

- Los videos de YouTube que aparecen tanto en Gallery como en Home se descargan **una
  sola vez** (gana Gallery).
- FFmpeg viene preinstalado en Google Colab, así que los screenshots funcionan solos
  en la nube. En uso local hay que instalarlo aparte.
- Un video que falla no detiene al resto: revisá `manifest.csv` para ver el estado de
  cada uno.
- Si editás `youtube_downloader.py`, regenerá el notebook para que la copia embebida
  quede igual (ver `scratchpad/build_notebook.py` o volvé a pegar el script en la celda
  `%%writefile`).
