@echo off
cd /d "%~dp0"
pythonw.exe "%~dp0Client.py" --agent-open "%~1"
