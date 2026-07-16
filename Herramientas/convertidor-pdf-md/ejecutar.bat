@echo off
chcp 65001 > nul
echo Convertidor PDF -^> Markdown (MarkItDown)
echo ===========================================
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

rem --- Lanzar conversor ---
python "%~dp0convertidor.py" %*
if errorlevel 1 (
    echo.
    echo ERROR: El conversor termino con error. Revisa el mensaje arriba.
    pause
)