"""Application configuration."""
import os
import secrets
import sys
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


def _get_base_dir() -> Path:
    """Return the base directory for data files.
    
    When running as a PyInstaller bundle (frozen), data files are stored
    next to the executable. In development, they're in the project root.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller mode: data goes next to the .exe
        return Path(sys.executable).parent
    else:
        # Development mode: data goes in project root
        return Path(__file__).parent.parent


def _get_resource_dir() -> Path:
    """Return the directory containing bundled resources (templates, static).
    
    When frozen, resources are extracted to sys._MEIPASS.
    In development, they're in the app package directory.
    """
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / "app"
    else:
        return Path(__file__).parent


BASE_DIR = _get_base_dir()
RESOURCE_DIR = _get_resource_dir()
MIGRATIONS_DIR = (Path(sys._MEIPASS) if getattr(sys, "frozen", False) else BASE_DIR) / "migrations"

# Support custom data directory via CISCO_NM_DATA_DIR env var.
# This allows the installer to place data on a network share or different drive.
_custom_data = os.environ.get("CISCO_NM_DATA_DIR")
if _custom_data:
    DATA_DIR = Path(_custom_data)
else:
    DATA_DIR = BASE_DIR / "data"

SECRET_KEY_FILE = DATA_DIR / ".secret_key"
INITIAL_ADMIN_PASSWORD_FILE = DATA_DIR / "initial_admin_password.txt"


def _load_or_create_secret_key() -> str:
    """Return a stable, installation-specific secret.

    An explicit NETMGR_SECRET_KEY wins. Otherwise a random key is created in
    the data directory with owner-only permissions where the OS supports it.
    This avoids shipping the same session/encryption key in every build.
    """
    configured = (os.environ.get("NETMGR_SECRET_KEY") or "").strip()
    if configured:
        if len(configured) < 32:
            raise ValueError("NETMGR_SECRET_KEY must contain at least 32 characters")
        return configured

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        existing = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        if len(existing) < 32:
            raise ValueError(f"Secret key file is invalid: {SECRET_KEY_FILE}")
        return existing

    generated = secrets.token_urlsafe(48)
    try:
        fd = os.open(str(SECRET_KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(generated)
        handle.write("\n")
    return generated


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Cisco 网络自动化运维平台"
    APP_VERSION: str = "1.9.41"
    HOST: str = "0.0.0.0"
    PORT: int = 9632
    DEBUG: bool = False
    COOKIE_SECURE: bool = False

    # Database
    DATABASE_URL: str = f"sqlite:///{DATA_DIR / 'netmgr.db'}"

    # Backup storage
    BACKUP_DIR: str = str(DATA_DIR / "backups")
    EXPORT_DIR: str = str(DATA_DIR / "exports")
    # Backup retention: delete config backups older than N days (0 = keep forever)
    BACKUP_RETENTION_DAYS: int = 0

    # SSH defaults
    SSH_TIMEOUT: int = 30
    SSH_GLOBAL_DELAY: float = 2.0
    MAX_CONCURRENT_SESSIONS: int = 20

    # Scheduler defaults (cron: minute hour day month day_of_week)
    DEFAULT_BACKUP_SCHEDULE: str = "0 2 * * *"      # Daily at 2 AM
    DEFAULT_DISCOVERY_SCHEDULE: str = "0 3 * * *"   # Daily at 3 AM
    DEFAULT_INFO_SCHEDULE: str = "0 4 * * 1"        # Weekly Monday 4 AM

    # Installation-specific key. Override with NETMGR_SECRET_KEY in managed
    # deployments; never place the value in source control.
    SECRET_KEY: str = Field(default_factory=_load_or_create_secret_key)

    class Config:
        env_file = ".env"
        env_prefix = "NETMGR_"


settings = Settings()

# Ensure directories exist
os.makedirs(settings.BACKUP_DIR, exist_ok=True)
os.makedirs(settings.EXPORT_DIR, exist_ok=True)
os.makedirs(os.path.dirname(settings.DATABASE_URL.replace("sqlite:///", "")), exist_ok=True)
