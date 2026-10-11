@echo off
cd /d %~dp0
if exist venv\Scripts\activate.bat goto run
python -m venv venv
call venv\Scripts\activate.bat
set /p G="NVIDIA GPU? (y/n): "
if /i "%G%"=="y" (pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124) else (pip install torch torchvision)
pip install -r requirements.txt
goto go
:run
call venv\Scripts\activate.bat
:go
python launcher.py
pause
