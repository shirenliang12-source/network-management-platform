@echo off
chcp 65001 >nul 2>&1
title Cisco 网络自动化运维平台

sc query CiscoNetworkManager | find "RUNNING" >nul 2>&1
if errorlevel 1 (
    echo 正在启动 CiscoNetworkManager 服务...
    net start CiscoNetworkManager
)

start "" "http://localhost:9632/"
