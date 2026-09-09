@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
set "SRC=%~dp0"
set "PERSIST=%LOCALAPPDATA%\CiscoNM-Setup"

:: 1) 先把整个载荷复制到一个“常驻”目录，避免 7z 自解压结束后删除临时目录导致提权后的脚本找不到文件
if not exist "%PERSIST%" mkdir "%PERSIST%" >nul 2>&1
xcopy /Y /E /I /Q "%SRC%*.*" "%PERSIST%" >nul 2>&1

:: 2) 非管理员 -> 用常驻副本提权重启（带 -Wait，保证 7z 不会提前清理临时目录）
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%PERSIST%\install.cmd' -Verb RunAs -Wait"
    exit /b
)

:: 3) 已是管理员：执行真正的安装
powershell -ExecutionPolicy Bypass -NoProfile -File "%PERSIST%\install.ps1"
endlocal
