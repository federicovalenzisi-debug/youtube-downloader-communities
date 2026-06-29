@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   YouTube Downloader (solo videos)
echo ========================================
echo.

echo Comprobando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado o no esta en el PATH.
    echo Instala Python desde https://www.python.org/downloads/
    echo Marca "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)
python --version
echo.

if not exist "links.txt" (
    echo ERROR: no se encontro links.txt en esta carpeta.
    echo Crea links.txt con una comunidad por linea:
    echo URL ^| Nombre de la comunidad
    echo.
    pause
    exit /b 1
)

echo Instalando/actualizando dependencias...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: fallo la instalacion de dependencias.
    echo.
    pause
    exit /b 1
)
echo.

echo Comprobando navegador de Playwright (opcional)...
python -m playwright install chromium
echo.

echo Comprobando FFmpeg (para los screenshots)...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo AVISO: FFmpeg no encontrado. Los videos se descargan igual,
    echo pero los screenshots y el merge de YouTube pueden fallar.
    echo Instala FFmpeg desde https://ffmpeg.org/download.html
    echo.
)

echo Iniciando descarga...
echo.
python youtube_downloader.py --links links.txt
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE%==0 (
    echo ========================================
    echo   Listo. Revisa la carpeta downloads\
    echo ========================================
) else (
    echo ========================================
    echo   Termino con algunos errores. Revisa los mensajes.
    echo ========================================
)
echo.
pause
exit /b %EXIT_CODE%
