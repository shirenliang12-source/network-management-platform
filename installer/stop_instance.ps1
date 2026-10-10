#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath((Join-Path $InstallDir 'CiscoNetworkManager.exe'))
$wrapper = [IO.Path]::GetFullPath((Join-Path $InstallDir 'nssm.exe'))
function Read-ServiceState {
    Get-CimInstance Win32_Service -Filter "Name='CiscoNetworkManager'"
}
function Get-TargetProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='CiscoNetworkManager.exe'" | Where-Object {
        $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target
    })
}
function Wait-Stopped([int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $state = Read-ServiceState
        if ((!$state -or $state.State -eq 'Stopped') -and (Get-TargetProcesses).Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}
try {
    $state = Read-ServiceState
    if ($state) {
        $registered = (Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\CiscoNetworkManager\Parameters').Application
        if (!$registered -or [IO.Path]::GetFullPath($registered) -ine $target) {
            throw 'Service application path differs from installation directory; no processes were stopped.'
        }
        if ($state.State -ne 'Stopped' -and $state.State -ne 'Stop Pending') {
            # Request only; do not block indefinitely in Stop-Service/NSSM.
            $controller = Get-Service CiscoNetworkManager
            $controller.Stop()
        }
    }
    if (Wait-Stopped 30) { Write-Output 'Service and application stopped.'; exit 0 }
    foreach ($process in Get-TargetProcesses) {
        # Recheck path on the live process handle before terminating it.
        $live = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        if ($live -and $live.Path -and [IO.Path]::GetFullPath($live.Path) -ieq $target) {
            $live.Kill()
        }
    }
    if (Wait-Stopped 10) { Write-Output 'Application stopped after timeout.'; exit 0 }
    $state = Read-ServiceState
    if ($state -and $state.State -eq 'Stop Pending' -and $state.ProcessId -gt 0 -and (Get-TargetProcesses).Count -eq 0) {
        $live = Get-Process -Id $state.ProcessId -ErrorAction Stop
        if (!$live.Path -or [IO.Path]::GetFullPath($live.Path) -ine $wrapper) {
            throw 'Stuck service wrapper path mismatch; termination refused.'
        }
        $confirmed = Read-ServiceState
        if ($confirmed.State -ne 'Stop Pending' -or $confirmed.ProcessId -ne $live.Id) {
            throw 'Service changed during recovery; termination refused.'
        }
        Write-Output "Recovering verified STOP_PENDING wrapper PID $($live.Id)."
        $live.Kill()
    }
    if (!(Wait-Stopped 20)) { throw 'Service did not reach STOPPED; replacement refused.' }
    Write-Output 'Verified service STOPPED and no application processes remain.'
    exit 0
} catch {
    Write-Output "Stop failed: $($_.Exception.Message)"
    exit 1
}
