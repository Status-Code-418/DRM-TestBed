@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ========================================
echo  DRM Agent initial setup
echo ========================================
echo.

where python > nul 2> nul
if errorlevel 1 (
    echo [ERROR] python command was not found.
    echo Install Python and run this file again.
    pause
    exit /b 1
)

echo [1/3] Installing required Python libraries...
python -m pip install --upgrade pip
python -m pip install requests pypdf pystray pillow pymupdf
if errorlevel 1 (
    echo [ERROR] Python library installation failed.
    pause
    exit /b 1
)

python -c "from PIL import Image, ImageDraw, ImageFont; im=Image.new('RGBA',(256,256),(255,255,255,0)); d=ImageDraw.Draw(im); d.rounded_rectangle((46,24,190,232),radius=10,fill=(245,248,252,255),outline=(28,99,180,255),width=10); d.polygon([(166,24),(190,24),(190,50)],fill=(196,215,238,255)); d.rectangle((62,82,174,126),fill=(210,35,45,255)); d.text((76,92),'PDF',fill=(255,255,255,255)); d.rectangle((76,150,160,168),fill=(28,99,180,255)); d.rectangle((76,184,145,202),fill=(28,99,180,255)); im.save('DRM_Agent_Viewer.ico',sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"

echo.
echo [2/3] Registering DRM Agent Viewer as a selectable app...

set "LAUNCHER=%~dp0DRM_Agent_Viewer.cmd"
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo pythonw.exe "%%~dp0Client.py" --agent-open "%%~1"
) > "%LAUNCHER%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$base=(Get-Location).Path;" ^
  "$client=Join-Path $base 'Client.py';" ^
  "$launcher=Join-Path $base 'DRM_Agent_Viewer.cmd';" ^
  "$icon=Join-Path $base 'DRM_Agent_Viewer.ico';" ^
  "$python=(Get-Command python).Source;" ^
  "$pythonw=Join-Path (Split-Path -Parent $python) 'pythonw.exe';" ^
  "if(-not (Test-Path -LiteralPath $pythonw)){ $pythonw=$python }" ^
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
echo [3/3] Opening Windows default apps settings...
start ms-settings:defaultapps

echo.
echo Setup finished.
echo.
echo If DRM Agent Viewer is not listed in Windows Settings:
echo   Right-click a PDF - Open with - Choose another app - More apps
echo   Select DRM_Agent_Viewer.cmd in this folder.
echo.
echo Run the agent with: python Client.py
pause
