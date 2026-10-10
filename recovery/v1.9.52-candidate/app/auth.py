"""Web UI 登录鉴权 + 多用户权限体系。

设计要点（来自用户决策）：
- 管理账号存储于独立的 users 表，与「账号密码」保险库 Account 互不干涉。
- 权限模型：超级管理员（最大权限，访问全部模块）+ 普通账号按「功能模块」收敛。
- 登录鉴权强制开启，不可关闭；会话用 HMAC 签名的 HttpOnly cookie（默认 7 天）。
- 首次启动：若 users 表为空，则从旧 web_auth 迁移；否则创建使用随机引导
  密码的 admin，并将密码写入数据目录下仅所有者可读的临时文件。
- 登录密码使用 pbkdf2_hmac 单向哈希（不可逆）。
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import INITIAL_ADMIN_PASSWORD_FILE, settings
from app.database import SessionLocal, engine
from app.models import SystemSetting, User, encrypt_password, decrypt_password

WEB_AUTH_KEY = "web_auth"
SESSION_COOKIE = "wb_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 天

PBKDF2_ITERS = 200_000
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 功能模块目录：key / 中文名 / 图标。同时用于权限勾选与前端导航过滤。
# 顺序即设置页与导航的展示顺序。
# ---------------------------------------------------------------------------
MODULES = [
    ("dashboard", "仪表盘", "📊"),
    ("devices", "设备管理", "📡"),
    ("topology", "拓扑图", "🌐"),
    ("backups", "配置备份", "💾"),
    ("ip_inventory", "IP地址清单", "🗂️"),
    ("ipam", "IP地址规划", "🌳"),
    ("dc", "数据中心", "🗄️"),
    ("assets", "其他资产", "📦"),
    ("servers", "服务器存储", "🖥️"),
    ("vms", "虚拟机系统", "💻"),
    ("integrations", "平台集成", "🔗"),
    ("accounts", "账号密码", "🔑"),
    ("ad", "AD集成", "🏢"),
    ("schedule", "定时任务", "⏰"),
    ("logs", "系统日志", "🪵"),
    ("credentials", "SSH凭据", "🔐"),
    ("commands", "命令模板", "📜"),
    ("nics", "网卡信息", "🔌"),
    ("serial", "串口管理", "🧵"),
    ("settings", "系统设置", "⚙️"),
    ("users", "用户管理", "👤"),
]
MODULE_KEYS = [m[0] for m in MODULES]
MODULE_LABELS = {m[0]: m[1] for m in MODULES}

# 请求路径 -> 模块 前缀映射（取最长匹配）。
PATH_MODULES = [
    ("/api/users", "users"), ("/users", "users"),
    ("/api/accounts", "accounts"), ("/accounts", "accounts"),
    ("/api/ad", "ad"),
    ("/api/logs", "logs"), ("/system/logs", "logs"),
    ("/api/credentials", "credentials"), ("/credentials", "credentials"),
    ("/api/commands", "commands"), ("/commands", "commands"),
    ("/api/nics", "nics"), ("/nics", "nics"),
    ("/api/serial", "serial"), ("/serial", "serial"),
    ("/api/settings", "settings"), ("/settings", "settings"),
    ("/api/dc", "dc"), ("/datacenter", "dc"),
    ("/api/assets/server-categories", "servers"),
    ("/api/assets/servers", "servers"), ("/servers", "servers"),
    ("/api/assets", "assets"), ("/assets", "assets"),
    ("/api/vms", "vms"), ("/vms", "vms"),
    ("/api/integrations", "integrations"), ("/integrations", "integrations"),
    ("/api/ipam", "ipam"), ("/ipam", "ipam"),
    ("/api/ip_inventory", "ip_inventory"), ("/ip-inventory", "ip_inventory"),
    ("/api/backups", "backups"), ("/backups", "backups"),
    ("/changes", "backups"),
    ("/api/topology", "topology"), ("/topology", "topology"),
    ("/api/schedule", "schedule"), ("/schedule", "schedule"),
    ("/api/devices", "devices"), ("/devices", "devices"),
    ("/api/dashboard", "dashboard"), ("/", "dashboard"),
]


def module_for_path(path: str):
    """返回该请求路径归属的功能模块 key；无法归类返回 None。"""
    best, best_len = None, -1
    for prefix, mod in PATH_MODULES:
        if path == prefix or path.startswith(prefix + "/"):
            if len(prefix) > best_len:
                best, best_len = mod, len(prefix)
    return best


# ---------------------------------------------------------------------------
# 会话 token（HMAC 签名，仅承载用户名 + 时间戳）
# ---------------------------------------------------------------------------
def _sign(data: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def make_token(username: str) -> str:
    ts = str(int(time.time()))
    user = get_user_by_username(username)
    version = int(getattr(user, "session_version", 1) or 1)
    payload = f"{username}|{ts}|{version}"
    sig = _sign(payload)
    raw = f"{payload}|{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def verify_token(token: str):
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        parts = raw.split("|")
        if len(parts) == 3:
            # Transitional support for sessions issued before v1.9.39. New
            # sessions below are revision-bound and can be revoked instantly.
            username, ts, sig = parts
            if not hmac.compare_digest(_sign(f"{username}|{ts}"), sig):
                return None
        elif len(parts) == 4:
            username, ts, version, sig = parts
            if not hmac.compare_digest(_sign(f"{username}|{ts}|{version}"), sig):
                return None
            user = get_user_by_username(username)
            if not user or int(getattr(user, "session_version", 1) or 1) != int(version):
                return None
        else:
            return None
        issued_at = int(ts)
        now = int(time.time())
        # Reject expired tokens and timestamps too far in the future. Cookie
        # max_age alone is not a security boundary because clients control it.
        if issued_at > now + 300 or now - issued_at > SESSION_MAX_AGE:
            return None
        return username
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 密码哈希（pbkdf2，单向）
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# User 表操作
# ---------------------------------------------------------------------------
def get_user_by_username(username: str):
    with SessionLocal() as db:
        return db.query(User).filter(User.username == username).first()


def authenticate(username: str, password: str):
    """校验账号密码；成功返回 User，失败返回 None。"""
    user = get_user_by_username(username)
    if user and user.is_active and verify_password(password, user.password_hash):
        return user
    return None


def list_users() -> list:
    with SessionLocal() as db:
        rows = db.query(User).order_by(User.id).all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "is_superuser": u.is_superuser,
                "is_active": u.is_active,
                "modules": u.get_modules(),
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ]


def create_user(username: str, password: str, is_superuser: bool, is_active: bool, modules: list) -> User:
    with SessionLocal() as db:
        u = User(
            username=username,
            password_hash=hash_password(password),
            is_superuser=is_superuser,
            is_active=is_active,
            modules=json.dumps(modules or []),
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u


def update_user(user_id: int, **fields) -> User:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise ValueError("用户不存在")
        if "username" in fields:
            u.username = fields["username"]
        if "is_superuser" in fields:
            u.is_superuser = fields["is_superuser"]
        if "is_active" in fields:
            u.is_active = fields["is_active"]
        if "modules" in fields:
            u.modules = json.dumps(fields["modules"] or [])
        if fields.get("password"):
            u.password_hash = hash_password(fields["password"])
        if fields.get("password") or "is_active" in fields:
            u.session_version = int(u.session_version or 1) + 1
        u.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(u)
        if fields.get("password") and u.is_superuser:
            try:
                INITIAL_ADMIN_PASSWORD_FILE.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove bootstrap password file: %s", INITIAL_ADMIN_PASSWORD_FILE)
        return u


def delete_user(user_id: int) -> None:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise ValueError("用户不存在")
        db.delete(u)
        db.commit()


def count_superusers() -> int:
    with SessionLocal() as db:
        return db.query(func.count(User.id)).filter(User.is_superuser == True).scalar() or 0


def count_active_superusers() -> int:
    """Return the number of accounts that can currently administer users."""
    with SessionLocal() as db:
        return (
            db.query(func.count(User.id))
            .filter(User.is_superuser == True, User.is_active == True)
            .scalar()
            or 0
        )


# ---------------------------------------------------------------------------
# 默认管理员 / 旧 web_auth 迁移
# ---------------------------------------------------------------------------
def _initial_admin_password() -> str:
    """Load an explicit bootstrap password or create a stable random one."""
    configured = (os.environ.get("NETMGR_INITIAL_ADMIN_PASSWORD") or "").strip()
    if configured:
        if len(configured) < 12:
            raise RuntimeError("NETMGR_INITIAL_ADMIN_PASSWORD must contain at least 12 characters")
        return configured

    if INITIAL_ADMIN_PASSWORD_FILE.exists():
        password = INITIAL_ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        if len(password) >= 12:
            return password

    password = secrets.token_urlsafe(24)
    INITIAL_ADMIN_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(INITIAL_ADMIN_PASSWORD_FILE),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(password)
        handle.write("\n")
    return password


def ensure_default_admin() -> None:
    """Create the first superuser without ever using an empty password."""
    with SessionLocal() as db:
        if db.query(func.count(User.id)).scalar():
            return
        migrated = None
        row = db.get(SystemSetting, WEB_AUTH_KEY)
        if row and row.value:
            try:
                auth = json.loads(row.value)
                uname = auth.get("username") or "admin"
                pw = decrypt_password(auth.get("password_enc", ""))
                migrated = (uname, pw)
            except Exception:
                migrated = None
        if migrated and migrated[1]:
            uname, pw = migrated
        else:
            uname, pw = "admin", _initial_admin_password()
        u = User(
            username=uname,
            password_hash=hash_password(pw),
            is_superuser=True,
            is_active=True,
            modules="[]",
        )
        db.add(u)
        db.commit()
        if not os.environ.get("NETMGR_INITIAL_ADMIN_PASSWORD"):
            logger.warning(
                "Initial admin password was written to %s; change it immediately after login",
                INITIAL_ADMIN_PASSWORD_FILE,
            )
