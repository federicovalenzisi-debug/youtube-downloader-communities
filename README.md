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

> **Máxima calidad (1080p/4K):** desde Colab, YouTube corre sobre una IP de datacenter
> que solo entrega alta resolución si la petición lleva un *PO token* y si hay un runtime
> de JavaScript para resolver la firma de los formatos. La primera celda prepara las dos
> cosas sola (opción `MAX_QUALITY`, activada por defecto):
>
> - instala **Deno** (runtime JS que yt-dlp necesita para destrabar 1080p/4K),
> - instala **Node.js 20** si Colab trae uno más viejo, y
> - levanta el servidor **bgutil** (PO token) en la versión que coincide con el plugin.
>
> Al terminar, la celda hace una **verificación real**: prueba un video 1080p conocido y
> avisa con `✅` si la máxima calidad funciona, o con `⚠️` si la IP de Colab está limitada.
> Si sale el aviso, lo más efectivo es **Entorno de ejecución → Desconectar y eliminar
> entorno** y volver a *Ejecutar todo* (Colab te da una IP nueva). Acordate de hacer eso
> también cada vez que cambie el notebook, para que tome la versión nueva.

#### Si la IP de Colab sigue limitada (cookies)

Si tras reintentar con IP nueva el aviso `⚠️` persiste, es porque esa IP quedó marcada
por YouTube. La solución infalible es usar tus **cookies** de YouTube:

1. En tu navegador, exportá las cookies de `youtube.com` a un archivo `cookies.txt`
   (extensión "Get cookies.txt LOCALLY" o similar).
2. Subilo a Colab (panel izquierdo → 📁 → subir) como `cookies.txt`.
3. yt-dlp lo toma automáticamente si está en la carpeta de trabajo. Con cookies, la
   descarga usa tu sesión y baja siempre a máxima calidad.

### Para vos (publicarlo una sola vez)

El notebook es `YouTube_Downloader_Colab.ipynb` y es **autocontenido**: lleva el código
adentro, así que el compañero no necesita nada más que ese archivo.

Opción A — **GitHub (recomendada):**

Este repo ya está publicado. El link de Colab para pasarle al compañero es:

```
https://colab.research.google.com/github/federicovalenzisi-debug/youtube-downloader-communities/blob/main/YouTube_Downloader_Colab.ipynb
```

(El notebook vive en la **raíz** del repo, por eso el path no lleva subcarpeta.) Si
clonás esto en otro repo, el patrón general es:
```
https://colab.research.google.com/github/USUARIO/REPO/blob/RAMA/YouTube_Downloader_Colab.ipynb
```

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
