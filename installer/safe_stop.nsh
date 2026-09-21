; Native SCM polling. Never image-kill NSSM or unrelated platform instances.
Var StopManager
Var StopService
Var StopBuffer
Var StopResult
Var StopCount

Function VerifiedStop
  StrCpy $StopResult 1
  System::Call 'advapi32::OpenSCManagerW(p 0, p 0, i 1) p .r0'
  StrCpy $StopManager $0
  ${If} $StopManager == 0
    Return
  ${EndIf}
  System::Call 'advapi32::OpenServiceW(p $StopManager, w "${SERVICE}", i 36) p .r0'
  StrCpy $StopService $0
  ${If} $StopService == 0
    System::Call 'kernel32::GetLastError() i .r0'
    ${If} $0 == 1060
      StrCpy $StopResult 0
    ${EndIf}
    Goto stop_close_manager
  ${EndIf}
  System::Alloc 36
  Pop $StopBuffer
  ${If} $StopBuffer == 0
    Goto stop_close_service
  ${EndIf}
  ; ControlService is asynchronous; errors such as already-stopped are resolved
  ; by querying actual state, never treated as proof of successful shutdown.
  System::Call 'advapi32::ControlService(p $StopService, i 1, p $StopBuffer) i .r0'
  StrCpy $StopCount 0
  stop_poll:
    System::Call 'advapi32::QueryServiceStatusEx(p $StopService, i 0, p $StopBuffer, i 36, *i .r0) i .r1'
    ${If} $1 == 0
      Goto stop_free
    ${EndIf}
    System::Call '*$StopBuffer(i, i .r0)'
    ${If} $0 == 1
      StrCpy $StopResult 0
      Goto stop_free
    ${EndIf}
    IntOp $StopCount $StopCount + 1
    ${If} $StopCount < 60
      Sleep 1000
      Goto stop_poll
    ${EndIf}
  stop_free:
    System::Free $StopBuffer
  stop_close_service:
    System::Call 'advapi32::CloseServiceHandle(p $StopService)'
  stop_close_manager:
    System::Call 'advapi32::CloseServiceHandle(p $StopManager)'
FunctionEnd

!macro RequireStopped
  Call VerifiedStop
  ${If} $StopResult != 0
    !insertmacro Diagnostic "Service STOPPED could not be confirmed; no further replacement"
    nsExec::ExecToLog '"$INSTDIR\nssm.exe" set ${SERVICE} AppExit Default Restart'
    MessageBox MB_ICONSTOP "Service shutdown was not confirmed. Upgrade stopped; program and recovery files were retained. Resolve STOP_PENDING or restart Windows before retrying."
    Abort
  ${EndIf}
!macroend
