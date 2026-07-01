@echo off
chcp 65001 > nul
echo Verificador E-14C
echo ================
echo.

rem --- Verificar Python via py launcher (recomendado en Windows) ---
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto :check_tqdm
)

rem --- Fallback a python ---
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto :check_tqdm
)

echo ERROR: Python no encontrado en este equipo.
echo.
echo Descarga e instala Python desde:
echo   https://www.python.org/downloads/
echo.
echo Asegurate de marcar "Add Python to PATH" durante la instalacion.
pause
exit /b 1

:check_tqdm
rem --- Verificar tqdm ---
%PYTHON_CMD% -c "import tqdm" >nul 2>&1
if errorlevel 1 (
    echo Instalando tqdm...
    %PYTHON_CMD% -m pip install tqdm --quiet
    if errorlevel 1 (
        echo ERROR: No se pudo instalar tqdm.
        echo Ejecuta manualmente: pip install tqdm
        pause
        exit /b 1
    )
)

rem --- Ejecutar verificador (modo interactivo con menu y logo) ---
%PYTHON_CMD% verificador_e14c.py
