@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0runtime\python.exe" (
  echo Скачайте готовый ZIP из раздела Releases и полностью распакуйте его.
  echo Архив Source code не содержит переносимый Python.
  pause
  exit /b 1
)
if "%~1"=="" (
  "%~dp0runtime\python.exe" -I -X utf8 "%~dp0patch.py" install
) else (
  "%~dp0runtime\python.exe" -I -X utf8 "%~dp0patch.py" install --game "%~1"
)
set "patchResult=%errorlevel%"
echo.
pause
exit /b %patchResult%
