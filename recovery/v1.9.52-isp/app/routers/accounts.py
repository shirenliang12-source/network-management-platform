"""账号密码保险柜（Account Vault）API 路由。"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Device
from app.schemas import AccountCreate, AccountUpdate, AccountResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountResponse])
def list_accounts(db: Session = Depends(get_db), category: str = "", search: str = ""):
    """列出账号；可按分类 / 关键字(名称/用户名/地址)过滤。"""
    q = db.query(Account)
    if category:
        q = q.filter(Account.category == category)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (Account.name.like(like)) |
            (Account.username.like(like)) |
            (Account.address.like(like))
        )
    items = q.order_by(Account.name).all()
    return [_to_response(a, db) for a in items]


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    a = db.query(Account).get(account_id)
    if not a:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _to_response(a, db)


@router.post("", response_model=AccountResponse)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    if not payload.name:
        raise HTTPException(status_code=400, detail="请填写账号名称")
    a = Account(
        name=payload.name,
        username=payload.username,
        category=payload.category or "其他",
        source=payload.source or "手动",
        address=payload.address,
        linked_device_id=payload.linked_device_id,
        email=payload.email,
        notes=payload.notes,
    )
    a.set_password(payload.password)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_response(a, db)


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)):
    a = db.query(Account).get(account_id)
    if not a:
        raise HTTPException(status_code=404, detail="账号不存在")
    data = payload.model_dump(exclude_unset=True)
    pw = data.pop("password", None)
    for k, v in data.items():
        setattr(a, k, v)
    if pw is not None:
        a.set_password(pw)
    db.commit()
    db.refresh(a)
    return _to_response(a, db)


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    a = db.query(Account).get(account_id)
    if not a:
        raise HTTPException(status_code=404, detail="账号不存在")
    db.delete(a)
    db.commit()
    return {"message": "账号已删除"}


@router.post("/{account_id}/reveal")
def reveal_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    """临时解密返回密码。AD 同步且无本地密码时返回 ad_managed=True。"""
    a = db.query(Account).get(account_id)
    if not a:
        raise HTTPException(status_code=404, detail="账号不存在")
    request.state.audit_action = "vault.reveal_password"
    request.state.audit_resource_type = "account"
    request.state.audit_resource_id = account_id
    request.state.audit_detail = {"account_name": a.name, "account_username": a.username}
    if a.source == "AD同步" and not a.password_enc:
        return {"password": "", "ad_managed": True}
    return {"password": a.get_password(), "ad_managed": False}


def _to_response(a: Account, db: Session) -> AccountResponse:
    dev_name = None
    if a.linked_device_id:
        d = db.query(Device).get(a.linked_device_id)
        if d:
            dev_name = d.name
    return AccountResponse(
        id=a.id,
        name=a.name,
        username=a.username,
        category=a.category,
        source=a.source,
        has_password=bool(a.password_enc),
        ad_managed=(a.source == "AD同步" and not a.password_enc),
        address=a.address,
        linked_device_id=a.linked_device_id,
        linked_device_name=dev_name,
        email=a.email,
        ad_dn=a.ad_dn,
        ad_sam=a.ad_sam,
        notes=a.notes,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )
