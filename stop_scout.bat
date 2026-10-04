@echo off
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'ScoutAI.*main\.py|main\.py' -and $_.ExecutablePath -like '*ScoutAI*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'Stopped PID ' + $_.ProcessId }"
pause
