@echo off
rem ZHIBO TUI 启动器：优先 Windows Terminal（Textual 渲染最佳），退回普通控制台。
rem 不要在 Git Bash (mintty) 窗口里跑 —— mintty 对 Textual 有已知渲染问题。
cd /d "%~dp0"
where wt >nul 2>nul
if %errorlevel%==0 (
  start "" wt -d "%~dp0" "%~dp0.venv\Scripts\python.exe" -m tui
) else (
  "%~dp0.venv\Scripts\python.exe" -m tui
)
