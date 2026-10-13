@echo off
setlocal
cd /d "%~dp0"

set "APP=%~dp0codex_tray_app.py"
set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%APP%" (
    echo Napaka: datoteke codex_tray_app.py ni v mapi projekta.
    pause
    exit /b 1
)

rem Ce sledilnik ze tece, ga ne zaganjamo znova: drugi zagon ga samo preklopi v pavzo (toggle).
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'pythonw*' -and $_.CommandLine -like '*codex_tray_app.py*' }) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo Codex Auto-Resume ze tece - drugi zagon ni potreben.
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" "%APP%"
    exit /b 0
)

where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "%APP%"
    exit /b 0
)

where py.exe >nul 2>nul
if not errorlevel 1 (
    echo Lokalno Python okolje ni najdeno. Zagon prek sistemskega Pythona ...
    py -3 "%APP%"
    if errorlevel 1 (
        echo.
        echo Zagon ni uspel. Preveri, ali je Python 3.10+ namescen in ali so namescene odvisnosti iz requirements.txt.
        pause
        exit /b 1
    )
    exit /b 0
)

echo Python ni najden. Namesti Python 3.10 ali novejsi ali pripravi mapo .venv v projektu.
pause
exit /b 1
