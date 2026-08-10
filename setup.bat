@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ========================================
echo  DRM Agent initial setup
echo ========================================
echo.

call :DetectPython
if not defined PYTHON_EXE (
    echo [INFO] Usable Python was not found.
    echo [INFO] Trying to install Python with winget...
    where winget > nul 2> nul
    if errorlevel 1 (
        echo [ERROR] winget was not found.
        echo Install Python 3 from https://www.python.org/downloads/
        echo During installation, check "Add python.exe to PATH".
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [ERROR] Python installation failed.
        echo Install Python manually and run this file again.
        pause
        exit /b 1
    )
    call :DetectPython
)

if not defined PYTHON_EXE (
    echo [ERROR] Python is still not available after installation.
    echo Close this CMD window, open a new CMD window, and run setup.bat again.
    pause
    exit /b 1
)

echo [INFO] Python: "%PYTHON_EXE%"
echo.
echo [1/4] Installing required Windows runtime...
where winget > nul 2> nul
if errorlevel 1 (
    echo [WARN] winget was not found. Skipping Visual C++ Runtime auto-install.
) else (
    winget install --id Microsoft.VCRedist.2015+.x64 -e --source winget --accept-package-agreements --accept-source-agreements
)

echo.
echo [2/4] Installing required Python libraries...
"%PYTHON_EXE%" -m ensurepip --upgrade
"%PYTHON_EXE%" -m pip install --upgrade pip
"%PYTHON_EXE%" -m pip install requests pypdf pystray pillow pymupdf
if errorlevel 1 (
    echo [ERROR] Python library installation failed.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import requests, pypdf, pystray, PIL, fitz"
if errorlevel 1 (
    echo [INFO] Library import check failed. Reinstalling PyMuPDF without cache...
    "%PYTHON_EXE%" -m pip uninstall -y pymupdf
    "%PYTHON_EXE%" -m pip install --no-cache-dir --force-reinstall pymupdf
    "%PYTHON_EXE%" -c "import requests, pypdf, pystray, PIL, fitz"
    if errorlevel 1 (
        echo [ERROR] Python library import check failed.
        echo If PyMuPDF shows a DLL load error, install Microsoft Visual C++ Redistributable x64 and run setup.bat again.
        echo Check whether PyMuPDF is installed for this Python:
        echo   "%PYTHON_EXE%" -m pip install pymupdf
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" -c "from PIL import Image, ImageDraw, ImageFont; im=Image.new('RGBA',(256,256),(255,255,255,0)); d=ImageDraw.Draw(im); d.rounded_rectangle((46,24,190,232),radius=10,fill=(245,248,252,255),outline=(28,99,180,255),width=10); d.polygon([(166,24),(190,24),(190,50)],fill=(196,215,238,255)); d.rectangle((62,82,174,126),fill=(210,35,45,255)); d.text((76,92),'PDF',fill=(255,255,255,255)); d.rectangle((76,150,160,168),fill=(28,99,180,255)); d.rectangle((76,184,145,202),fill=(28,99,180,255)); im.save('DRM_Agent_Viewer.ico',sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"

echo.
echo [3/4] Registering DRM Agent Viewer as a selectable app...

set "LAUNCHER=%~dp0DRM_Agent_Viewer.cmd"
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo "%PYTHONW_EXE%" "%%~dp0Client.py" --agent-open "%%~1"
) > "%LAUNCHER%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$base=(Get-Location).Path;" ^
  "$client=Join-Path $base 'Client.py';" ^
  "$launcher=Join-Path $base 'DRM_Agent_Viewer.cmd';" ^
  "$icon=Join-Path $base 'DRM_Agent_Viewer.ico';" ^
  "if(-not (Test-Path -LiteralPath $client)){ throw 'Client.py was not found.' }" ^
  "if(-not (Test-Path -LiteralPath $launcher)){ throw 'DRM_Agent_Viewer.cmd was not found.' }" ^
  "if(-not (Test-Path -LiteralPath $icon)){ throw 'DRM_Agent_Viewer.ico was not found.' }" ^
  "$cmd='\"' + $launcher + '\" \"%%1\"';" ^
  "function K($p){ if(-not (Test-Path -LiteralPath $p)){ New-Item -Path $p -Force | Out-Null } }" ^
  "function D($p,$v){ K $p; Set-Item -Path $p -Value $v }" ^
  "D 'HKCU:\Software\Classes\DRMAgentPDF' 'DRM Agent PDF Handler';" ^
  "Set-ItemProperty -Path 'HKCU:\Software\Classes\DRMAgentPDF' -Name 'FriendlyTypeName' -Value 'DRM Agent Viewer';" ^
  "D 'HKCU:\Software\Classes\DRMAgentPDF\DefaultIcon' ('\"' + $icon + '\"');" ^
  "D 'HKCU:\Software\Classes\DRMAgentPDF\shell\open\command' $cmd;" ^
  "K 'HKCU:\Software\DRMAgent\Capabilities';" ^
  "Set-ItemProperty -Path 'HKCU:\Software\DRMAgent\Capabilities' -Name 'ApplicationName' -Value 'DRM Agent Viewer';" ^
  "Set-ItemProperty -Path 'HKCU:\Software\DRMAgent\Capabilities' -Name 'ApplicationDescription' -Value 'DRM Agent secure PDF viewer';" ^
  "Set-ItemProperty -Path 'HKCU:\Software\DRMAgent\Capabilities' -Name 'ApplicationIcon' -Value $icon;" ^
  "K 'HKCU:\Software\DRMAgent\Capabilities\FileAssociations';" ^
  "Set-ItemProperty -Path 'HKCU:\Software\DRMAgent\Capabilities\FileAssociations' -Name '.pdf' -Value 'DRMAgentPDF';" ^
  "K 'HKCU:\Software\RegisteredApplications';" ^
  "Set-ItemProperty -Path 'HKCU:\Software\RegisteredApplications' -Name 'DRM Agent Viewer' -Value 'Software\DRMAgent\Capabilities';" ^
  "Write-Host 'DRM Agent Viewer registration completed.'"

if errorlevel 1 (
    echo [ERROR] DRM Agent Viewer registration failed.
    pause
    exit /b 1
)

echo.
echo [4/4] Opening Windows default apps settings...
start ms-settings:defaultapps

echo.
echo Setup finished.
echo.
echo If DRM Agent Viewer is not listed in Windows Settings:
echo   Right-click a PDF - Open with - Choose another app - More apps
echo   Select DRM_Agent_Viewer.cmd in this folder.
echo.
echo Run the agent with: "%PYTHON_EXE%" Client.py
pause
exit /b 0

:DetectPython
set "PYTHON_EXE="
set "PYTHONW_EXE="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$paths = @(); $roots = @($env:LocalAppData + '\Programs\Python', $env:LocalAppData + '\Microsoft\WinGet\Packages', $env:ProgramFiles, ${env:ProgramFiles(x86)}); foreach($root in $roots){ if($root -and (Test-Path -LiteralPath $root)){ $paths += Get-ChildItem -LiteralPath $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue ^| Where-Object { $_.FullName -notlike '*WindowsApps*' } ^| Select-Object -ExpandProperty FullName } }; foreach($path in $paths){ if($path -and (Test-Path -LiteralPath $path)){ Write-Output $path; exit 0 } }; $candidates = @(@('py','-3'), @('python')); foreach($candidate in $candidates){ $exe = $candidate[0]; $extra = @(); if($candidate.Count -gt 1){ $extra = $candidate[1..($candidate.Count-1)] }; try { $out = & $exe @extra -c 'import sys; print(sys.executable)' 2^>$null; if($LASTEXITCODE -eq 0 -and $out -and (Test-Path -LiteralPath $out) -and ($out -notlike '*WindowsApps*')){ Write-Output $out; break } } catch {} }"`) do (
    set "PYTHON_EXE=%%P"
)
if defined PYTHON_EXE (
    for %%I in ("%PYTHON_EXE%") do set "PYTHON_DIR=%%~dpI"
    if exist "%PYTHON_DIR%pythonw.exe" (
        set "PYTHONW_EXE=%PYTHON_DIR%pythonw.exe"
    ) else (
        set "PYTHONW_EXE=%PYTHON_EXE%"
    )
)
exit /b 0
