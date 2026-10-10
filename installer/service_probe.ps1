#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Executable,
      [Parameter(Mandatory=$true)][string]$DataDir,
      [string]$ExpectedVersion, [switch]$PrintUrl,
      [switch]$SetupHttps, [switch]$SetupPg, [string]$PgBin)
$ErrorActionPreference = 'Stop'
try {
    $serviceSettings = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\CiscoNetworkManager\Parameters' -ErrorAction SilentlyContinue
    foreach ($entry in @($serviceSettings.AppEnvironment) + @($serviceSettings.AppEnvironmentExtra)) {
        if ($entry -match '^(NETMGR_[A-Z_]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
    if ($serviceSettings.AppParameters -match '--port\s+(\d+)') {
        $env:NETMGR_PORT = $Matches[1]
    }
    if ($serviceSettings.AppDirectory) { Set-Location -LiteralPath $serviceSettings.AppDirectory }
    if ($SetupPg) { & $Executable --data-dir $DataDir --setup-pg --pg-bin $PgBin }
    elseif ($SetupHttps) { & $Executable --data-dir $DataDir --setup-https }
    elseif ($PrintUrl) { & $Executable --data-dir $DataDir --print-url }
    else { & $Executable --data-dir $DataDir --health-check --expected-version $ExpectedVersion }
    exit $LASTEXITCODE
} catch { Write-Error 'Service health probe failed'; exit 1 }
