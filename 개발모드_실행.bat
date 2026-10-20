@echo off
if not "%~1"=="_min" (
    start "무협 스튜디오(개발)" /min "%~f0" _min
    exit /b
)
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ================================================
echo    [개발 모드] 무협 스튜디오 - 자동 리로드
echo ================================================
echo app.py 저장 시 자동 재시작. 반영은 브라우저 F5.
echo 종료: 작업표시줄의 이 창을 열어 닫거나 Ctrl+C.
echo.
start "" cmd /c "ping -n 4 127.0.0.1 >nul & start http://127.0.0.1:7860"
".venv\Scripts\python.exe" -m gradio app.py
echo.
echo [서버가 종료되었습니다]
pause
