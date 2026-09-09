#Requires -Version 5.1
<#
  Cisco Network Manager 卸载脚本
  - 停止并移除 Windows 服务
  - 删除防火墙规则
  - 删除开始菜单快捷方式
  - 删除安装目录
#>

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
        "-ExecutionPolicy Bypass -NoProfile -File `"$($MyInvocation.MyCommand.Definition)`""
    )
    exit 0
}

$ErrorActionPreference = 'Continue'
$AppName    = 'CiscoNetworkManager'
$InstallDir = Join-Path $env:ProgramFiles $AppName
$nssm       = Join-Path $InstallDir 'nssm.exe'
$LogFile    = Join-Path $env:TEMP 'cisco-netmgr-uninstall.log'

function Log($m) { "$m" | Tee-Object -FilePath $LogFile -Append }

Log '=== 卸载开始 ==='
if (Test-Path $nssm) {
    & $nssm stop    $AppName | Out-Null
    & $nssm remove  $AppName confirm | Out-Null
    Log '服务已停止并移除'
} else {
    Log '未找到 nssm，尝试用 sc 移除'
    sc.exe stop   $AppName | Out-Null
    sc.exe delete $AppName | Out-Null
}

netsh advfirewall firewall delete rule name="CiscoNetworkManager-Web-9632" | Out-Null
Log '防火墙规则已移除'

$sm = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs' $AppName
if (Test-Path $sm) { Remove-Item $sm -Recurse -Force; Log '开始菜单已移除' }

if (Test-Path $InstallDir) {
    # Preserve data/ and .env by default so uninstall/reinstall or a failed
    # upgrade cannot erase database, encryption key, commands or configuration.
    $appFiles = @(
        'CiscoNetworkManager.exe', 'CiscoNetworkManager.new', 'nssm.exe',
        '使用说明.txt', 'README.txt', 'sample_devices.csv', '启动.bat',
        'start.bat', 'uninstall.ps1', 'service.log'
    )
    foreach ($name in $appFiles) {
        $path = Join-Path $InstallDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue
    Log "程序文件已移除；data 目录和 .env（如存在）已保留: $InstallDir"
}

Log '=== 卸载完成 ==='
Read-Host '卸载完成，按回车关闭此窗口'
