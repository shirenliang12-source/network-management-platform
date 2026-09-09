# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Cisco Network Manager."""

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Project root
PROJ_ROOT = os.path.abspath('.')

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
    + collect_submodules('pyVmomi')
    + collect_submodules('pyVim')
    + [
        # Explicit hidden imports
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
    ]
)

# Data files: templates and static assets
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
        # Exclude unnecessary packages to reduce size
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CiscoNetworkManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CiscoNetworkManager',
)
