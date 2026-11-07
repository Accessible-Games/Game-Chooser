@echo off
setlocal
cd /d "%~dp0"
set "T=GameChooserTraceTest"

rem Step 1: a file in the game's own saves folder
mkdir saves 2>nul
echo volume=high> saves\settings.ini
ping -n 2 127.0.0.1 >nul

rem Step 2: roaming app data
mkdir "%APPDATA%\%T%" 2>nul
echo roaming> "%APPDATA%\%T%\roaming.txt"
ping -n 2 127.0.0.1 >nul

rem Step 3: local app data
mkdir "%LOCALAPPDATA%\%T%" 2>nul
echo local> "%LOCALAPPDATA%\%T%\local.txt"
ping -n 2 127.0.0.1 >nul

rem Step 4: documents
mkdir "%USERPROFILE%\Documents\%T%" 2>nul
echo documents> "%USERPROFILE%\Documents\%T%\documents.txt"
ping -n 2 127.0.0.1 >nul

rem Step 5: a registry value, written by reg.exe (a child process)
reg add "HKCU\Software\%T%" /v Level /t REG_SZ /d 3 /f >nul
ping -n 2 127.0.0.1 >nul

rem Step 6: write a temp file, then rename it over the save
echo slot one> saves\slot1.tmp
move /y saves\slot1.tmp saves\slot1.sav >nul
ping -n 2 127.0.0.1 >nul

rem Step 7: write a file, then delete it
echo junk> saves\junk.txt
del saves\junk.txt
ping -n 2 127.0.0.1 >nul

rem Step 8: a file written by a child process
cmd /c "echo child> "%LOCALAPPDATA%\%T%\child.txt""
ping -n 2 127.0.0.1 >nul

rem Step 9: a registry value added and then deleted by reg.exe
reg add "HKCU\Software\%T%" /v Temp /d x /f >nul
reg delete "HKCU\Software\%T%" /v Temp /f >nul

echo Trace test finished
exit /b 0
