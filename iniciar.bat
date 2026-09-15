@echo off
chcp 65001 >nul
title Kiosco POS
cd /d "%~dp0"

if exist "%~dp0dist\KioscoPOS\KioscoPOS.exe" (
    start "" "%~dp0dist\KioscoPOS\KioscoPOS.exe" %*
    exit /b 0
)

if exist "%~dp0dist\KioscoPOS.exe" (
    start "" "%~dp0dist\KioscoPOS.exe" %*
    exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
    echo No se encontro 'python' en el PATH.
    pause
    exit /b 1
)

python -m app.main %*
if errorlevel 1 (
    echo.
    echo La aplicacion termino con un error.
    pause
)