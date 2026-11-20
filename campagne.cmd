@echo off
rem Campagne complete en une commande sous Windows : double-clic ou .\campagne.cmd [-Runs 1] [-MaxHours 8]
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\campagne.ps1" %*
set CODE=%ERRORLEVEL%
echo.
echo Code de sortie : %CODE%  (0 = campagne terminee ; sinon relancer campagne.cmd pour reprendre)
pause
exit /b %CODE%
