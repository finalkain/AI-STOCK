@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 터틀 백테스트 실행 중...
python backtest/run_backtest.py
echo.
echo 결과가 다운로드 폴더에 저장되었습니다.
pause
