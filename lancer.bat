@echo off
cd /d "%~dp0"

set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PYEXE%" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"

if not exist "%PYEXE%" (
  for %%V in (313 312 311 310) do (
    if not exist "%PYEXE%" if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  )
)

if not exist "%PYEXE%" (
  echo Python est introuvable.
  echo Installez Python 3 depuis https://www.python.org/downloads/ puis relancez ce fichier.
  pause
  exit /b 1
)

"%PYEXE%" app.py
if errorlevel 1 pause
