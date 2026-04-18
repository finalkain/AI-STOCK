@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 터틀 일일 스캔 실행 중...
python daily_scan.py
echo.
echo 결과가 다운로드 폴더에 저장되었습니다.
pause
