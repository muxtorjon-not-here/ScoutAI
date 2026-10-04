@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "Get-Content scout.log -Tail 40 -Wait"
