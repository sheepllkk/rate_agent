@echo off
rem Start the background FX monitor (pythonw, no console window)
rem Kill any existing instance first to avoid double-running
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*bg_loop.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
start "" "C:\Users\sheep\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe" "D:\Users\sheep\Desktop\rate_agent\bg_loop.py"
echo Background monitor started (old instance stopped first).
