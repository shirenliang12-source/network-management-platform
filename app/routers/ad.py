"""AD / LDAP 集成 API 路由。配置持久化于 system_settings (key=ad_config)。"""
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemSetting, Account
from app.schemas import ADConfig, ADVerifyRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ad", tags=["ad"])

CFG_KEY = "ad_config"
DEFAULTS = {
    "host": "", "port": 389, "use_ssl": False, "bind_dn": "",
    "bind_password": "", "base_dn": "", "user_filter": "(objectClass=user)",
    "sam_attr": "sAMAccountName", "name_attr": "displayName", "mail_attr": "mail",
}


def _get_cfg(db: Session) -> Dict[str, Any]:
    """读取配置（bind_password 不回显）。"""
    cfg = dict(DEFAULTS)
    row = db.query(SystemSetting).get(CFG_KEY)
    if row and row.value:
        try:
            cfg.update(json.loads(row.value))
        except Exception:
            pass
    cfg["bind_password"] = ""
    return cfg


def _cfg_for_ldap(db: Session) -> Dict[str, Any]:
    """读取配置并解密 bind_password 以供 LDAP 使用。"""
    cfg = dict(DEFAULTS)
    row = db.query(SystemSetting).get(CFG_KEY)
    if row and row.value:
        try:
            cfg.update(json.loads(row.value))
        except Exception:
            pass
    enc = cfg.get("bind_password") or ""
    cfg["bind_password"] = _decrypt(enc) if enc else ""
    return cfg


def _save_cfg(db: Session, data: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    raw_pw = data.get("bind_password") or ""
    cfg["bind_password"] = _encrypt(raw_pw)  # 加密存储
    row = db.query(SystemSetting).get(CFG_KEY)
    if not row:
        row = SystemSetting(key=CFG_KEY, value=json.dumps(cfg, ensure_ascii=False))
        db.add(row)
    else:
        row.value = json.dumps(cfg, ensure_ascii=False)
    db.commit()
    out = dict(cfg)
    out["bind_password"] = ""
    return out


def _encrypt(pw: str) -> str:
    from app.models import encrypt_password
    return encrypt_password(pw)


def _decrypt(enc: str) -> str:
    from app.models import decrypt_password
    return decrypt_password(enc)


@router.get("/config", response_model=ADConfig)
def get_config(db: Session = Depends(get_db)):
    return ADConfig(**_get_cfg(db))


@router.put("/config", response_model=ADConfig)
def save_config(payload: ADConfig, db: Session = Depends(get_db)):
    return ADConfig(**_save_cfg(db, payload.model_dump()))


@router.post("/test")
def test_connection(db: Session = Depends(get_db)):
    try:
        from app.services.ad_service import test_connection as ldap_test
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"AD 模块未安装(ldap3): {e}")
    cfg = _cfg_for_ldap(db)
    if not cfg.get("host"):
        raise HTTPException(status_code=400, detail="请先填写 AD 服务器地址")
    if not cfg.get("bind_dn"):
        raise HTTPException(status_code=400, detail="请填写绑定 DN（Bind DN）")
    return ldap_test(cfg)


@router.post("/sync")
def sync_users(db: Session = Depends(get_db)):
    try:
        from app.services.ad_service import search_users
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"AD 模块未安装(ldap3): {e}")
    cfg = _cfg_for_ldap(db)
    if not cfg.get("host") or not cfg.get("bind_dn"):
        raise HTTPException(status_code=400, detail="请先配置并测试 AD 连接")
    try:
        users = search_users(cfg)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AD 查询失败: {e}")
    imported = updated = 0
    for u in users:
        sam = u["sam"]
        existing = db.query(Account).filter(
            Account.ad_sam == sam, Account.source == "AD同步"
        ).first()
        if existing:
            existing.name = u["name"]
            existing.email = u["mail"]
            existing.ad_dn = u["dn"]
            existing.notes = "AD 账号" + ("（已禁用）" if u["disabled"] else "")
            updated += 1
        else:
            acc = Account(
                name=u["name"],
                username=sam,
                category="AD用户",
                source="AD同步",
                address="",
                linked_device_id=None,
                email=u["mail"],
                ad_dn=u["dn"],
                ad_sam=sam,
                notes="AD 账号" + ("（已禁用）" if u["disabled"] else ""),
            )
            # 不存密码：AD 为权威源，本地仅保存账号引用
            db.add(acc)
            imported += 1
    db.commit()
    return {
        "ok": True,
        "imported": imported,
        "updated": updated,
        "total": len(users),
        "message": f"同步完成：新增 {imported} 个，更新 {updated} 个（共 {len(users)} 个 AD 用户）",
    }


@router.post("/verify")
def verify_credential(payload: ADVerifyRequest, db: Session = Depends(get_db)):
    try:
        from app.services.ad_service import verify_credential as ldap_verify
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"AD 模块未安装(ldap3): {e}")
    cfg = _cfg_for_ldap(db)
    if not cfg.get("host"):
        raise HTTPException(status_code=400, detail="请先配置 AD 服务器")
    username = payload.username.strip()
    password = payload.password
    return ldap_verify(cfg, username, password)
