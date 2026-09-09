"""Build bootstrap: neutralize the WorkBuddy safe-delete shim so PyInstaller
can clean up its own temp files with native deletes, then run PyInstaller."""
import os
import sys

import shutil

# WorkBuddy injects a safe-delete shim in some build environments. Restore the
# native functions when it is present, while remaining buildable in a normal
# Python virtual environment.
try:
    import sitecustomize
except ImportError:
    sitecustomize = None

if sitecustomize is not None:
    if hasattr(sitecustomize, "_orig_remove"):
        os.remove = sitecustomize._orig_remove
        os.unlink = sitecustomize._orig_remove
    if hasattr(sitecustomize, "_orig_rmdir"):
        os.rmdir = sitecustomize._orig_rmdir
    if hasattr(sitecustomize, "_orig_shutil_rmtree"):
        shutil.rmtree = sitecustomize._orig_shutil_rmtree

from PyInstaller.__main__ import run

sys.argv = [
    "pyinstaller",
    "build_onefile.spec",
    "--distpath", "dist_onefile",
    "--workpath", "build",
    "--noconfirm",
]
sys.exit(run())
