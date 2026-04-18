@echo off
chcp 65001 >nul
echo ============================================
echo   터틀 트레이딩 시스템 - 윈도우 설치
echo ============================================
echo.

:: Python 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 설치하세요.
    echo 설치 시 "Add Python to PATH" 반드시 체크!
    pause
    exit /b 1
)

echo [1/2] Python 패키지 설치 중...
pip install -r "%~dp0requirements.txt"

echo.
echo [2/2] 설치 완료!
echo.
echo 사용법:
echo   일일 스캔:    run.bat
echo   백테스트:     run_backtest.bat
echo.
pause
