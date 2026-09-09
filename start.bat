@echo off
chcp 65001 >nul
title Cisco 网络自动化运维平台

echo ========================================
echo   Cisco 网络自动化运维平台 启动脚本
echo ========================================
echo.

REM Python venv path
set VENV_PYTHON=C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe

REM Check if venv exists
if not exist "%VENV_PYTHON%" (
    echo [错误] 找不到 Python 虚拟环境: %VENV_PYTHON%
    echo 请先运行 install.bat 安装依赖
    pause
    exit /b 1
)

REM Change to project directory
cd /d "%~dp0"

echo [信息] 正在启动服务...
echo [信息] 访问地址: http://localhost:9632
echo [信息] API文档:  http://localhost:9632/docs
echo [信息] 按 Ctrl+C 停止服务
echo.

"%VENV_PYTHON%" run.py

pause
