@echo off
chcp 65001 >nul
title 安装依赖 - Cisco 网络自动化运维平台

echo ========================================
echo   安装 Python 依赖包
echo ========================================
echo.

set VENV_PIP=C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\pip.exe

if not exist "%VENV_PIP%" (
    echo [错误] 找不到 pip: %VENV_PIP%
    pause
    exit /b 1
)

cd /d "%~dp0"

echo [信息] 正在安装依赖...
"%VENV_PIP%" install -r requirements.txt

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [成功] 依赖安装完成！
    echo [信息] 请运行 start.bat 启动服务
) else (
    echo.
    echo [错误] 依赖安装失败，请检查网络连接
)

pause
