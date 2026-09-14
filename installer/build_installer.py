#!/usr/bin/env python3
"""Build the Windows installer for Cisco Network Manager.

Output:  dist_onefile/CiscoNetworkManager-v<VER>-setup.exe

Strategy
--------
* If `makensis` (NSIS) is available on the machine, build a REAL NSIS installer
  (native UAC elevation, wizard, Add/Remove-Programs entry, reliable service
  registration). This is the preferred output.
* Otherwise fall back to a 7z self-extracting EXE that copies its payload to a
  PERSISTENT folder and then elevates + installs from there (robust against the
  classic 7z temp-folder-delete race that makes "it just extracted, no install").

Run from project root:  python installer/build_installer.py
"""
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import hashlib
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEVENZIP = r"C:\Program Files\7-Zip\7z.exe"
SFX = r"C:\Program Files\7-Zip\7z.sfx"
DIST = os.path.join(ROOT, "dist_onefile")
INSTALLER = os.path.join(ROOT, "installer")
CACHE = os.path.join(ROOT, "_installer_cache")
BUILD = os.path.join(ROOT, "_installer_build")
NSSM_VERSION = "nssm-2.24-101-g897c7ad"
NSSM_URL = f"https://www.nssm.cc/ci/{NSSM_VERSION}.zip"
NSSM_SHA1 = "ca2f6782a05af85facf9b620e047b01271edd11d"

_MAKENSIS_CANDIDATES = [
    r"C:\Program Files\NSIS\makensis.exe",
    r"C:\Program Files (x86)\NSIS\makensis.exe",
]
for _v in (os.environ.get("NSIS_HOME"), os.environ.get("NsisDir")):
    if _v:
        _MAKENSIS_CANDIDATES.append(os.path.join(_v, "makensis.exe"))


def find_makensis():
    # Prefer the current official compiler cached alongside the build, while
    # retaining compatibility with the historical NSIS 2.51 NuGet layout.
    _cache_nsis3 = os.path.join(CACHE, "nsis312", "makensis.exe")
    _cache_nsis = os.path.join(CACHE, "nsis251", "tools", "makensis.exe")
    if os.path.exists(_cache_nsis3):
        _MAKENSIS_CANDIDATES.append(_cache_nsis3)
    if os.path.exists(_cache_nsis):
        _MAKENSIS_CANDIDATES.append(_cache_nsis)
    for c in _MAKENSIS_CANDIDATES:
        if os.path.exists(c):
            return c
    try:
        out = subprocess.run(["where", "makensis"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


_cfg = open(os.path.join(ROOT, "app", "config.py"), encoding="utf-8").read()
_m = re.search(r'APP_VERSION:\s*str\s*=\s*"([\d.]+)"', _cfg)
VER = _m.group(1) if _m else "0.0.0"

# Prefer the newest onefile exe: PyInstaller writes to dist/ by default,
# while older builds landed in dist_onefile/. Using a stale exe here silently
# ships an old version in the installer.
EXE_CANDIDATES = [
    os.path.join(ROOT, "dist", "CiscoNetworkManager.exe"),
    os.path.join(DIST, "CiscoNetworkManager.exe"),
]
EXE_CANDIDATES = [p for p in EXE_CANDIDATES if os.path.isfile(p)]
if not EXE_CANDIDATES:
    raise SystemExit("ERROR: no CiscoNetworkManager.exe found in dist/ or dist_onefile/")
EXE_SRC = max(EXE_CANDIDATES, key=os.path.getmtime)
print(f"packaging exe: {EXE_SRC} (mtime {os.path.getmtime(EXE_SRC)})")
OUT = os.path.join(DIST, f"CiscoNetworkManager-v{VER}-setup.exe")
PAYLOAD = os.path.join(BUILD, "payload")
NSI_TMPL = os.path.join(INSTALLER, "install.nsi")
NSI_RENDERED = os.path.join(BUILD, "install.nsi")
ARCHIVE = os.path.join(BUILD, "app.7z")
CONFIG = os.path.join(BUILD, "config.txt")


def run(cmd, cwd=None, check=True):
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, check=check)
    return r.returncode


def ensure_nssm():
    nssm = os.path.join(CACHE, "nssm.exe")
    if os.path.exists(nssm):
        print("nssm present:", nssm)
        return nssm
    os.makedirs(CACHE, exist_ok=True)
    zip_path = os.path.join(CACHE, "nssm.zip")
    print("downloading nssm ->", zip_path)
    urllib.request.urlretrieve(NSSM_URL, zip_path)
    with open(zip_path, "rb") as archive_file:
        actual_sha1 = hashlib.sha1(archive_file.read()).hexdigest()
    if actual_sha1.lower() != NSSM_SHA1:
        os.remove(zip_path)
        sys.exit(f"nssm checksum mismatch: {actual_sha1}")
    member = f"{NSSM_VERSION}/win64/nssm.exe"
    with zipfile.ZipFile(zip_path) as archive:
        archive.extract(member, CACHE)
    src = os.path.join(CACHE, NSSM_VERSION, "win64", "nssm.exe")
    os.replace(src, nssm)
    shutil.rmtree(os.path.join(CACHE, NSSM_VERSION), ignore_errors=True)
    os.remove(zip_path)
    return nssm


def assemble_payload(nssm):
    if os.path.isdir(PAYLOAD):
        shutil.rmtree(PAYLOAD, ignore_errors=True)
    os.makedirs(os.path.join(PAYLOAD, "data", "backups"), exist_ok=True)
    os.makedirs(os.path.join(PAYLOAD, "data", "exports"), exist_ok=True)

    # Copy stable project sources under ASCII payload names. Older build flows
    # generated Chinese-named temporary files in dist_onefile; relying on them
    # could silently package stale documentation or fail on a clean checkout.
    top_map = {
        EXE_SRC: "CiscoNetworkManager.exe",
        os.path.join(ROOT, "README.md"): "README.txt",
        os.path.join(ROOT, "sample_devices.csv"): "sample_devices.csv",
        os.path.join(INSTALLER, "start.bat"): "start.bat",
    }
    for s, dst in top_map.items():
        if not os.path.exists(s):
            sys.exit(f"missing {s} (build the app first: python build_bootstrap.py)")
        shutil.copy2(s, os.path.join(PAYLOAD, dst))
    shutil.copy2(nssm, os.path.join(PAYLOAD, "nssm.exe"))
    with open(os.path.join(PAYLOAD, "version.txt"), "w", encoding="ascii") as version_file:
        version_file.write(VER + "\n")
    # commands.json is created by the application only when it is missing.
    # Never ship it as an installer payload: extracting a seed file during an
    # upgrade would overwrite operator-defined commands and device-type labels.
    for sub in ("backups", "exports"):
        open(os.path.join(PAYLOAD, "data", sub, ".gitkeep"), "w", encoding="utf-8").close()
    # installer scripts (used by the 7z fallback path)
    for f in ("install.cmd", "install.ps1", "uninstall.ps1"):
        shutil.copy2(os.path.join(INSTALLER, f), os.path.join(PAYLOAD, f))
    print("payload assembled:", PAYLOAD)


def build_nsis():
    makensis = find_makensis()
    if not makensis:
        return False
    txt = open(NSI_TMPL, encoding="utf-8").read()
    # inject the real version from app/config.py (regex so future bumps work)
    txt = re.sub(r'!define VER "[\d.]+"', f'!define VER "{VER}"', txt)
    # Compile to an ASCII relative filename. Some NSIS builds cannot open an
    # absolute OutFile containing non-ASCII path components; move it to the
    # final dist directory only after compilation succeeds.
    staged_name = f"CiscoNetworkManager-v{VER}-setup.exe"
    staged_out = os.path.join(BUILD, staged_name)
    txt = txt.replace('OutFile "dist_onefile\\CiscoNetworkManager-v${VER}-setup.exe"',
                      f'OutFile "{staged_name}"')
    os.makedirs(BUILD, exist_ok=True)
    with open(NSI_RENDERED, "w", encoding="utf-8") as f:
        f.write(txt)
    if os.path.exists(OUT):
        os.remove(OUT)
    if os.path.exists(staged_out):
        os.remove(staged_out)
    # NSISDIR must point at the toolkit root so ${NSISDIR} includes/plugins resolve
    env = dict(os.environ)
    env["NSISDIR"] = os.path.dirname(os.path.abspath(makensis))
    print("+ NSISDIR =", env["NSISDIR"])
    r = subprocess.run([makensis, "/V2", NSI_RENDERED], cwd=BUILD, env=env)
    if r.returncode != 0:
        raise SystemExit(f"NSIS compilation failed with exit code {r.returncode}")
    os.replace(staged_out, OUT)
    print(f"BUILT (NSIS) {OUT}  ({os.path.getsize(OUT):,} bytes)")
    return True


def build_7z_sfx():
    if not os.path.exists(SEVENZIP):
        sys.exit(f"7-Zip not found: {SEVENZIP}")
    if not os.path.exists(SFX):
        sys.exit(f"7z SFX module not found: {SFX}")
    if os.path.exists(ARCHIVE):
        os.remove(ARCHIVE)
    run([SEVENZIP, "a", "-y", "-t7z", "-mx=7", "-mmt=on", ARCHIVE, "."], cwd=PAYLOAD)

    cfg = (
        ';!@Install@!UTF-8!\n'
        f'Title="Cisco Network Manager v{VER} 安装程序"\n'
        f'BeginPrompt="即将安装 Cisco Network Manager v{VER} 并注册为 Windows 服务'
        f'（开机自启、放行防火墙 9632 端口）。继续？"\n'
        'RunProgram="install.cmd"\n'
        ';!@InstallEnd@!\n'
    )
    with open(CONFIG, "w", encoding="utf-8", newline="") as f:
        f.write(cfg)

    if os.path.exists(OUT):
        os.remove(OUT)
    with open(OUT, "wb") as o:
        for p in (SFX, CONFIG, ARCHIVE):
            with open(p, "rb") as i:
                o.write(i.read())
    print(f"BUILT (7z SFX) {OUT}  ({os.path.getsize(OUT):,} bytes)")


def build():
    if not os.path.exists(EXE_SRC):
        sys.exit(f"app exe missing: {EXE_SRC} (run python build_bootstrap.py first)")

    nssm = ensure_nssm()
    assemble_payload(nssm)

    if build_nsis():
        return
    print("makensis not found -> falling back to 7z self-extracting installer")
    build_7z_sfx()


if __name__ == "__main__":
    build()
