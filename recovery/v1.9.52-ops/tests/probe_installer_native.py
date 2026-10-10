"""Read-only native API probe; does not install, create, or stop a real service."""
from pathlib import Path
import os
import subprocess
import uuid

root=Path(__file__).resolve().parents[1]
directory=root/'_installer_probe'
directory.mkdir(exist_ok=True)
template=(root/'installer/install.nsi').read_text(encoding='utf-8')
helper=template[template.index('Var StopResult'):template.index('Function .onInit')]
service='CNM_ReadOnlyProbe_'+uuid.uuid4().hex
# Assert the generated name has no service before invoking the missing-service branch.
query=subprocess.run(['sc.exe','query',service],capture_output=True)
if query.returncode!=1060:
    raise RuntimeError('Probe requires an absent randomly named service')
script='''!include "LogicLib.nsh"
!define SERVICE "__SERVICE__"
Name "Read-only SCM probe"
OutFile "probe.exe"
RequestExecutionLevel user
SilentInstall silent
__HELPER__
Section
  SetAutoClose true
  Call StopPlatformService
  FileOpen $0 "$EXEDIR\\probe.txt" w
  FileWrite $0 "result=$StopResult scm=$StopScm service=$StopService state=$StopState"
  FileClose $0
  ${If} $StopResult != 0
    SetErrorLevel 40
    Quit
  ${EndIf}
  System::Call 'kernel32::GetCurrentProcess() p.r7'
  StrCpy $8 ${NSIS_MAX_STRLEN}
  System::Call 'kernel32::QueryFullProcessImageName(p r7, i 0, t .r9, *i r8) i.r0'
  ${If} $0 != 1
    SetErrorLevel 41
    Quit
  ${EndIf}
  ${If} $9 != $EXEPATH
    SetErrorLevel 42
    Quit
  ${EndIf}
  SetErrorLevel 0
SectionEnd
'''.replace('__SERVICE__',service).replace('__HELPER__',helper)
(directory/'probe.nsi').write_text(script,encoding='utf-8')
compiler=root.parents[1]/'_installer_cache/nsis312/makensis.exe'
environment=dict(os.environ,NSISDIR=str(compiler.parent))
subprocess.run([str(compiler),'/V2','probe.nsi'],cwd=directory,env=environment,check=True)
result=subprocess.run([str(directory/'probe.exe'),'/S'],cwd=directory,timeout=30,
                      creationflags=subprocess.CREATE_NO_WINDOW)
if result.returncode:
    raise RuntimeError(f'Native installer API probe failed: {result.returncode}: '+(directory/'probe.txt').read_text())
print('PASS native SCM missing-service handling and exact process-image query; no real service changed')
