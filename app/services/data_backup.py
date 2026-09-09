"""Application data backup service.

Distinct from ``backup_service`` (which snapshots *device* running-configs over
SSH). This snapshots the **application's own data** — the SQLite database and
the on-disk user configuration files — so a bad upgrade/iteration can never
silently destroy the operator's setup.

A one-click UI trigger lives on the Backups page ("应用数据备份（升级/迭代前防护）").
"""
import os
import functools
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import threading
import zipfile
import io
import tempfile
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

# Where app-data snapshots live (separate from device-config .cfg files and
# from the auto startup db_backups snapshot dir).
DATA_BACKUP_DIR = os.path.join(settings.BACKUP_DIR, "data_backups")

# On-disk files (outside the DB) that belong to the operator's configuration
# and must be preserved alongside the database.
CONFIG_FILES = ["commands.json"]
SNAPSHOT_NAME_RE = re.compile(r"^\d{8}-\d{6}(?:-\d{6})?$")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 20
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_ALLOWED_ARCHIVE_FILES = {"netmgr.db", "commands.json", "manifest.json"}
_DATA_LOCK = threading.RLock()


def _secret_key_fingerprint() -> str:
    """Non-secret identifier used to reject restores with the wrong key."""
    return hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).hexdigest()


def _serialized(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _DATA_LOCK:
            return func(*args, **kwargs)
    return wrapper


def _snapshot_name(ts: datetime = None) -> str:
    return (ts or datetime.now()).strftime("%Y%m%d-%H%M%S-%f")


def _snapshot_database(source_path: str, destination_path: str) -> None:
    """Create a transactionally consistent SQLite snapshot."""
    source = sqlite3.connect(source_path, timeout=30)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


@_serialized
def create_data_backup() -> dict:
    """Take a point-in-time snapshot of the application data.

    Copies the SQLite DB (db + -wal + -shm so the snapshot is consistent at
    rest) plus the on-disk config files into a timestamped folder under
    ``data/backups/data_backups/<ts>/`` and writes a ``manifest.json``.

    Returns a record dict describing the backup.
    """
    try:
        db_path = settings.DATABASE_URL.replace("sqlite:///", "", 1)
        os.makedirs(DATA_BACKUP_DIR, exist_ok=True)
        ts = _snapshot_name()
        dest = os.path.join(DATA_BACKUP_DIR, ts)
        os.makedirs(dest, exist_ok=True)

        saved = []
        # --- database ---
        if os.path.exists(db_path):
            _snapshot_database(db_path, os.path.join(dest, "netmgr.db"))
            saved.append("netmgr.db")
        else:
            logger.warning("DB not found at %s; snapshot will contain config only", db_path)

        # --- on-disk config files ---
        data_root = os.path.dirname(db_path)
        for fname in CONFIG_FILES:
            src = os.path.join(data_root, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dest, fname))
                saved.append(fname)

        manifest = {
            "name": ts,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "app_version": settings.APP_VERSION,
            "files": saved,
            "db_path": db_path,
            "secret_key_fingerprint": _secret_key_fingerprint(),
        }
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        saved.append("manifest.json")

        logger.info("App data backup created: %s (%d files)", dest, len(saved))
        return {
            "ok": True,
            "name": ts,
            "path": dest,
            "app_version": settings.APP_VERSION,
            "created_at": manifest["created_at"],
            "files": saved,
            "size": sum(
                os.path.getsize(os.path.join(dest, f))
                for f in saved if os.path.exists(os.path.join(dest, f))
            ),
        }
    except Exception as e:
        logger.error("App data backup failed: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}


def list_data_backups(limit: int = 50) -> list:
    """List existing app-data snapshots, newest first."""
    if not os.path.isdir(DATA_BACKUP_DIR):
        return []
    rows = []
    for name in sorted(os.listdir(DATA_BACKUP_DIR), reverse=True):
        if not SNAPSHOT_NAME_RE.fullmatch(name):
            continue
        folder = os.path.join(DATA_BACKUP_DIR, name)
        if not os.path.isdir(folder):
            continue
        manifest_path = os.path.join(folder, "manifest.json")
        rec = {
            "name": name,
            "created_at": "",
            "app_version": "",
            "files": [],
            "size": 0,
        }
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    m = json.load(f)
                rec["created_at"] = m.get("created_at", "")
                rec["app_version"] = m.get("app_version", "")
                rec["files"] = m.get("files", [])
            except Exception:
                pass
        rec["size"] = sum(
            os.path.getsize(os.path.join(folder, f))
            for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
        )
        rows.append(rec)
        if len(rows) >= limit:
            break
    return rows


def get_data_backup_zip(name: str) -> bytes:
    """Return a zip archive (bytes) of a snapshot folder.

    Raises ``FileNotFoundError`` if the snapshot does not exist.
    """
    if not SNAPSHOT_NAME_RE.fullmatch(name or ""):
        raise FileNotFoundError("invalid backup name")
    root = os.path.realpath(DATA_BACKUP_DIR)
    folder = os.path.realpath(os.path.join(root, name))
    if os.path.commonpath((root, folder)) != root:
        raise FileNotFoundError("invalid backup path")
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"backup {name} not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, arcname=os.path.join(name, fname))
    return buf.getvalue()


def _extract_validated_archive(zip_bytes: bytes, target_dir: str) -> dict:
    """Extract only expected regular files after enforcing resource limits."""
    if len(zip_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("备份包超过 256 MB 限制")

    extracted = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        if len(files) > MAX_ARCHIVE_FILES:
            raise ValueError("备份包文件数量过多")
        total_size = sum(item.file_size for item in files)
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("备份包解压后超过 1 GB 限制")

        for item in files:
            normalized = item.filename.replace("\\", "/")
            parts = [part for part in normalized.split("/") if part]
            if not parts or normalized.startswith("/") or ".." in parts:
                raise ValueError("备份包包含不安全路径")
            if item.flag_bits & 0x1:
                raise ValueError("不支持加密 ZIP")
            basename = parts[-1]
            if basename not in _ALLOWED_ARCHIVE_FILES:
                raise ValueError(f"备份包包含不允许的文件: {basename}")
            if basename in extracted:
                raise ValueError(f"备份包包含重复文件: {basename}")
            if item.compress_size and item.file_size > item.compress_size * 200:
                raise ValueError(f"备份包压缩比异常: {basename}")

            destination = os.path.join(target_dir, basename)
            with archive.open(item) as source, open(destination, "wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted[basename] = destination
    return extracted


def _is_sqlite_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return False
        connection = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
        try:
            check = connection.execute("PRAGMA quick_check").fetchone()
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            return bool(check and check[0] == "ok" and "devices" in tables)
        finally:
            connection.close()
    except Exception:
        return False


@_serialized
def restore_data_backup(zip_bytes: bytes) -> dict:
    """Restore application data from an uploaded backup zip.

    Steps:
      1. Validate the zip (must contain a SQLite ``netmgr.db``; ``commands.json``
         and ``manifest.json`` are optional but expected).
      2. Take a **pre-restore safety snapshot** of the current data so the
         operation is reversible.
      3. Dispose the live SQLAlchemy engine (single-process app) to release the
         SQLite file locks, then swap ``netmgr.db`` (+ ``-wal``/``-shm``) and
         ``commands.json`` with the uploaded copies. The next request reopens
         the restored database.

    Returns a result dict; on failure sets ``ok=False`` with an ``error``.
    """
    tmp = None
    staged_paths = []
    try:
        if not zip_bytes:
            return {"ok": False, "error": "空文件"}

        if len(zip_bytes) > MAX_ARCHIVE_BYTES:
            return {"ok": False, "error": "备份包超过 256 MB 限制"}

        # 1) extract + validate
        tmp = tempfile.mkdtemp(prefix="cnm_restore_")
        extracted = _extract_validated_archive(zip_bytes, tmp)

        src_db = extracted.get("netmgr.db")
        if not src_db or not _is_sqlite_file(src_db):
            return {"ok": False, "error": "备份包中未找到有效的 netmgr.db（SQLite 数据库）"}

        manifest = {}
        mpath = extracted.get("manifest.json")
        if mpath:
            try:
                with open(mpath, encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, ValueError, TypeError):
                return {"ok": False, "error": "备份包 manifest.json 无效"}
        backup_key = manifest.get("secret_key_fingerprint")
        if backup_key and backup_key != _secret_key_fingerprint():
            return {
                "ok": False,
                "error": "备份属于另一安装密钥；请安全迁移原 data/.secret_key 后再恢复",
            }

        db_path = settings.DATABASE_URL.replace("sqlite:///", "", 1)
        data_root = os.path.dirname(db_path)

        # 2) safety snapshot (captures current live data for rollback)
        pre = create_data_backup()
        pre_name = pre.get("name") if pre.get("ok") else None

        # 3) swap files
        if not pre.get("ok"):
            return {"ok": False, "error": "无法创建恢复前安全快照，已取消恢复"}

        src_cmd = extracted.get("commands.json")
        staged_db = os.path.join(data_root, ".netmgr.restore-staged.db")
        shutil.copy2(src_db, staged_db)
        staged_paths.append(staged_db)
        staged_cmd = None
        if src_cmd:
            staged_cmd = os.path.join(data_root, ".commands.restore-staged.json")
            shutil.copy2(src_cmd, staged_cmd)
            staged_paths.append(staged_cmd)

        # Release SQLite locks held by the running engine, then replace db files.
        from app.database import engine
        engine.dispose()

        previous_db = db_path + ".restore-previous"
        command_path = os.path.join(data_root, "commands.json")
        previous_cmd = command_path + ".restore-previous"
        for stale in (previous_db, previous_cmd):
            if os.path.exists(stale):
                os.remove(stale)

        try:
            if os.path.exists(db_path):
                os.replace(db_path, previous_db)
            for ext in ("-wal", "-shm"):
                companion = db_path + ext
                if os.path.exists(companion):
                    os.remove(companion)
            os.replace(staged_db, db_path)

            if staged_cmd:
                if os.path.exists(command_path):
                    os.replace(command_path, previous_cmd)
                os.replace(staged_cmd, command_path)
        except Exception:
            if os.path.exists(previous_db):
                if os.path.exists(db_path):
                    os.remove(db_path)
                os.replace(previous_db, db_path)
            if os.path.exists(previous_cmd):
                if os.path.exists(command_path):
                    os.remove(command_path)
                os.replace(previous_cmd, command_path)
            raise
        else:
            for old in (previous_db, previous_cmd):
                if os.path.exists(old):
                    os.remove(old)

        # restored version (from manifest if present)
        restored_version = manifest.get("app_version", "")

        logger.info("App data restored from uploaded backup (pre-restore snapshot: %s)", pre_name)
        return {
            "ok": True,
            "restored_version": restored_version,
            "pre_restore_backup": pre_name,
            "restart_required": True,
            "message": "应用数据已恢复，请立即重启服务以加载新数据",
        }
    except Exception as e:
        logger.error("App data restore failed: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        for staged in staged_paths:
            try:
                if os.path.exists(staged):
                    os.remove(staged)
            except OSError:
                pass
        if tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
