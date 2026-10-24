@echo off
title Microduck X - Orbital Hangar
echo ===================================================
echo   Starting Microduck X (Orbital Hangar)
echo ===================================================
echo.

echo Opening dashboard at http://localhost:8765 ...
start http://localhost:8765

wsl -d Ubuntu bash -c "export MDX_ENV=hangar; export HOME=/home/charan_balaji; cd \"\$(wslpath -u '%~dp0')\" && sh microduck-x/scripts/x-stop.sh 2>/dev/null && sh microduck-x/scripts/x-start.sh && echo '===================================================' && echo ' Microduck X is running live at http://localhost:8765!' && echo ' Press [Enter] to shut down cleanly.' && echo '===================================================' && trap 'sh microduck-x/scripts/x-stop.sh; exit 0' INT TERM && read -r _ && sh microduck-x/scripts/x-stop.sh"

echo.
echo Microduck X stopped.
pause

