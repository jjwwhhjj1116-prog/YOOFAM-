@echo off
if not "%~1"=="_min" (
    start "무협 스튜디오" /min "%~f0" _min
    exit /b
)
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ================================================
echo    무협 영상+TTS+자막 변환 스튜디오
echo ================================================
echo 서버 실행 중. 브라우저(127.0.0.1:7860)가 자동으로 열립니다.
echo 종료: 작업표시줄의 이 창을 열어 닫거나 Ctrl+C.
echo.
start "" cmd /c "ping -n 4 127.0.0.1 >nul & start http://127.0.0.1:7860"
".venv\Scripts\python.exe" app.py
echo.
echo [서버가 종료되었습니다]
pause
