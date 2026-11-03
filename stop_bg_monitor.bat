@echo off
rem Stop the background FX monitor (kills only the bg_loop.py process)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*bg_loop.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Background monitor stopped.
