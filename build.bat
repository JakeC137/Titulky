@echo off
rem Rebuilds the portable app in dist\Titulky from app.py + titulky.py.
cd /d "%~dp0"
set NV=.venv\Lib\site-packages\nvidia
.venv\Scripts\pyinstaller.exe --noconfirm --windowed --name Titulky ^
  --collect-data faster_whisper --collect-binaries ctranslate2 ^
  --add-data "%NV%\cublas\bin;nvidia\cublas\bin" ^
  --add-data "%NV%\cudnn\bin;nvidia\cudnn\bin" ^
  --add-data "%NV%\cuda_nvrtc\bin;nvidia\cuda_nvrtc\bin" ^
  app.py || exit /b 1

rem Bundle the Whisper model so the app works without downloading anything.
for /d %%d in ("%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-large-v3\snapshots\*") do (
  robocopy "%%d" dist\Titulky\models\large-v3 /NJH /NJS /NFL /NDL >nul
)
copy /y README.txt dist\Titulky\ >nul
echo Built dist\Titulky
