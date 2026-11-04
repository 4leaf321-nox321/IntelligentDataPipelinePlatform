@echo off
rem 최신 릴리스 번들 받기 — 더블클릭. 같은 폴더의 downloads\ 에 받는다.
rem 옵션은 fetch-release.ps1 머리말 참고 (예: fetch-release.bat -Apptainer -Out D:\bundles)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch-release.ps1" %*
if errorlevel 1 (echo. & echo 실패했습니다. 위 메시지를 보세요.) else (echo. & echo 끝.)
pause
