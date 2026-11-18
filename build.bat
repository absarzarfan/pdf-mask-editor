@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip >nul
pip install -r requirements-dev.txt || goto :error

echo Running tests...
python -m pytest -q || goto :error

echo Building PDFMaskEditor.exe...
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name PDFMaskEditor ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    main.py || goto :error

echo.
echo Done: dist\PDFMaskEditor.exe
pause
exit /b 0

:error
echo.
echo Build failed. Read the messages above.
pause
exit /b 1
