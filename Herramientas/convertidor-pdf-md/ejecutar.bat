@echo off
chcp 65001 > nul
echo Convertidor PDF -^> Markdown + CSV (MarkItDown)
echo ===============================================
echo.

rem --- Verificar Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado.
    echo Instala Python 3.10+ desde https://www.python.org/downloads/
    pause
    exit /b 1
)

rem --- Verificar/instalar dependencias ---
python -c "import markitdown" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias...
    pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo ERROR: No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
    echo Dependencias instaladas.
    echo.
)

rem --- Si se pasaron argumentos, ejecutar directo ---
set ARGS=%*
if not "%ARGS%"=="" (
    python "%~dp0convertidor.py" %ARGS%
    if errorlevel 1 (
        echo.
        echo ERROR: El conversor termino con error. Revisa el mensaje arriba.
        pause
    )
    exit /b %errorlevel%
)

rem --- Sin argumentos: preguntar modo ---
echo Modo de conversion:
echo   [1] Solo texto (Markdown basico)
echo   [2] Texto + tablas embebidas en el .md
echo   [3] Texto + archivos CSV de respaldo
echo   [4] Todo (texto + tablas embebidas + CSV de respaldo)
echo.
set /p MODO="Selecciona [1-4] (Enter = 4): "

if "%MODO%"=="1" (
    set FLAGS=
) else if "%MODO%"=="2" (
    set FLAGS=--force --embed-csv
) else if "%MODO%"=="3" (
    set FLAGS=--force --csv-files
) else (
    set FLAGS=--force --embed-csv --csv-files
)

python "%~dp0convertidor.py" %FLAGS%
if errorlevel 1 (
    echo.
    echo ERROR: El conversor termino con error. Revisa el mensaje arriba.
    pause
)