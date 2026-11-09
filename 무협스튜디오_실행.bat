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
echo 서버 실행 중. 준비가 끝나면 브라우저(127.0.0.1:7860)가 열립니다.
echo 처음 뜨는 데 10~30초 걸립니다(gradio/whisper 적재). 기다리세요.
echo 종료: 작업표시줄의 이 창을 열어 닫거나 Ctrl+C.
echo.

rem 이미 떠 있으면 또 띄우지 않는다 — 7860 은 고정 포트라 두 번째는 실패한다
netstat -ano | findstr "127.0.0.1:7860" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [이미 실행 중입니다] 브라우저만 엽니다.
    start "" http://127.0.0.1:7860
    exit /b 0
)

rem 포트가 열릴 때까지 기다린 뒤 브라우저를 연다(고정 대기 금지)
start "" /min cmd /c ""%~dp0_브라우저_열기.bat""
".venv\Scripts\python.exe" app.py
echo.
echo [서버가 종료되었습니다]
pause
