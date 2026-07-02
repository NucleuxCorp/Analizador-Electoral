@echo off
chcp 65001 > nul
echo Verificador SSL — Registraduria 2026
echo =====================================
echo.

rem --- Verificar curl ---
curl.exe --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: curl.exe no encontrado.
    echo curl viene incluido en Windows 10 v1803+ y Windows Server 2019+.
    echo Si estas en una version anterior, descargalo desde https://curl.se/windows/
    pause
    exit /b 1
)

rem --- Lanzar script PowerShell ---
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verificar_ssl.ps1"
if errorlevel 1 (
    echo.
    echo ERROR: PowerShell termino con error. Revisa el mensaje arriba.
    pause
)
