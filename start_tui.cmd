@echo off
rem ZHIBO TUI 启动器：优先 Windows Terminal（Textual 渲染最佳），退回普通控制台。
rem 坑：wt 会重新切分命令行并吞掉引号，本路径含空格（D:\WPS SyncDisk），
rem 传绝对路径必挂（0x80070002 找不到文件）。因此先 cd 到项目根，
rem 再以相对路径交给 wt —— 相对路径没有空格，不会被截断。
cd /d "%~dp0"
where wt >nul 2>nul
if %errorlevel%==0 (
  wt -d . cmd /c .venv\Scripts\python.exe -m tui
) else (
  ".venv\Scripts\python.exe" -m tui
)
