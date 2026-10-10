; Cisco Network Manager - NSIS installer (classic UI, ANSI / NSIS 2.51 compatible)
; Build:  python installer/build_installer.py
SetCompressor /SOLID lzma

!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!define VER "1.9.16"
!define APPNAME "Cisco Network Manager"
!define SERVICE "CiscoNetworkManager"
!define PORT "9632"

Name "${APPNAME}"
OutFile "dist_onefile\CiscoNetworkManager-v${VER}-setup.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
RequestExecutionLevel admin

Icon "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
UninstallIcon "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"

; ---- version info block (ASCII only for ANSI build) ----
VIProductVersion "${VER}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileVersion" "${VER}"
VIAddVersionKey "ProductVersion" "${VER}"
VIAddVersionKey "LegalCopyright" "(c) Cisco Network Manager"
VIAddVersionKey "FileDescription" "Cisco Network Manager Installer"

Var DataDir
Var DataDirCtl
Var ExistingDataDir
Var ExistingAppParameters
Var PreviousExeAvailable
Var HttpsCtl
Var EnableHttps
Var RuntimeExisted
Var WebScheme
Var PgCtl
Var EnablePg
Var DiagnosticHandle

!macro Diagnostic TEXT
  DetailPrint "${TEXT}"
  FileOpen $DiagnosticHandle "$INSTDIR\upgrade-diagnostics.log" a
  FileSeek $DiagnosticHandle 0 END
  FileWrite $DiagnosticHandle "${TEXT}$\r$\n"
  FileClose $DiagnosticHandle
!macroend

!macro StopForUpgrade
  nsExec::ExecToStack /TIMEOUT=90000 'powershell.exe -NoProfile -NonInteractive -File "$PLUGINSDIR\stop_instance.ps1" -InstallDir "$INSTDIR"'
  Pop $R1
  Pop $R2
  !insertmacro Diagnostic "Verified stop exit=$R1; $R2"
  ${If} $R1 != "0"
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
    MessageBox MB_ICONSTOP "The service could not be safely stopped. No further replacement will be attempted. Existing data and recovery files are retained. See upgrade-diagnostics.log."
    Abort
  ${EndIf}
!macroend

Function .onInit
  StrCpy $EnableHttps "0"
  StrCpy $EnablePg "0"
  StrCpy $WebScheme "http"
  ; Initialize upgrade state before any UI page. Silent installs skip custom
  ; page callbacks, so persistence must not depend on DataDirPageCreate.
  ReadRegStr $ExistingDataDir HKLM "Software\CiscoNetworkManager" "DataDir"
  ReadRegStr $ExistingAppParameters HKLM "SYSTEM\CurrentControlSet\Services\${SERVICE}\Parameters" "AppParameters"
  ${If} $ExistingDataDir != ""
    StrCpy $DataDir "$ExistingDataDir"
  ${Else}
    ; Resolve the default after the user selects the actual installation folder.
    StrCpy $DataDir ""
  ${EndIf}
FunctionEnd

; ---- pages ----
Page directory
Page custom DataDirPageCreate DataDirPageLeave
Page custom HttpsPageCreate HttpsPageLeave
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

; ===================== DATA DIR PAGE =====================
Function DataDirPageCreate
  ; Reuse the persisted data directory on every upgrade. For releases that
  ; predate this registry value, keep the existing NSSM parameters unchanged.
  ${If} $ExistingDataDir != ""
    StrCpy $DataDir "$ExistingDataDir"
  ${ElseIf} $DataDir == ""
    StrCpy $DataDir "$INSTDIR\data"
  ${EndIf}
  nsDialogs::Create 1018
  Pop $0

  ${NSD_CreateLabel} 0 0 100% 24u "Select the data storage directory. This can be a local drive (e.g. D:\NetMgrData) or a network share (e.g. \\server\share). The database, backups, and exports will be stored here."
  Pop $0

  ${NSD_CreateLabel} 0 32u 100% 12u "Data directory:"
  Pop $0

  ${NSD_CreateText} 0 48u 85% 12u "$DataDir"
  Pop $DataDirCtl

  ${NSD_CreateBrowseButton} 86% 48u 14% 12u "Browse..."
  Pop $0
  ${NSD_OnClick} $0 OnBrowseDataDir

  ${NSD_CreateLabel} 0 70u 100% 20u "Note: If upgrading, existing data will be copied to the new location. Leave as default to keep data next to the program."
  Pop $0

  nsDialogs::Show
FunctionEnd

Function OnBrowseDataDir
  nsDialogs::SelectFolderDialog "Select data directory" "$DataDir"
  Pop $0
  ${If} $0 != "error"
    ${NSD_SetText} $DataDirCtl "$0"
    StrCpy $DataDir "$0"
  ${EndIf}
FunctionEnd

Function DataDirPageLeave
  ${NSD_GetText} $DataDirCtl $0
  StrCpy $DataDir "$0"
FunctionEnd

Function HttpsPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateCheckbox} 0 12u 100% 20u "Enable HTTPS (preserve existing certificates on upgrade)"
  Pop $HttpsCtl
  ${NSD_SetState} $HttpsCtl $EnableHttps
  ${NSD_CreateLabel} 0 42u 100% 62u "A private certificate will be generated for localhost and this computer name. Browsers require administrator-approved certificate trust. Existing HTTPS settings are preserved even when this box is unchecked. Certificate and key files remain in the data directory."
  Pop $0
!ifdef WITH_PG
  ${NSD_CreateCheckbox} 0 115u 100% 20u "Install built-in PostgreSQL and migrate SQLite (preview)"
  Pop $PgCtl
  ${NSD_SetState} $PgCtl $EnablePg
!endif
  nsDialogs::Show
FunctionEnd

Function HttpsPageLeave
  ${NSD_GetState} $HttpsCtl $EnableHttps
!ifdef WITH_PG
  ${NSD_GetState} $PgCtl $EnablePg
!endif
FunctionEnd

; ===================== INSTALL =====================
Section "Install"
  ${If} $DataDir == ""
    StrCpy $DataDir "$INSTDIR\data"
  ${EndIf}
  CreateDirectory "$INSTDIR"
  !insertmacro Diagnostic "Beginning upgrade to v${VER}"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "payload\stop_instance.ps1"
  ; ---- stop and verify the existing service; retain its registration ----
  ; The running service (and nssm.exe itself) holds the files locked.
  ; CRITICAL: disable nssm auto-restart BEFORE stopping, otherwise nssm
  ; respawns the process between our stop and file-copy, re-locking the exe.
  DetailPrint "Disabling nssm auto-restart (AppExit Default Exit) ..."
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Exit'

  DetailPrint "Waiting for verified service shutdown ..."
  !insertmacro StopForUpgrade

  ; Keep the stopped service registration until the new executable is safely
  ; in place. If replacement fails, the previous service can be restarted.
  DetailPrint "Existing service registration retained for safe rollback"

  ; Keep the exact previous executable until the new service has passed its
  ; HTTP health check. This makes an interrupted or bad upgrade recoverable.
  StrCpy $PreviousExeAvailable "0"
  IfFileExists "$INSTDIR\CiscoNetworkManager.exe" 0 no_previous_exe
    ClearErrors
    CopyFiles /SILENT "$INSTDIR\CiscoNetworkManager.exe" "$INSTDIR\CiscoNetworkManager.previous.exe"
    IfErrors previous_exe_failed previous_exe_saved
  previous_exe_failed:
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" start ${SERVICE}'
    MessageBox MB_ICONSTOP "Cannot save the previous executable. Upgrade cancelled before replacement."
    Abort
  previous_exe_saved:
    StrCpy $PreviousExeAvailable "1"
    DetailPrint "Previous executable saved for upgrade rollback"
  no_previous_exe:

  SetOverwrite on
  SetOutPath "$INSTDIR"
  ; Extract the new exe under a TEMP name so we never touch the (possibly still
  ; locked) running CiscoNetworkManager.exe during extraction.
  File "/oname=CiscoNetworkManager.new" "payload\CiscoNetworkManager.exe"
  File "payload\README.txt"
  File "payload\INSTALLATION-NOTES.txt"
  File "payload\DEVICE-SUPPORT.txt"
  File "payload\sample_devices.csv"
  File "payload\start.bat"
  File "payload\service_probe.ps1"
!ifdef WITH_PG
  ; Install only when absent. Never overwrite binaries of a running DB service.
  IfFileExists "$INSTDIR\postgresql\pgsql\bin\pg_ctl.exe" pg_binaries_present 0
    SetOutPath "$INSTDIR\postgresql"
    File /r "payload\postgresql\*.*"
    SetOutPath "$INSTDIR"
  pg_binaries_present:
!endif

  ; ---- robust replace of the running exe (retry, then reboot-pending) ----
  DetailPrint "Replacing running executable (with retry) ..."
  StrCpy $R0 0
  replace_loop:
    ClearErrors
    Delete "$INSTDIR\CiscoNetworkManager.exe"
    Rename "$INSTDIR\CiscoNetworkManager.new" "$INSTDIR\CiscoNetworkManager.exe"
    IfErrors replace_retry replace_ok
  replace_retry:
    ; --- replace failed (file still locked): retry a few times ---
    IntOp $R0 $R0 + 1
    ${If} $R0 < 8
      DetailPrint "  replace blocked (attempt $R0), killing process and retrying ..."
      !insertmacro StopForUpgrade
      Sleep 2000
      Goto replace_loop
    ${EndIf}
    DetailPrint "ERROR: executable is still locked; restoring previous version"
    ${If} $PreviousExeAvailable == "1"
      CopyFiles /SILENT "$INSTDIR\CiscoNetworkManager.previous.exe" "$INSTDIR\CiscoNetworkManager.exe"
      nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
      nsExec::ExecToLog '"$INSTDIR\nssm.exe" start ${SERVICE}'
    ${EndIf}
    MessageBox MB_ICONSTOP "Upgrade failed because the old program could not be replaced. The previous executable was kept. Please restart Windows and run the installer again."
    Abort
  replace_ok:
  ; nssm.exe is the same cached binary across releases. On upgrade it may still
  ; be briefly locked even after killing the process, so skip overwriting it if
  ; it already exists to avoid "Error opening file for writing".
  IfFileExists "$INSTDIR\nssm.exe" nssm_already_present nssm_needs_extract
  nssm_needs_extract:
    DetailPrint "Extracting nssm.exe ..."
    File "payload\nssm.exe"
    Goto nssm_done
  nssm_already_present:
    DetailPrint "nssm.exe already present, skipping overwrite ..."
  nssm_done:

  CreateDirectory "$INSTDIR\data\backups"
  CreateDirectory "$INSTDIR\data\exports"

  ; ---- prepare custom data directory ----
  DetailPrint "Data directory: $DataDir"
  ${If} $DataDir != "$INSTDIR\data"
  ${AndIf} $DataDir != ""
    ; Create the custom data directory structure
    CreateDirectory "$DataDir"
    CreateDirectory "$DataDir\backups"
    CreateDirectory "$DataDir\exports"
    ; If upgrading: copy existing database + data from old location
    IfFileExists "$INSTDIR\data\netmgr.db" 0 skip_migrate
      DetailPrint "Migrating existing data to $DataDir ..."
      IfFileExists "$DataDir\netmgr.db" 0 copy_db
        DetailPrint "Database already exists in target, skipping migration ..."
        Goto skip_migrate
      copy_db:
        CopyFiles /SILENT "$INSTDIR\data\netmgr.db" "$DataDir\netmgr.db"
        CopyFiles /SILENT "$INSTDIR\data\netmgr.db-wal" "$DataDir\netmgr.db-wal"
        CopyFiles /SILENT "$INSTDIR\data\netmgr.db-shm" "$DataDir\netmgr.db-shm"
        ; Keep the installation key with its encrypted credentials and copy
        ; operator command customisations. These files are never overwritten.
        CopyFiles /SILENT "$INSTDIR\data\.secret_key" "$DataDir\.secret_key"
        CopyFiles /SILENT "$INSTDIR\data\initial_admin_password.txt" "$DataDir\initial_admin_password.txt"
        CopyFiles /SILENT "$INSTDIR\data\commands.json" "$DataDir\commands.json"
      ; Copy backup/export directories if they have content
      CopyFiles /SILENT "$INSTDIR\data\backups\*.*" "$DataDir\backups\"
      CopyFiles /SILENT "$INSTDIR\data\exports\*.*" "$DataDir\exports\"
    skip_migrate:
  ${EndIf}

  ; ---- firewall ----
  ; Snapshot only our managed configuration; private keys and DB files are kept.
  StrCpy $RuntimeExisted "0"
  IfFileExists "$DataDir\managed\runtime.json" 0 runtime_saved
    ClearErrors
    CopyFiles /SILENT "$DataDir\managed\runtime.json" "$DataDir\managed\runtime.previous.json"
    IfErrors runtime_backup_failed
    StrCpy $RuntimeExisted "1"
  runtime_saved:
!ifdef WITH_PG
  ${If} $EnablePg == "1"
    nsExec::ExecToStack /TIMEOUT=1200000 '"$INSTDIR\CiscoNetworkManager.exe" --service-context --data-dir "$DataDir" --setup-pg --pg-bin "$INSTDIR\postgresql\pgsql\bin"'
    Pop $R1
    Pop $R2
    ${If} $R1 == "20"
      DetailPrint "$R2"
      MessageBox MB_ICONEXCLAMATION|MB_YESNO "Windows application control blocked PostgreSQL. No migration was started; the original SQLite database and configuration are unchanged.$\r$\n$\r$\nContinue this upgrade using SQLite? Choose No to cancel and retain the previous program.$\r$\n$\r$\n$R2" /SD IDNO IDYES pg_continue_sqlite
      Goto runtime_prepare_failed
      pg_continue_sqlite:
      StrCpy $EnablePg "0"
      StrCpy $R1 "0"
    ${EndIf}
    ${If} $R1 != "0"
      DetailPrint "$R2"
      Goto runtime_prepare_failed
    ${EndIf}
  ${EndIf}
!endif
  ${If} $EnableHttps == "1"
    nsExec::ExecToStack '"$INSTDIR\CiscoNetworkManager.exe" --service-context --data-dir "$DataDir" --setup-https'
    Pop $R1
    Pop $R2
    ${If} $R1 != "0"
      Goto runtime_prepare_failed
    ${EndIf}
  ${EndIf}
  Goto runtime_prepare_ok
  runtime_prepare_failed:
    ${If} $RuntimeExisted == "1"
      CopyFiles /SILENT "$DataDir\managed\runtime.previous.json" "$DataDir\managed\runtime.json"
    ${Else}
      Delete "$DataDir\managed\runtime.json"
    ${EndIf}
    Goto runtime_restore_program
  runtime_backup_failed:
    ; Never delete the live configuration if creating its backup failed.
    DetailPrint "Cannot back up runtime settings; upgrade cancelled"
  runtime_restore_program:
    ${If} $PreviousExeAvailable == "1"
      CopyFiles /SILENT "$INSTDIR\CiscoNetworkManager.previous.exe" "$INSTDIR\CiscoNetworkManager.exe"
      nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
      nsExec::ExecToLog '"$INSTDIR\nssm.exe" start ${SERVICE}'
    ${EndIf}
    MessageBox MB_ICONSTOP "Database or HTTPS setup failed. Previous configuration and program were retained. Original SQLite and any PG recovery data were not removed."
    Abort
  runtime_prepare_ok:
  DetailPrint "Configuring Windows Firewall (TCP ${PORT}) ..."
  ; remove both current and any legacy (8080) rule to avoid stale leftovers
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="${SERVICE}-Web-${PORT}"'
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="${SERVICE}-Web-8080"'
  nsExec::ExecToLog 'netsh advfirewall firewall add rule name="${SERVICE}-Web-${PORT}" dir=in action=allow protocol=TCP localport=${PORT}'

  ; ---- register Windows service via nssm ----
  DetailPrint "Registering Windows service (${SERVICE}) ..."
  ; Retain existing service identity, environment and credentials on upgrades.
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" install ${SERVICE} "$INSTDIR\CiscoNetworkManager.exe"'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} Application "$INSTDIR\CiscoNetworkManager.exe"'
  ; Set AppParameters with --data-dir if custom data directory was chosen
  ${If} $DataDir != "$INSTDIR\data"
  ${AndIf} $DataDir != ""
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppParameters "--host 0.0.0.0 --port ${PORT} --data-dir $\"$DataDir$\""'
    DetailPrint "Service will use data dir: $DataDir"
    WriteRegStr HKLM "Software\CiscoNetworkManager" "DataDir" "$DataDir"
  ${ElseIf} $ExistingDataDir != ""
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppParameters "--host 0.0.0.0 --port ${PORT}"'
    WriteRegStr HKLM "Software\CiscoNetworkManager" "DataDir" "$DataDir"
  ${ElseIf} $ExistingAppParameters != ""
    ; First upgrade from an older installer with a custom data directory:
    ; preserve the service's exact parameters even though no app registry
    ; state existed yet.
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppParameters $\"$ExistingAppParameters$\"'
    DetailPrint "Preserved existing service parameters"
  ${Else}
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppParameters "--host 0.0.0.0 --port ${PORT}"'
    WriteRegStr HKLM "Software\CiscoNetworkManager" "DataDir" "$DataDir"
  ${EndIf}
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} DisplayName "${APPNAME}"'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} Description "${APPNAME} Web Service (v${VER})"'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} Start SERVICE_AUTO_START'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppDirectory "$INSTDIR"'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppStdout "$INSTDIR\service.log"'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppStderr "$INSTDIR\service.log"'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppRotateFiles 1'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppRotateOnline 1'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppRotateBytes 5242880'
  nsExec::ExecToStack '"$INSTDIR\nssm.exe" start ${SERVICE}'
  Pop $R1
  Pop $R2
  !insertmacro Diagnostic "Service start exit=$R1; $R2"

  ; Do not claim success until the newly installed version answers its health
  ; endpoint. A migration/import/startup error now triggers binary rollback.
  DetailPrint "Waiting for Cisco Network Manager v${VER} health check ..."
  StrCpy $R0 0
  health_loop:
    Sleep 2000
    nsExec::ExecToStack /TIMEOUT=30000 '"$INSTDIR\CiscoNetworkManager.exe" --service-context --health-check --expected-version ${VER}'
    Pop $R1
    Pop $R2
    !insertmacro Diagnostic "Health attempt $R0 exit=$R1; $R2"
    ${If} $R1 == "0"
      DetailPrint "Health check passed: v${VER}"
      Goto health_ok
    ${EndIf}
    IntOp $R0 $R0 + 1
    ${If} $R0 < 30
      DetailPrint "  service not ready yet ($R0/30)"
      Goto health_loop
    ${EndIf}

    DetailPrint "ERROR: new version did not become healthy; rolling back executable"
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Exit'
    !insertmacro StopForUpgrade
    ${If} $PreviousExeAvailable == "1"
      ${If} $RuntimeExisted == "1"
        CopyFiles /SILENT "$DataDir\managed\runtime.previous.json" "$DataDir\managed\runtime.json"
      ${Else}
        Delete "$DataDir\managed\runtime.json"
      ${EndIf}
      ClearErrors
      Delete "$INSTDIR\CiscoNetworkManager.exe"
      CopyFiles /SILENT "$INSTDIR\CiscoNetworkManager.previous.exe" "$INSTDIR\CiscoNetworkManager.exe"
      ${If} ${Errors}
        !insertmacro Diagnostic "Rollback executable restore failed; previous file retained"
        MessageBox MB_ICONSTOP "Rollback could not restore the executable. The previous executable and data were retained. See upgrade-diagnostics.log."
        Abort
      ${EndIf}
      nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
      nsExec::ExecToStack '"$INSTDIR\nssm.exe" start ${SERVICE}'
      Pop $R1
      Pop $R2
      !insertmacro Diagnostic "Rollback restart exit=$R1; $R2"
      MessageBox MB_ICONSTOP "The new version did not pass its health check. A rollback was attempted. See $INSTDIR\upgrade-diagnostics.log and service.log for details."
    ${Else}
      MessageBox MB_ICONSTOP "The service failed to start. See $INSTDIR\service.log for details."
    ${EndIf}
    Abort
  health_ok:
    Delete "$INSTDIR\CiscoNetworkManager.previous.exe"
    Delete "$DataDir\managed\runtime.previous.json"
    nsExec::ExecToStack '"$INSTDIR\CiscoNetworkManager.exe" --service-context --print-url'
    Pop $R1
    Pop $R2

  ; ---- Start Menu ----
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  WriteINIStr "$SMPROGRAMS\${APPNAME}\Manage.url" "InternetShortcut" "URL" "$R2"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk" "$INSTDIR\uninstall.exe"

  ; ---- uninstaller + ARP entry ----
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "DisplayName" "${APPNAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "DisplayVersion" "${VER}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "Publisher" "${APPNAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "DisplayIcon" "$INSTDIR\CiscoNetworkManager.exe"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}" "NoRepair" 1
  ; Do not replace an unknown legacy --data-dir with the displayed default.
  ; When no registry state exists but service parameters do, those parameters
  ; remain the source of truth on subsequent upgrades.
  ${If} $ExistingDataDir != ""
    WriteRegStr HKLM "Software\CiscoNetworkManager" "DataDir" "$DataDir"
  ${ElseIf} $ExistingAppParameters == ""
    WriteRegStr HKLM "Software\CiscoNetworkManager" "DataDir" "$DataDir"
  ${ElseIf} $DataDir != "$INSTDIR\data"
    WriteRegStr HKLM "Software\CiscoNetworkManager" "DataDir" "$DataDir"
  ${EndIf}
  WriteRegStr HKLM "Software\CiscoNetworkManager" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\CiscoNetworkManager" "Version" "${VER}"

  DetailPrint "Installation complete. Open the Start Menu management shortcut."
SectionEnd

; ===================== UNINSTALL =====================
Section "Uninstall"
  DetailPrint "Stopping and removing service ..."
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" stop ${SERVICE}'
  nsExec::ExecToLog '"$INSTDIR\nssm.exe" remove ${SERVICE} confirm'

  DetailPrint "Removing firewall rule ..."
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="${SERVICE}-Web-${PORT}"'

  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SERVICE}"

  RMDir /r "$SMPROGRAMS\${APPNAME}"

  ; Remove program artifacts but preserve data/ and .env. This makes an
  ; uninstall/reinstall recoverable and prevents an interrupted upgrade from
  ; deleting the database, encryption key or operator configuration.
  Delete "$INSTDIR\CiscoNetworkManager.exe"
  Delete "$INSTDIR\CiscoNetworkManager.new"
  Delete "$INSTDIR\CiscoNetworkManager.previous.exe"
  Delete "$INSTDIR\nssm.exe"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\sample_devices.csv"
  Delete "$INSTDIR\start.bat"
  Delete "$INSTDIR\service.log"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  DetailPrint "Uninstalled ${APPNAME}; persistent data and .env were kept"
SectionEnd
