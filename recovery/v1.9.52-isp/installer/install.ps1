#Requires -Version 5.1
<#
  Cisco Network Manager 安装脚本（由 7z 自解压安装包调用）
  - 复制到 C:\Program Files\CiscoNetworkManager
  - 放行 Windows 防火墙 TCP 9632
  - 用 nssm 注册为 Windows 服务（开机自启、无界面、异常自动重启）
  - 创建开始菜单快捷方式（管理界面 / 卸载）
#>

function Test-Admin {
    $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 非管理员 -> 提权重启并等待
if (-not (Test-Admin)) {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
        "-ExecutionPolicy Bypass -NoProfile -File `"$($MyInvocation.MyCommand.Definition)`""
    )
    exit 0
}

$ErrorActionPreference = 'Continue'
$AppName    = 'CiscoNetworkManager'
$InstallDir = Join-Path $env:ProgramFiles $AppName
$Port       = 9632
$BindHost   = '0.0.0.0'
$Url        = "http://localhost:$Port"
$LogFile    = Join-Path $env:LOCALAPPDATA 'CiscoNM-Setup\install.log'
$ExpectedVersion = (Get-Content -LiteralPath (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'version.txt') -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
$PreviousExe = Join-Path $InstallDir 'CiscoNetworkManager.previous.exe'
$RollbackReady = $false

function Log($msg) {
    $t = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "$t  $msg" | Tee-Object -FilePath $LogFile -Append
}

try {
    Log "=== Cisco Network Manager 安装开始 (v$ExpectedVersion) ==="
    $src = Split-Path -Parent $MyInvocation.MyCommand.Definition
    Log "源目录 : $src"
    Log "目标目录: $InstallDir"

    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }

    # ---- 选择数据存储目录 ----
    $defaultDataDir = Join-Path $InstallDir 'data'
    $StateKey = 'HKLM:\Software\CiscoNetworkManager'
    $DataDir = $null
    try {
        $DataDir = (Get-ItemProperty -LiteralPath $StateKey -Name DataDir -ErrorAction Stop).DataDir
    } catch {}
    # Compatibility with installations made before DataDir was persisted in
    # our registry key: recover a custom path from the existing NSSM service.
    if (-not $DataDir) {
        try {
            $serviceKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\CiscoNetworkManager\Parameters'
            $existingParams = (Get-ItemProperty -LiteralPath $serviceKey -Name AppParameters -ErrorAction Stop).AppParameters
            if ($existingParams -match '--data-dir\s+(?:"([^"]+)"|(\S+))') {
                $DataDir = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
            }
        } catch {}
    }
    if (-not $DataDir) { $DataDir = $defaultDataDir }
    Add-Type -AssemblyName System.Windows.Forms
    $fb = New-Object System.Windows.Forms.FolderBrowserDialog
    $fb.Description = "选择数据存储目录（可放本地其他盘或网络远程文件）`r`n`r`n默认: $defaultDataDir`r`n数据库、备份、导出文件将保存在此目录。"
    $fb.SelectedPath = $DataDir
    $fb.ShowNewFolderButton = $true
    $dlgResult = $fb.ShowDialog()
    if ($dlgResult -eq [System.Windows.Forms.DialogResult]::OK -and $fb.SelectedPath) {
        $DataDir = $fb.SelectedPath
    }
    Log "数据目录: $DataDir"

    # 0) 先关闭 nssm 自动重启，防止 stop→copy 之间进程被自动拉起重锁文件
    $nssmOld = Join-Path $InstallDir 'nssm.exe'
    if (Test-Path $nssmOld) {
        Log '禁用 nssm 自动重启 (AppExit Default Exit) ...'
        & $nssmOld set $AppName AppExit Default Exit 2>&1 | Out-Null
        Log '停止旧服务 ...'
        & $nssmOld stop $AppName 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    }

    # 0a) 强杀所有残留进程（确保 exe 文件不被锁）
    Log '强杀残留 CiscoNetworkManager.exe 进程 ...'
    taskkill /F /IM CiscoNetworkManager.exe 2>&1 | Out-Null
    Start-Sleep -Seconds 1
    # 也杀掉可能残留的 nssm.exe（旧的）
    taskkill /F /IM nssm.exe 2>&1 | Out-Null
    Start-Sleep -Seconds 1

    # 1) 复制顶层文件（此时文件已解锁）
    $currentExe = Join-Path $InstallDir 'CiscoNetworkManager.exe'
    if (Test-Path -LiteralPath $currentExe) {
        Copy-Item -LiteralPath $currentExe -Destination $PreviousExe -Force -ErrorAction Stop
        $RollbackReady = $true
        Log "已保存上一版本程序: $PreviousExe"
    }
    $top = @(
        'CiscoNetworkManager.exe', '使用说明.txt', 'README.txt',
        'sample_devices.csv', '启动.bat', 'start.bat', 'nssm.exe', 'uninstall.ps1'
    )
    foreach ($f in $top) {
        $s = Join-Path $src $f
        if (Test-Path $s) {
            Copy-Item $s (Join-Path $InstallDir $f) -Force -ErrorAction Stop
            Log "已复制 $f"
        } else { Log "WARN 缺失 $f" }
    }

    # 2) data 目录（默认位置）
    $dataDst = Join-Path $InstallDir 'data'
    foreach ($sub in @('backups', 'exports')) { New-Item -ItemType Directory -Path (Join-Path $dataDst $sub) -Force | Out-Null }
    # commands.json is intentionally not copied by the installer. The program
    # creates defaults only when the file is absent, preserving all edits on upgrade.

    # 2a) 如果选择了自定义数据目录，创建结构并迁移数据
    if ($DataDir -ne $defaultDataDir) {
        Log "配置自定义数据目录: $DataDir"
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $DataDir 'backups') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $DataDir 'exports') -Force | Out-Null
        # Migrate existing data if upgrading
        $oldDb = Join-Path $dataDst 'netmgr.db'
        $newDb = Join-Path $DataDir 'netmgr.db'
        if ((Test-Path $oldDb) -and -not (Test-Path $newDb)) {
            Log '迁移现有数据到新数据目录 ...'
            # Copy the complete persistent directory, including hidden
            # .secret_key, commands.json, WAL files, backups and exports.
            & robocopy.exe $dataDst $DataDir /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "数据目录迁移失败，robocopy 退出码: $LASTEXITCODE" }
            Log '数据迁移完成'
        } elseif ((Test-Path $oldDb) -and (Test-Path $newDb)) {
            Log '目标目录已有数据库，跳过迁移'
        }
    }

    # 3) 防火墙
    $fw = "CiscoNetworkManager-Web-$Port"
    netsh advfirewall firewall delete rule name="$fw" | Out-Null
    $r = netsh advfirewall firewall add rule name="$fw" dir=in action=allow protocol=TCP localport=$Port 2>&1
    Log "防火墙规则: $r"

    # 4) 注册 Windows 服务（nssm）—— 重新安装确保使用新 exe
    $nssm = Join-Path $InstallDir 'nssm.exe'
    $exe  = Join-Path $InstallDir 'CiscoNetworkManager.exe'
    & $nssm remove $AppName confirm 2>&1 | Out-Null
    & $nssm install $AppName $exe | Out-Null
    # 设置启动参数（含 --data-dir 如果选择了自定义数据目录）
    if ($DataDir -ne $defaultDataDir) {
        $appParams = "--host $BindHost --port $Port --data-dir `"$DataDir`""
        Log "服务参数: $appParams"
    } else {
        $appParams = "--host $BindHost --port $Port"
    }
    & $nssm set $AppName AppParameters $appParams | Out-Null
    & $nssm set $AppName DisplayName "Cisco Network Manager" | Out-Null
    & $nssm set $AppName Description "Cisco 网络自动化运维平台 Web 服务 (v$ExpectedVersion)" | Out-Null
    & $nssm set $AppName Start SERVICE_AUTO_START | Out-Null
    & $nssm set $AppName AppDirectory $InstallDir | Out-Null
    & $nssm set $AppName AppExit Default Restart | Out-Null
    & $nssm set $AppName AppStdout (Join-Path $InstallDir 'service.log') | Out-Null
    & $nssm set $AppName AppStderr (Join-Path $InstallDir 'service.log') | Out-Null
    Log 'nssm 服务已注册'

    # 5) 启动服务
    $out = & $nssm start $AppName 2>&1
    Log "启动服务: $out"

    # 6) 等待健康检查；升级必须同时确认服务可访问和版本正确。
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 2
            if ($health.status -eq 'ok' -and (!$ExpectedVersion -or $health.version -eq $ExpectedVersion)) {
                $ok = $true
                break
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $ok) {
        throw "新版服务未通过健康检查，安装程序将恢复上一版本。详情请查看 $LogFile"
    }
    Log "健康检查: OK (v$($health.version))"

    # 只有新版确认可用后才提交安装状态，避免失败升级覆盖已有配置指针。
    New-Item -Path $StateKey -Force | Out-Null
    New-ItemProperty -Path $StateKey -Name DataDir -Value $DataDir -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $StateKey -Name Version -Value $ExpectedVersion -PropertyType String -Force | Out-Null
    Log "已持久化数据目录: $DataDir"

    if (Test-Path -LiteralPath $PreviousExe) {
        Remove-Item -LiteralPath $PreviousExe -Force -ErrorAction SilentlyContinue
    }
    $RollbackReady = $false

    # 7) 开始菜单快捷方式
    $sm = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs' $AppName
    New-Item -ItemType Directory -Path $sm -Force | Out-Null
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $sm 'Cisco Network Manager 管理界面.url'))
    $lnk.TargetPath = $Url; $lnk.Save()
    $ul = $ws.CreateShortcut((Join-Path $sm '卸载 Cisco Network Manager.lnk'))
    $ul.TargetPath = 'powershell.exe'
    $ul.Arguments = "-ExecutionPolicy Bypass -NoProfile -File `"$(Join-Path $InstallDir 'uninstall.ps1')`""
    $ul.WorkingDirectory = $InstallDir
    $ul.Save()
    Log '开始菜单快捷方式已创建'

    Log '=== 安装完成 ==='
    Log "访问地址: $Url"
    Log "安装日志: $LogFile"
} catch {
    Log "ERROR: $_"
    Log $_.ScriptStackTrace
    if ($RollbackReady -and (Test-Path -LiteralPath $PreviousExe)) {
        Log '正在恢复上一版本程序 ...'
        try {
            $rollbackNssm = Join-Path $InstallDir 'nssm.exe'
            if (Test-Path -LiteralPath $rollbackNssm) {
                & $rollbackNssm stop $AppName 2>&1 | Out-Null
            }
            Start-Sleep -Seconds 2
            Copy-Item -LiteralPath $PreviousExe -Destination (Join-Path $InstallDir 'CiscoNetworkManager.exe') -Force -ErrorAction Stop
            if (Test-Path -LiteralPath $rollbackNssm) {
                & $rollbackNssm start $AppName 2>&1 | Out-Null
            }
            Log '上一版本程序已恢复并重新启动。'
        } catch {
            Log "回滚失败: $_"
        }
    }
}

Read-Host '安装完成，按回车关闭此窗口'
