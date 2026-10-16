@echo off
rem Drag and drop mp3 files onto this file, or run: titulky.bat file.mp3 --lang en
"%~dp0.venv\Scripts\python.exe" "%~dp0titulky.py" %*
pause
