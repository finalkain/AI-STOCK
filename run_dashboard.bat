@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 투자 비서 대시보드 실행 중...
echo 브라우저에서 http://localhost:8501 을 열어주세요.
streamlit run dashboard.py --server.headless true
pause
