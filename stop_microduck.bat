@echo off
title Stop Microduck X
echo Stopping Microduck X services...
wsl -d Ubuntu bash -c "export HOME=/home/charan_balaji; cd \"\$(wslpath -u '%~dp0')\" && sh microduck-x/scripts/x-stop.sh"
echo Done.
timeout /t 3
