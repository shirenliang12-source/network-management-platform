"""数据中心 (Data Center) API routes — NetBox 风格：站点 (Site) -> 机柜 (Rack)。"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import DCSite, DCRack
from app.schemas import (
    DCSiteCreate,
    DCSiteUpdate,
    DCSiteResponse,
    DCRackCreate,
    DCRackUpdate,
    DCRackResponse,
    DCSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dc", tags=["datacenter"])


@router.get('/racks/{rack_id}/relations')
def rack_relations(rack_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.asset_relations import relations, request_modules
    return relations(db, 'rack', rack_id, request_modules(request))


@router.get('/sites/{site_id}/relations')
def site_relations(site_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.asset_relations import relations, request_modules
    return relations(db, 'site', site_id, request_modules(request))

# 机柜状态 / 类型 / 宽度 / 角色 选项（与 UI 下拉保持一致）
DC_RACK_STATUSES = ["在用", "规划中", "预留", "停用"]
DC_RACK_TYPES = ["机柜", "开放式机架", "壁挂式"]
DC_RACK_WIDTHS = ["19英寸", "23英寸", "其他"]
DC_RACK_ROLES = ["服务器区", "网络区", "存储区", "布线区", "安全区", "其他"]


def _valid(value: str, allowed: list, default: str) -> str:
    return value if value in allowed else default


# ---------------------------------------------------------------------------
# Sites (站点)
# ---------------------------------------------------------------------------
@router.get("/sites", response_model=list[DCSiteResponse])
def list_sites(db: Session = Depends(get_db), search: str = Query("", description="搜索站点名称/公司/区域/地址")):
    q = db.query(DCSite)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (DCSite.name.ilike(like))
            | (DCSite.company.ilike(like))
            | (DCSite.region.ilike(like))
            | (DCSite.address.ilike(like))
            | (DCSite.contact_name.ilike(like))
        )
    items = q.order_by(DCSite.name.asc()).all()
    return [_site_response(db, s) for s in items]


@router.get("/sites/{site_id}", response_model=DCSiteResponse)
def get_site(site_id: int, db: Session = Depends(get_db)):
    site = db.query(DCSite).get(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    return _site_response(db, site)


@router.post("/sites", response_model=DCSiteResponse)
def create_site(payload: DCSiteCreate, db: Session = Depends(get_db)):
    if not payload.name:
        raise HTTPException(status_code=400, detail="站点名称不能为空")
    site = DCSite(
        name=payload.name.strip(),
        company=(payload.company or "").strip(),
        region=(payload.region or "").strip(),
        address=(payload.address or "").strip(),
        contact_name=(payload.contact_name or "").strip(),
        contact_phone=(payload.contact_phone or "").strip(),
        description=(payload.description or "").strip(),
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return _site_response(db, site)


@router.put("/sites/{site_id}", response_model=DCSiteResponse)
def update_site(site_id: int, payload: DCSiteUpdate, db: Session = Depends(get_db)):
    site = db.query(DCSite).get(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(site, key, (value or "") if isinstance(value, str) else value)
    db.commit()
    db.refresh(site)
    return _site_response(db, site)


@router.delete("/sites/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db)):
    if db.query(DCRack).filter_by(site_id=site_id).first():
        raise HTTPException(409, '站点仍有机柜，请先迁移或删除机柜')
    site = db.query(DCSite).get(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    # 级联删除其下所有机柜（避免孤儿数据）
    db.query(DCRack).filter(DCRack.site_id == site_id).delete()
    db.delete(site)
    db.commit()
    return {"message": "已删除"}


def _site_response(db: Session, site: DCSite) -> DCSiteResponse:
    rack_count = db.query(func.count(DCRack.id)).filter(DCRack.site_id == site.id).scalar() or 0
    return DCSiteResponse(
        id=site.id,
        name=site.name or "",
        company=site.company or "",
        region=site.region or "",
        address=site.address or "",
        contact_name=site.contact_name or "",
        contact_phone=site.contact_phone or "",
        description=site.description or "",
        rack_count=rack_count,
        created_at=site.created_at,
        updated_at=site.updated_at,
    )


# ---------------------------------------------------------------------------
# Racks (机柜)
# ---------------------------------------------------------------------------
@router.get("/racks", response_model=list[DCRackResponse])
def list_racks(
    db: Session = Depends(get_db),
    site_id: int = Query(None),
    status: str = Query(""),
    search: str = Query(""),
):
    q = db.query(DCRack)
    if site_id is not None:
        q = q.filter(DCRack.site_id == site_id)
    if status:
        q = q.filter(DCRack.status == status)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (DCRack.rack_number.ilike(like))
            | (DCRack.name.ilike(like))
            | (DCRack.role.ilike(like))
            | (DCRack.serial.ilike(like))
            | (DCRack.asset_tag.ilike(like))
            | (DCRack.location_detail.ilike(like))
            | (DCRack.description.ilike(like))
        )
    items = q.order_by(DCRack.rack_number.asc()).all()
    return [_rack_response(db, r) for r in items]


@router.get("/racks/{rack_id}", response_model=DCRackResponse)
def get_rack(rack_id: int, db: Session = Depends(get_db)):
    rack = db.query(DCRack).get(rack_id)
    if not rack:
        raise HTTPException(status_code=404, detail="机柜不存在")
    return _rack_response(db, rack)


@router.post("/racks", response_model=DCRackResponse)
def create_rack(payload: DCRackCreate, db: Session = Depends(get_db)):
    site = db.query(DCSite).get(payload.site_id)
    if not site:
        raise HTTPException(status_code=400, detail="所属站点不存在")
    if not payload.rack_number:
        raise HTTPException(status_code=400, detail="机柜编号不能为空")
    rack = DCRack(
        site_id=payload.site_id,
        rack_number=payload.rack_number.strip(),
        name=(payload.name or "").strip(),
        role=(payload.role or "").strip(),
        type=_valid(payload.type, DC_RACK_TYPES, "机柜"),
        width=_valid(payload.width, DC_RACK_WIDTHS, "19英寸"),
        u_height=payload.u_height if payload.u_height and payload.u_height > 0 else 42,
        status=_valid(payload.status, DC_RACK_STATUSES, "在用"),
        serial=(payload.serial or "").strip(),
        asset_tag=(payload.asset_tag or "").strip(),
        location_detail=(payload.location_detail or "").strip(),
        description=(payload.description or "").strip(),
    )
    db.add(rack)
    db.commit()
    db.refresh(rack)
    return _rack_response(db, rack)


@router.put("/racks/{rack_id}", response_model=DCRackResponse)
def update_rack(rack_id: int, payload: DCRackUpdate, db: Session = Depends(get_db)):
    rack = db.query(DCRack).get(rack_id)
    if not rack:
        raise HTTPException(status_code=404, detail="机柜不存在")
    data = payload.model_dump(exclude_unset=True)
    # 校验关联站点
    if "site_id" in data and data["site_id"]:
        site = db.query(DCSite).get(data["site_id"])
        if not site:
            raise HTTPException(status_code=400, detail="所属站点不存在")
    for key, value in data.items():
        if value is None:
            continue
        if key == "type":
            value = _valid(value, DC_RACK_TYPES, rack.type)
        elif key == "width":
            value = _valid(value, DC_RACK_WIDTHS, rack.width)
        elif key == "status":
            value = _valid(value, DC_RACK_STATUSES, rack.status)
        elif key == "u_height":
            value = value if value and value > 0 else rack.u_height
        elif isinstance(value, str):
            value = value.strip()
        setattr(rack, key, value)
    db.commit()
    db.refresh(rack)
    return _rack_response(db, rack)


@router.delete("/racks/{rack_id}")
def delete_rack(rack_id: int, db: Session = Depends(get_db)):
    from app.models import ITAsset, ServerAsset
    if db.query(ITAsset).filter_by(rack_id=rack_id).first() or db.query(ServerAsset).filter_by(rack_id=rack_id).first():
        raise HTTPException(409, '机柜仍有关联资产，请先迁移或删除资产')
    rack = db.query(DCRack).get(rack_id)
    if not rack:
        raise HTTPException(status_code=404, detail="机柜不存在")
    db.delete(rack)
    db.commit()
    return {"message": "已删除"}


def _rack_response(db: Session, rack: DCRack) -> DCRackResponse:
    site = db.query(DCSite).get(rack.site_id)
    return DCRackResponse(
        id=rack.id,
        site_id=rack.site_id,
        site_name=site.name if site else "",
        rack_number=rack.rack_number or "",
        name=rack.name or "",
        role=rack.role or "",
        type=rack.type or "机柜",
        width=rack.width or "19英寸",
        u_height=rack.u_height or 42,
        status=rack.status or "在用",
        serial=rack.serial or "",
        asset_tag=rack.asset_tag or "",
        location_detail=rack.location_detail or "",
        description=rack.description or "",
        created_at=rack.created_at,
        updated_at=rack.updated_at,
    )


# ---------------------------------------------------------------------------
# Summary (仪表盘用)
# ---------------------------------------------------------------------------
@router.get("/summary", response_model=DCSummary)
def dc_summary(db: Session = Depends(get_db)):
    sites_count = db.query(func.count(DCSite.id)).scalar() or 0
    racks_count = db.query(func.count(DCRack.id)).scalar() or 0
    by_status = {}
    for s in DC_RACK_STATUSES:
        by_status[s] = db.query(func.count(DCRack.id)).filter(DCRack.status == s).scalar() or 0
    # 角色统计（仅统计非空角色）
    by_role = {}
    rows = db.query(DCRack.role, func.count(DCRack.id)).group_by(DCRack.role).all()
    for role, cnt in rows:
        key = role or "未分类"
        by_role[key] = cnt
    return DCSummary(
        sites_count=sites_count,
        racks_count=racks_count,
        racks_by_status=by_status,
        racks_by_role=by_role,
    )
