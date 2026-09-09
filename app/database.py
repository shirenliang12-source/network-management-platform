"""Database initialization and session management."""
import logging
import os
import glob
import sqlite3
import json
import shutil
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, text, inspect, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import MIGRATIONS_DIR, settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, conn_record):
    """Apply SQLite durability and relationship guarantees on every connection."""
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception as exc:
        logger.warning("Unable to apply one or more SQLite pragmas: %s", exc)
    finally:
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

UPGRADE_STATUS_FILE = Path(settings.BACKUP_DIR) / "upgrade_status.json"


def _database_path() -> Path | None:
    prefix = "sqlite:///"
    if not settings.DATABASE_URL.startswith(prefix):
        return None
    return Path(settings.DATABASE_URL[len(prefix):]).resolve()


def _write_upgrade_status(payload: dict) -> None:
    """Persist migration state atomically so startup failures remain visible."""
    UPGRADE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = UPGRADE_STATUS_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, UPGRADE_STATUS_FILE)


def _schema_revision() -> str:
    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        return "未迁移"
    with engine.connect() as connection:
        return str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar() or "未知")


def check_database_integrity(*, full: bool = False) -> dict:
    """Run read-only SQLite consistency and relationship checks."""
    db_path = _database_path()
    if db_path is None:
        return {"ok": True, "relations_ok": True, "engine": "external", "integrity": "not_applicable", "foreign_key_violations": []}
    if not db_path.exists():
        return {"ok": True, "relations_ok": True, "engine": "sqlite", "integrity": "new_database", "foreign_key_violations": []}
    pragma = "integrity_check" if full else "quick_check"
    with engine.connect() as connection:
        rows = [str(row[0]) for row in connection.exec_driver_sql(f"PRAGMA {pragma}").all()]
        foreign_keys = [list(row) for row in connection.exec_driver_sql("PRAGMA foreign_key_check").fetchmany(100)]
        enabled = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar())
    integrity_ok = rows == ["ok"]
    return {
        # Orphaned rows from old releases are reported for repair but must not
        # make an otherwise readable legacy database impossible to upgrade.
        "ok": integrity_ok,
        "relations_ok": not foreign_keys,
        "engine": "sqlite",
        "integrity": "ok" if integrity_ok else "; ".join(rows[:20]),
        "foreign_keys_enabled": enabled,
        "foreign_key_violations": foreign_keys,
    }


def get_upgrade_status() -> dict:
    try:
        value = json.loads(UPGRADE_STATUS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


def get_database_health(*, full: bool = False) -> dict:
    result = check_database_integrity(full=full)
    db_path = _database_path()
    if db_path is not None:
        usage = shutil.disk_usage(db_path.parent)
        result.update({
            "database_size": db_path.stat().st_size if db_path.exists() else 0,
            "disk_free": usage.free,
            "schema_revision": _schema_revision(),
            "upgrade": get_upgrade_status(),
        })
    return result


def get_db():
    """FastAPI dependency to get DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_database():
    """Add missing columns to existing tables (SQLite ALTER TABLE).
    
    SQLAlchemy's create_all() only creates new tables; it does NOT add
    columns to existing tables. This function checks each table for
    expected columns and adds any that are missing via ALTER TABLE.
    """
    inspector = inspect(engine)
    
    # Expected columns for the devices table
    devices_expected = {
        "source_ip": "VARCHAR(45) DEFAULT ''",
        "company": "VARCHAR(100) DEFAULT ''",
        "model": "VARCHAR(100) DEFAULT ''",
        "function": "VARCHAR(100) DEFAULT ''",
        "credential_profile_id": "INTEGER",
        "production_date_manual": "VARCHAR(50) DEFAULT ''",
    }

    # Expected columns for the device_info table
    device_info_expected = {
        "cpu_usage": "VARCHAR(50) DEFAULT ''",
        "memory_usage": "VARCHAR(50) DEFAULT ''",
        "interface_up_count": "INTEGER DEFAULT 0",
        "interface_down_count": "INTEGER DEFAULT 0",
        # v1.9.35: production-date provenance (so the detail page can
        # distinguish "(自动)" vs "(手动)" labels)
        "production_date_source": "VARCHAR(20) DEFAULT ''",
        "production_date_pattern": "VARCHAR(50) DEFAULT ''",
        "production_date_raw_match": "VARCHAR(500) DEFAULT ''",
    }

    if inspector.has_table("devices"):
        existing_cols = {col["name"] for col in inspector.get_columns("devices")}
        with engine.connect() as conn:
            for col_name, col_def in devices_expected.items():
                if col_name not in existing_cols:
                    logger.info(f"Migrating: adding column '{col_name}' to devices table")
                    conn.execute(text(f"ALTER TABLE devices ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    logger.info(f"Migration: column '{col_name}' added successfully")

    if inspector.has_table("device_info"):
        existing_cols = {col["name"] for col in inspector.get_columns("device_info")}
        with engine.connect() as conn:
            for col_name, col_def in device_info_expected.items():
                if col_name not in existing_cols:
                    logger.info(f"Migrating: adding column '{col_name}' to device_info table")
                    conn.execute(text(f"ALTER TABLE device_info ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    logger.info(f"Migration: column '{col_name}' added successfully")

    # Expected columns for the schedule_configs table
    schedule_expected = {
        "last_run_status": "VARCHAR(20)",
    }

    if inspector.has_table("schedule_configs"):
        existing_cols = {col["name"] for col in inspector.get_columns("schedule_configs")}
        with engine.connect() as conn:
            for col_name, col_def in schedule_expected.items():
                if col_name not in existing_cols:
                    logger.info(f"Migrating: adding column '{col_name}' to schedule_configs table")
                    conn.execute(text(f"ALTER TABLE schedule_configs ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    logger.info(f"Migration: column '{col_name}' added successfully")

    # Expected columns for the ip_inventory table
    ip_inventory_expected = {
        "sort_order": "INTEGER DEFAULT 0",
        "device_id": "INTEGER",
        "asset_sn": "VARCHAR(100) DEFAULT ''",
        "network_type": "VARCHAR(20) DEFAULT '有线'",
        "firewall": "VARCHAR(100) DEFAULT ''",
        "zone_interface_name": "VARCHAR(100) DEFAULT ''",
    }

    if inspector.has_table("ip_inventory"):
        existing_cols = {col["name"] for col in inspector.get_columns("ip_inventory")}
        with engine.connect() as conn:
            for col_name, col_def in ip_inventory_expected.items():
                if col_name not in existing_cols:
                    logger.info(f"Migrating: adding column '{col_name}' to ip_inventory table")
                    conn.execute(text(f"ALTER TABLE ip_inventory ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    logger.info(f"Migration: column '{col_name}' added successfully")

    # Expected columns for the ipam_aggregates table
    ipam_aggregates_expected = {
        "name": "VARCHAR(200) NOT NULL DEFAULT ''",
        "prefix": "VARCHAR(50) NOT NULL DEFAULT ''",
        "description": "TEXT DEFAULT ''",
        "date_added": "VARCHAR(20) DEFAULT ''",
    }

    # Expected columns for the ipam_prefixes table
    ipam_prefixes_expected = {
        "aggregate_id": "INTEGER",
        "parent_id": "INTEGER",
        "prefix": "VARCHAR(50) NOT NULL DEFAULT ''",
        "status": "VARCHAR(20) DEFAULT '规划'",
        "role": "VARCHAR(100) DEFAULT ''",
        "vlan": "VARCHAR(50) DEFAULT ''",
        "company": "VARCHAR(100) DEFAULT ''",
        "firewall": "VARCHAR(100) DEFAULT ''",
        "zone_interface_name": "VARCHAR(100) DEFAULT ''",
        "description": "TEXT DEFAULT ''",
        "is_pool": "BOOLEAN DEFAULT 0",
    }

    # Expected columns for the ipam_ip_addresses table
    ipam_ip_addresses_expected = {
        "prefix_id": "INTEGER NOT NULL DEFAULT 0",
        "address": "VARCHAR(45) NOT NULL DEFAULT ''",
        "status": "VARCHAR(20) DEFAULT '规划'",
        "allocation_type": "VARCHAR(10) DEFAULT '静态'",
        "dns_name": "VARCHAR(255) DEFAULT ''",
        "description": "TEXT DEFAULT ''",
        "assigned_device_id": "INTEGER",
        "device_name": "VARCHAR(200) DEFAULT ''",
        "device_model": "VARCHAR(100) DEFAULT ''",
        "device_ip": "VARCHAR(45) DEFAULT ''",
    }

    for table, expected in (
        ("ipam_aggregates", ipam_aggregates_expected),
        ("ipam_prefixes", ipam_prefixes_expected),
        ("ipam_ip_addresses", ipam_ip_addresses_expected),
    ):
        if inspector.has_table(table):
            existing_cols = {col["name"] for col in inspector.get_columns(table)}
            with engine.connect() as conn:
                for col_name, col_def in expected.items():
                    if col_name not in existing_cols:
                        logger.info(f"Migrating: adding column '{col_name}' to {table} table")
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        logger.info(f"Migration: column '{col_name}' added successfully")

    # Expected columns for the dc_sites table (数据中心站点)
    dc_sites_expected = {
        "name": "VARCHAR(150) NOT NULL DEFAULT ''",
        "company": "VARCHAR(100) DEFAULT ''",
        "region": "VARCHAR(100) DEFAULT ''",
        "address": "VARCHAR(300) DEFAULT ''",
        "contact_name": "VARCHAR(100) DEFAULT ''",
        "contact_phone": "VARCHAR(100) DEFAULT ''",
        "description": "TEXT DEFAULT ''",
    }

    # Expected columns for the dc_racks table (机柜)
    dc_racks_expected = {
        "site_id": "INTEGER NOT NULL DEFAULT 0",
        "rack_number": "VARCHAR(50) NOT NULL DEFAULT ''",
        "name": "VARCHAR(150) DEFAULT ''",
        "role": "VARCHAR(100) DEFAULT ''",
        "type": "VARCHAR(50) DEFAULT '机柜'",
        "width": "VARCHAR(20) DEFAULT '19英寸'",
        "u_height": "INTEGER DEFAULT 42",
        "status": "VARCHAR(20) DEFAULT '在用'",
        "serial": "VARCHAR(100) DEFAULT ''",
        "asset_tag": "VARCHAR(100) DEFAULT ''",
        "location_detail": "VARCHAR(200) DEFAULT ''",
        "description": "TEXT DEFAULT ''",
    }

    for table, expected in (
        ("dc_sites", dc_sites_expected),
        ("dc_racks", dc_racks_expected),
    ):
        if inspector.has_table(table):
            existing_cols = {col["name"] for col in inspector.get_columns(table)}
            with engine.connect() as conn:
                for col_name, col_def in expected.items():
                    if col_name not in existing_cols:
                        logger.info(f"Migrating: adding column '{col_name}' to {table} table")
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        logger.info(f"Migration: column '{col_name}' added successfully")


    # Expected columns for the it_assets table (其他 IT 资产)
    it_assets_expected = {
        "name": "VARCHAR(200) NOT NULL DEFAULT ''",
        "asset_type": "VARCHAR(50) DEFAULT '其他'",
        "brand": "VARCHAR(100) DEFAULT ''",
        "model": "VARCHAR(200) DEFAULT ''",
        "serial": "VARCHAR(100) DEFAULT ''",
        "asset_tag": "VARCHAR(100) DEFAULT ''",
        "management_ip": "VARCHAR(45) DEFAULT ''",
        "linked_device_id": "INTEGER",
        "site_id": "INTEGER",
        "location_detail": "VARCHAR(200) DEFAULT ''",
        "rack_id": "INTEGER",
        "rack_position": "VARCHAR(50) DEFAULT ''",
        "status": "VARCHAR(20) DEFAULT '在用'",
        "notes": "TEXT DEFAULT ''",
    }

    # Expected columns for the server_assets table (服务器 / 存储)
    server_assets_expected = {
        "name": "VARCHAR(200) NOT NULL DEFAULT ''",
        "category": "VARCHAR(20) DEFAULT '服务器'",
        "brand": "VARCHAR(100) DEFAULT ''",
        "model": "VARCHAR(200) DEFAULT ''",
        "serial": "VARCHAR(100) DEFAULT ''",
        "asset_tag": "VARCHAR(100) DEFAULT ''",
        "rack_id": "INTEGER NOT NULL DEFAULT 0",
        "u_start": "INTEGER DEFAULT 1",
        "u_size": "INTEGER DEFAULT 1",
        "status": "VARCHAR(20) DEFAULT '在用'",
        "management_ip": "VARCHAR(45) DEFAULT ''",
        "os": "VARCHAR(200) DEFAULT ''",
        "cpu": "VARCHAR(200) DEFAULT ''",
        "memory": "VARCHAR(200) DEFAULT ''",
        "storage_desc": "VARCHAR(300) DEFAULT ''",
        "owner": "VARCHAR(100) DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
    }

    for table, expected in (
        ("it_assets", it_assets_expected),
        ("server_assets", server_assets_expected),
    ):
        if inspector.has_table(table):
            existing_cols = {col["name"] for col in inspector.get_columns(table)}
            with engine.connect() as conn:
                for col_name, col_def in expected.items():
                    if col_name not in existing_cols:
                        logger.info(f"Migrating: adding column '{col_name}' to {table} table")
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        logger.info(f"Migration: column '{col_name}' added successfully")

    # Expected columns for the vm_instances table (虚拟机清单)
    vm_instances_expected = {
        "disk_count": "INTEGER DEFAULT 1",
        "storage_lun": "VARCHAR(500) DEFAULT ''",
        "disks": "TEXT DEFAULT ''",
    }

    if inspector.has_table("vm_instances"):
        existing_cols = {col["name"] for col in inspector.get_columns("vm_instances")}
        with engine.connect() as conn:
            for col_name, col_def in vm_instances_expected.items():
                if col_name not in existing_cols:
                    logger.info(f"Migrating: adding column '{col_name}' to vm_instances table")
                    conn.execute(text(f"ALTER TABLE vm_instances ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    logger.info(f"Migration: column '{col_name}' added successfully")

    # 首次运行（或升级）时，为「服务器/存储类别」写入默认可选项
    try:
        from app.models import ServerCategory
        if inspector.has_table("server_categories"):
            with SessionLocal() as db:
                if db.query(ServerCategory).count() == 0:
                    for name in ("服务器", "存储"):
                        db.add(ServerCategory(name=name))
                    db.commit()
                    logger.info("Migration: seeded default server_categories")
    except Exception as e:
        logger.warning(f"Seeding server_categories failed: {e}")

def _backup_database() -> str:
    """Iterative-upgrade safety net: snapshot the existing DB before any
    create/migrate so a bad schema change can never destroy user data.

    Keeps only the most recent 5 snapshots under data/backups/db_backups/.
    Uses SQLite's online backup API so WAL contents are included in one
    transactionally consistent database file.
    """
    try:
        db_path = settings.DATABASE_URL.replace("sqlite:///", "", 1)
        if not os.path.exists(db_path):
            return ""  # fresh install, nothing to preserve
        db_size = os.path.getsize(db_path)
        free = shutil.disk_usage(os.path.dirname(db_path) or ".").free
        required = max(32 * 1024 * 1024, db_size * 3)
        if free < required:
            raise RuntimeError(
                f"磁盘空间不足，升级前至少需要 {required // (1024 * 1024)} MB 可用空间"
            )
        backup_dir = os.path.join(settings.BACKUP_DIR, "db_backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        base_dst = os.path.join(backup_dir, f"netmgr-{ts}.db")
        source = sqlite3.connect(db_path, timeout=30)
        destination = sqlite3.connect(base_dst)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        logger.info(f"DB snapshot saved: {base_dst}")
        # prune old snapshots, keep latest 5
        files = sorted(
            glob.glob(os.path.join(backup_dir, "netmgr-*.db")),
            key=os.path.getmtime, reverse=True,
        )
        for old in files[5:]:
            for candidate in (old, old + "-wal", old + "-shm"):
                try:
                    os.remove(candidate)
                except OSError:
                    pass
        return base_dst
    except Exception as e:
        # Never mutate an existing database unless a recoverable pre-upgrade
        # copy has been created successfully.
        raise RuntimeError(f"升级前数据库快照失败: {e}") from e


def init_db():
    """Back up the database, apply versioned migrations, then data upgrades."""
    status = {
        "status": "running", "version": settings.APP_VERSION,
        "started_at": datetime.utcnow().isoformat() + "Z", "completed_at": None,
        "revision_before": _schema_revision(), "revision_after": None,
        "backup_path": "", "error": "",
    }
    _write_upgrade_status(status)
    try:
        before = check_database_integrity()
        if not before["ok"]:
            raise RuntimeError(
                f"数据库升级前检查失败: {before['integrity']}，外键异常 {len(before['foreign_key_violations'])} 条"
            )
        status["backup_path"] = _backup_database()
        _write_upgrade_status(status)
        _run_schema_migrations()
        # Upgrade credentials encrypted by pre-v2 releases. This runs only after
        # tables/migrations exist and is idempotent.
        from app.models import migrate_legacy_credentials
        with SessionLocal() as db:
            migrated = migrate_legacy_credentials(db)
        if migrated:
            logger.info("Re-encrypted %d legacy credential value(s)", migrated)
        after = check_database_integrity()
        if not after["ok"]:
            raise RuntimeError(
                f"数据库升级后检查失败: {after['integrity']}，外键异常 {len(after['foreign_key_violations'])} 条"
            )
        status.update({
            "status": "success", "completed_at": datetime.utcnow().isoformat() + "Z",
            "revision_after": _schema_revision(),
        })
        _write_upgrade_status(status)
    except Exception as exc:
        status.update({
            "status": "failed", "completed_at": datetime.utcnow().isoformat() + "Z",
            "revision_after": _schema_revision(), "error": str(exc)[:2000],
        })
        _write_upgrade_status(status)
        raise


def _alembic_config():
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))
    return config


def _run_schema_migrations():
    """Upgrade schema with Alembic, baselining pre-Alembic installations.

    Older releases created tables directly and patched columns on startup.
    Their one-time compatibility migration is retained here only to reach the
    first Alembic revision safely. All later changes belong in migrations/.
    """
    from alembic import command

    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"Migration scripts not found: {MIGRATIONS_DIR}")

    inspector = inspect(engine)
    has_version_table = inspector.has_table("alembic_version")
    has_legacy_schema = any(
        inspector.has_table(name)
        for name in ("devices", "users", "system_settings", "task_logs")
    )
    config = _alembic_config()
    if not has_version_table and has_legacy_schema:
        logger.info("Baselining legacy database into Alembic revision history")
        Base.metadata.create_all(bind=engine)
        _migrate_database()
        command.stamp(config, "20260902_0001")
    command.upgrade(config, "head")
