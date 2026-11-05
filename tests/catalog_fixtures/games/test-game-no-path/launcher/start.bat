@echo off
cd /d "%~dp0"
if not exist Saves mkdir Saves
if not exist Saves\slot1.sav echo save slot 1> Saves\slot1.sav
echo Launcher, the catalog's launcher
ping -n 3 127.0.0.1 >nul
