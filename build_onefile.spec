# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Cisco Network Manager - OneFile mode (v1.2.0).

v1.2.0: Added NIC selection, backup thread-safety fix, configurable commands.
"""

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None


def runtime_modules(name):
    """Exclude package test suites from the frozen runtime."""
    return not any(part in {"tests", "testing"} for part in name.split("."))

# Project root
PROJ_ROOT = os.path.abspath('.')

# Collect all submodules for packages with dynamic imports
hiddenimports = (
    collect_submodules('uvicorn', filter=runtime_modules)
    + collect_submodules('netmiko', filter=runtime_modules)
    + collect_submodules('apscheduler', filter=runtime_modules)
    + collect_submodules('sqlalchemy', filter=runtime_modules)
    + collect_submodules('alembic', filter=runtime_modules)
    + collect_submodules('starlette', filter=runtime_modules)
    + collect_submodules('fastapi', filter=runtime_modules)
    + collect_submodules('pydantic', filter=runtime_modules)
    + collect_submodules('pydantic_settings', filter=runtime_modules)
    + collect_submodules('jinja2', filter=runtime_modules)
    + collect_submodules('networkx', filter=runtime_modules)
    + collect_submodules('ldap3', filter=runtime_modules)
    + collect_submodules('pyasn1', filter=runtime_modules)
    + collect_submodules('pyVmomi', filter=runtime_modules)
    + collect_submodules('pyVim', filter=runtime_modules)
    + [
        'uvicorn.logging',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'netmiko.cisco',
        'netmiko.cisco_base_connection',
        'netmiko.cisco.cisco_ios',
        'netmiko.cisco.cisco_nxos',
        'email.mime.text',
        'email.mime.multipart',
        'anyio._backends._asyncio',
        'h11',
        'httptools',
        'websockets',
        'watchfiles',
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

# Data files: templates and static assets bundled inside the exe
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
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# OneFile mode: single self-contained exe
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
    icon=None,
)
