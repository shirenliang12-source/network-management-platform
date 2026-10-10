# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Cisco Network Manager - OneFile mode (Linux).

Mirrors build_onefile.spec but:
  * removes Windows-only Analysis kwargs (win_no_prefer_redirects / win_private_assemblies)
  * makes psutil hiddenimports platform-conditional (Linux uses _pslinux)
  * emits a binary named `CiscoNetworkManager` (no .exe extension)
"""

import os
import platform
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Project root (run pyinstaller from the project root so this resolves correctly)
PROJ_ROOT = os.path.abspath('.')

# psutil platform-specific submodules
if platform.system() == "Windows":
    psutil_platform = ['psutil._pswindows', 'psutil._psutil_windows']
else:
    psutil_platform = ['psutil._pslinux', 'psutil._psutil_linux']

# Collect all submodules for packages with dynamic imports
hiddenimports = (
    collect_submodules('uvicorn')
    + collect_submodules('netmiko')
    + collect_submodules('apscheduler')
    + collect_submodules('sqlalchemy')
    + collect_submodules('alembic')
    + collect_submodules('starlette')
    + collect_submodules('fastapi')
    + collect_submodules('pydantic')
    + collect_submodules('pydantic_settings')
    + collect_submodules('jinja2')
    + collect_submodules('networkx')
    + collect_submodules('psutil')
    + collect_submodules('ldap3')
    + collect_submodules('pyasn1')
    + collect_submodules('pyVmomi')
    + collect_submodules('pyVim')
    + [
        'uvicorn.logging',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'netmiko.cisco',
        'netmiko.cisco_base_connection',
        'netmiko.cisco.cisco_ios',
        'netmiko.cisco.cisco_xe',
        'netmiko.cisco.cisco_nxos',
        'email.mime.text',
        'email.mime.multipart',
        'anyio._backends._asyncio',
        'h11',
        'httptools',
        'websockets',
        'watchfiles',
        'psutil',
    ]
    + psutil_platform
    + [
        'app.routers.commands',
        'app.routers.nics',
        'app.routers.credentials',
        'app.routers.assets',
        'app.routers.accounts',
        'app.routers.ad',
        'app.services.command_config',
        'app.services.nic_service',
        'app.services.ad_service',
    ]
)

# Data files: templates and static assets bundled inside the binary
datas = [
    (os.path.join(PROJ_ROOT, 'app', 'templates'), os.path.join('app', 'templates')),
    (os.path.join(PROJ_ROOT, 'app', 'static'), os.path.join('app', 'static')),
    (os.path.join(PROJ_ROOT, 'migrations'), 'migrations'),
    (os.path.join(PROJ_ROOT, 'alembic.ini'), '.'),
    (os.path.join(PROJ_ROOT, 'sample_devices.csv'), '.'),
]

a = Analysis(
    ['run.py'],
    pathex=[PROJ_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'PIL',
        'PyQt5',
        'PyQt6',
        'cv2',
        'numpy',
        'pandas',
        'scipy',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# OneFile mode: single self-contained binary (no .exe suffix on Linux)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CiscoNetworkManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Disable UPX to avoid false positive antivirus detection
    console=True,  # Keep console so errors are visible
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
