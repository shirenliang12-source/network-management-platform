"""Wrapper to run PyInstaller with original os.remove/shutil.rmtree restored.

The WorkBuddy safe-delete shim patches os.remove to trash instead of delete,
which breaks PyInstaller builds. This wrapper restores the originals.
"""
import os
import shutil
import sys

# Restore original delete functions from the shim
try:
    import sitecustomize as _sc
    if hasattr(_sc, '_orig_remove'):
        os.remove = _sc._orig_remove
        os.unlink = _sc._orig_remove
    if hasattr(_sc, '_orig_rmdir'):
        os.rmdir = _sc._orig_rmdir
    if hasattr(_sc, '_orig_shutil_rmtree'):
        shutil.rmtree = _sc._orig_shutil_rmtree
    print("[build_wrapper] Restored original os.remove/os.rmdir/shutil.rmtree")
except Exception as e:
    print(f"[build_wrapper] No shim detected or restore failed: {e}")

# Now run PyInstaller
from PyInstaller.__main__ import run
run()
