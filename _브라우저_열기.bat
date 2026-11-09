@echo off
rem ───────────────────────────────────────────────────────────────
rem  서버가 7860 포트를 실제로 잡은 뒤에 브라우저를 연다.
rem
rem  종전에는 "ping -n 4" 로 3초만 기다리고 브라우저를 열었다.
rem  app.py 는 gradio/faster-whisper/torch 를 불러오느라 10~30초가 걸려서,
rem  브라우저가 서버보다 먼저 열리고 "연결할 수 없음" 이 떴다.
rem  그래서 실행이 안 된 것처럼 보였다. 서버는 그 뒤에 조용히 올라온다.
rem ───────────────────────────────────────────────────────────────
setlocal
set PORT=7860
set /a n=0

:wait
netstat -ano | findstr "127.0.0.1:%PORT%" | findstr "LISTENING" >nul
if not errorlevel 1 goto open
set /a n+=1
if %n% geq 180 goto giveup
timeout /t 1 /nobreak >nul
goto wait

:open
start "" http://127.0.0.1:%PORT%
exit /b 0

:giveup
echo.
echo [3분을 기다렸지만 서버가 %PORT% 포트를 잡지 못했습니다]
echo  - 무협 스튜디오 창(작업표시줄)을 열어 오류 메시지를 확인하세요.
echo.
pause
exit /b 1
