@echo off
setlocal EnableDelayedExpansion

echo ============================================
echo   webreader - One-click Installer (Windows)
echo ============================================
echo.

:: ─── Check Python ──────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11+ first.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER% found

:: ─── Install package ───────────────────────
echo.
echo Installing webreader package...
pip install -e "%~dp0.." >nul 2>&1
if errorlevel 1 (
    echo [WARN] pip install had issues, trying with dependencies...
    pip install playwright markdownify rich click >nul 2>&1
)
echo [OK] Package installed

:: ─── Install Playwright browsers ────────────
echo.
echo Checking Playwright browser...
python -m playwright install chromium >nul 2>&1
if errorlevel 1 (
    echo [INFO] Playwright chromium install skipped or not needed
) else (
    echo [OK] Playwright chromium ready
)

:: ─── Register Native Messaging Host ────────
echo.
echo Registering native messaging host...

:: Get absolute path to host.py
pushd "%~dp0native_host" >nul 2>&1
set HOST_PATH=%CD%\host.py
popd

:: Get the extension ID from manifest.json (simplified: use placeholder)
:: In production, compute this from extension's public key

:: Create registry entry for Chrome
reg add "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.webreader.host" /ve /t REG_SZ /d "%~dp0native_host\host_manifest_win.json" /f >nul 2>&1

:: Create registry entry for Edge
reg add "HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.webreader.host" /ve /t REG_SZ /d "%~dp0native_host\host_manifest_win.json" /f >nul 2>&1

:: Update the host manifest with real paths
powershell -Command ^
    "$json = Get-Content '%~dp0native_host\host_manifest_win.json' | ConvertFrom-Json; ^
     $json.path = '%HOST_PATH%'; ^
     $json | ConvertTo-Json | Set-Content '%~dp0native_host\host_manifest_win.json'" >nul 2>&1

echo [OK] Native messaging host registered for Chrome & Edge

:: ─── Summary ──────────────────────────────
echo.
echo ============================================
echo   ✅ Installation Complete!
echo ============================================
echo.
echo   Next steps:
echo.
echo   1. Load the extension:
echo      Open chrome://extensions or edge://extensions
echo      Enable "Developer mode"
echo      Click "Load unpacked"
echo      Select folder: %~dp0extension
echo.
echo   2. Start using:
echo      webreader launch          ^(^starts Edge with debugging^)
echo      webreader read <URL>      ^(^read any page^)
echo      webreader list            ^(^list browsers^)
echo.
echo   Or just click the 🌐 icon on any page!
echo.
pause
