@echo off
chcp 65001 > nul
echo Compilador de verificador_e14c.py
echo ==================================
echo.
echo Este script compila verificador_e14c.py en un ejecutable .exe
echo usando PyInstaller. Cualquier persona puede hacer esto para
echo verificar que el .exe distribuido es identico al codigo fuente.
echo.

rem --- Verificar Python ---
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto :check_pyinstaller
)
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto :check_pyinstaller
)

echo ERROR: Python no encontrado.
echo Instala Python desde https://www.python.org/downloads/
pause
exit /b 1

:check_pyinstaller
%PYTHON_CMD% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Instalando PyInstaller...
    %PYTHON_CMD% -m pip install pyinstaller --quiet
    if errorlevel 1 (
        echo ERROR: No se pudo instalar PyInstaller.
        pause
        exit /b 1
    )
)

echo Compilando...
echo.
%PYTHON_CMD% -m PyInstaller --onefile --name verificador_e14c --distpath . --workpath .build --specpath .build verificador_e14c.py

if errorlevel 1 (
    echo.
    echo ERROR: La compilacion fallo.
    pause
    exit /b 1
)

rem Limpiar archivos temporales de build
rmdir /s /q .build 2>nul

echo.
echo Compilacion exitosa.
echo El ejecutable generado es: verificador_e14c.exe
echo.
echo Puedes comparar el .exe generado con el distribuido para confirmar
echo que son identicos (mismo SHA-256).
echo.
pause
