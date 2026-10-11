@echo off
rem Builds dist\DoujinTranslator\DoujinTranslator.exe  (run inside the same Python env you use for the app, e.g.  conda activate comic)
cd /d %~dp0
python -m pip install --upgrade pyinstaller gallery-dl
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller doujin.spec --noconfirm
if errorlevel 1 ( echo. & echo BUILD FAILED - see the messages above. & pause & exit /b 1 )
mkdir dist\DoujinTranslator\fonts 2>nul
copy /y README.md dist\DoujinTranslator\ >nul
echo.
echo Done:  dist\DoujinTranslator\DoujinTranslator.exe
echo Zip that whole folder to share it. Models download on first use into the "models" folder next to the exe.
pause
