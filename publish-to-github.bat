@echo off
rem Put this file INSIDE the unzipped doujin-comic-translator folder, then double-click it.
cd /d %~dp0
where git >nul 2>nul
if errorlevel 1 goto nogit
set REPO=https://github.com/MrBeAsT1020/doujin-comic-translator.git
git config user.email >nul 2>nul
if not errorlevel 1 goto have_id
set /p E=Email for your commits: 
set /p N=Your name: 
git config user.email "%E%"
git config user.name "%N%"
:have_id
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote remove origin 2>nul
git remote add origin %REPO%
echo.
echo If a sign-in window appears, log in to GitHub there.
git push -u origin main
if not errorlevel 1 goto done
echo.
echo The push was refused, probably because the repo already has a README or other files.
set /p F=Overwrite what is on GitHub with this folder? (y/n): 
if /i not "%F%"=="y" goto done
git push -u origin main --force
:done
echo.
echo Finished. Check https://github.com/MrBeAsT1020/doujin-comic-translator
pause
exit /b 0
:nogit
echo Git is not installed. Get it from https://git-scm.com/download/win and run this again.
pause
