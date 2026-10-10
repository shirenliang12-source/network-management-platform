"""资产模块 API routes — 其他 IT 资产 (it_assets) + 服务器/存储 (server_assets)。

与「网络设备」(devices) 区分：
- 其他 IT 资产：打印机 / 无线AP / IP电话 / 摄像头 等，可关联一台已发现的网络设备。
- 服务器/存储：关联机柜 (dc_racks) 并指定 U 位（起始U + 占用U数），机柜视图可展示占用。
"""
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import ITAsset, ServerAsset, ServerIP, ServerCategory, DCRack, DCSite, Device
from app.schemas import (
    ITAssetCreate, ITAssetUpdate, ITAssetResponse,
    ServerAssetCreate, ServerAssetUpdate, ServerAssetResponse,
    AssetSummary, ServerCategorySchema, ServerCategoryCreate, ServerCategoryUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get('/servers/{server_id}/relations')
def server_relations(server_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.asset_relations import relations, request_modules
    return relations(db, 'server', server_id, request_modules(request))

ASSET_STATUSES = ["在用", "备用", "停用", "报废"]
IT_TYPES = ["打印机", "无线AP", "IP电话", "摄像头", "其他"]
SERVER_CATEGORIES = ["服务器", "存储"]
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _valid(value: str, allowed: list, default: str) -> str:
    return value if value in allowed else default


def _category_color(value: str) -> str:
    color = (value or "").strip()
    if color and not _HEX_COLOR_RE.fullmatch(color):
        raise HTTPException(status_code=400, detail="类别颜色必须为 #RRGGBB 格式")
    return color


# ---- Multi-IP helpers (servers / 存储) ----
def _normalize_ips(ips: list) -> list:
    seen, out = set(), []
    for ip in ips or []:
        ip = (ip or "").strip()
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _server_ip_used_by_other(db, ip: str, exclude_server_id: int = None) -> bool:
    if db.query(ServerAsset).filter(ServerAsset.management_ip == ip).first():
        s = db.query(ServerAsset).filter(ServerAsset.management_ip == ip).first()
        if s.id != exclude_server_id:
            return True
    row = db.query(ServerIP).filter(ServerIP.ip_address == ip).first()
    if row and row.server_id != exclude_server_id:
        return True
    return False


def _set_server_ips(db, server: ServerAsset, additional_ips: list):
    additional_ips = _normalize_ips(additional_ips)
    kept = []
    for ip in additional_ips:
        if ip == server.management_ip:
            continue
        if _server_ip_used_by_other(db, ip, exclude_server_id=server.id):
            logger.info(f"Skip duplicate extra IP {ip} for server {server.id}")
            continue
        kept.append(ip)
    db.query(ServerIP).filter(ServerIP.server_id == server.id).delete()
    for ip in kept:
        db.add(ServerIP(server_id=server.id, ip_address=ip))


def _server_ip_list(server: ServerAsset) -> list:
    return [
        {
            "ip_address": ip.ip_address,
            "is_primary": bool(ip.is_primary),
            "notes": ip.notes or "",
        }
        for ip in (server.extra_ips or [])
    ]


# ===========================================================================
# 其他 IT 资产 (it_assets)
# ===========================================================================
@router.get("/it", response_model=list[ITAssetResponse])
def list_it_assets(
    db: Session = Depends(get_db),
    site_id: int = Query(None),
    rack_id: int = Query(None),
    asset_type: str = Query(""),
    status: str = Query(""),
    search: str = Query(""),
    linked_device_id: int = Query(None),
):
    q = db.query(ITAsset)
    if site_id is not None:
        q = q.filter(ITAsset.site_id == site_id)
    if rack_id is not None:
        q = q.filter(ITAsset.rack_id == rack_id)
    if asset_type:
        q = q.filter(ITAsset.asset_type == asset_type)
    if status:
        q = q.filter(ITAsset.status == status)
    if linked_device_id is not None:
        q = q.filter(ITAsset.linked_device_id == linked_device_id)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (ITAsset.name.ilike(like))
            | (ITAsset.brand.ilike(like))
            | (ITAsset.model.ilike(like))
            | (ITAsset.asset_tag.ilike(like))
            | (ITAsset.serial.ilike(like))
            | (ITAsset.location_detail.ilike(like))
        )
    items = q.order_by(ITAsset.name.asc()).all()
    return [_it_response(db, a) for a in items]


@router.get("/it/{asset_id}", response_model=ITAssetResponse)
def get_it_asset(asset_id: int, db: Session = Depends(get_db)):
    a = db.query(ITAsset).get(asset_id)
    if not a:
        raise HTTPException(status_code=404, detail="资产不存在")
    return _it_response(db, a)


@router.post("/it", response_model=ITAssetResponse)
def create_it_asset(payload: ITAssetCreate, db: Session = Depends(get_db)):
    if not payload.name:
        raise HTTPException(status_code=400, detail="资产名称不能为空")
    if payload.site_id:
        if not db.query(DCSite).get(payload.site_id):
            raise HTTPException(status_code=400, detail="所属站点不存在")
    if payload.linked_device_id:
        if not db.query(Device).get(payload.linked_device_id):
            raise HTTPException(status_code=400, detail="关联网络设备不存在")
    if payload.rack_id:
        if not db.query(DCRack).get(payload.rack_id):
            raise HTTPException(status_code=400, detail="关联机柜不存在")
    a = ITAsset(
        name=payload.name.strip(),
        asset_type=_valid(payload.asset_type, IT_TYPES, "其他"),
        brand=(payload.brand or "").strip(),
        model=(payload.model or "").strip(),
        serial=(payload.serial or "").strip(),
        asset_tag=(payload.asset_tag or "").strip(),
        management_ip=(payload.management_ip or "").strip(),
        linked_device_id=payload.linked_device_id,
        site_id=payload.site_id,
        location_detail=(payload.location_detail or "").strip(),
        rack_id=payload.rack_id,
        rack_position=(payload.rack_position or "").strip(),
        status=_valid(payload.status, ASSET_STATUSES, "在用"),
        notes=(payload.notes or "").strip(),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _it_response(db, a)


@router.put("/it/{asset_id}", response_model=ITAssetResponse)
def update_it_asset(asset_id: int, payload: ITAssetUpdate, db: Session = Depends(get_db)):
    a = db.query(ITAsset).get(asset_id)
    if not a:
        raise HTTPException(status_code=404, detail="资产不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("site_id"):
        if not db.query(DCSite).get(data["site_id"]):
            raise HTTPException(status_code=400, detail="所属站点不存在")
    if data.get("linked_device_id"):
        if not db.query(Device).get(data["linked_device_id"]):
            raise HTTPException(status_code=400, detail="关联网络设备不存在")
    if data.get("rack_id"):
        if not db.query(DCRack).get(data["rack_id"]):
            raise HTTPException(status_code=400, detail="关联机柜不存在")
    for key, value in data.items():
        if value is None:
            continue
        if key == "status":
            value = _valid(value, ASSET_STATUSES, a.status)
        elif key == "asset_type":
            value = _valid(value, IT_TYPES, a.asset_type)
        elif isinstance(value, str):
            value = value.strip()
        setattr(a, key, value)
    db.commit()
    db.refresh(a)
    return _it_response(db, a)


@router.delete("/it/{asset_id}")
def delete_it_asset(asset_id: int, db: Session = Depends(get_db)):
    a = db.query(ITAsset).get(asset_id)
    if not a:
        raise HTTPException(status_code=404, detail="资产不存在")
    db.delete(a)
    db.commit()
    return {"message": "已删除"}


def _it_response(db: Session, a: ITAsset) -> ITAssetResponse:
    linked = db.query(Device).get(a.linked_device_id) if a.linked_device_id else None
    site = db.query(DCSite).get(a.site_id) if a.site_id else None
    rack_number = None
    if a.rack_id:
        rack = db.query(DCRack).get(a.rack_id)
        if rack:
            rack_number = rack.rack_number
            # 若未单独填站点，则继承机柜所属站点
            if site is None and rack.site_id:
                site = db.query(DCSite).get(rack.site_id)
    return ITAssetResponse(
        id=a.id,
        name=a.name or "",
        asset_type=a.asset_type or "其他",
        brand=a.brand or "",
        model=a.model or "",
        serial=a.serial or "",
        asset_tag=a.asset_tag or "",
        management_ip=a.management_ip or "",
        linked_device_id=a.linked_device_id,
        linked_device_name=(linked.name if linked else None),
        site_id=a.site_id,
        site_name=(site.name if site else None),
        location_detail=a.location_detail or "",
        rack_id=a.rack_id,
        rack_number=rack_number,
        rack_position=a.rack_position or "",
        status=a.status or "在用",
        notes=a.notes or "",
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


# ===========================================================================
# 服务器 / 存储 (server_assets) — 关联机柜 + U 位
# ===========================================================================
@router.get("/servers", response_model=list[ServerAssetResponse])
def list_servers(
    db: Session = Depends(get_db),
    rack_id: int = Query(None),
    category: str = Query(""),
    status: str = Query(""),
    search: str = Query(""),
):
    q = db.query(ServerAsset)
    if rack_id is not None:
        q = q.filter(ServerAsset.rack_id == rack_id)
    if category:
        q = q.filter(ServerAsset.category == category)
    if status:
        q = q.filter(ServerAsset.status == status)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (ServerAsset.name.ilike(like))
            | (ServerAsset.brand.ilike(like))
            | (ServerAsset.model.ilike(like))
            | (ServerAsset.asset_tag.ilike(like))
            | (ServerAsset.serial.ilike(like))
            | (ServerAsset.owner.ilike(like))
        )
    items = q.order_by(ServerAsset.rack_id.asc(), ServerAsset.u_start.asc()).all()
    return [_server_response(db, s) for s in items]


from app.api_models import StrictRequest
from pydantic import Field
from fastapi.responses import Response


class ServerCSVRequest(StrictRequest):
    csv: str = Field(min_length=1, max_length=10_000_000)


@router.get('/servers/export')
def export_servers_csv(template: bool = False, db: Session = Depends(get_db)):
    from app.services.server_csv import export_csv
    return Response(export_csv(db, template), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=servers.csv'})


@router.post('/servers/import')
def import_servers_csv(payload: ServerCSVRequest, request: Request, db: Session = Depends(get_db)):
    from app.services.server_csv import import_csv
    result = import_csv(db, payload.csv)
    request.state.audit_action = 'servers.import'
    request.state.audit_detail = {'created': result['created'], 'skipped': result['skipped']}
    return result


@router.get("/servers/{asset_id}", response_model=ServerAssetResponse)
def get_server(asset_id: int, db: Session = Depends(get_db)):
    s = db.query(ServerAsset).get(asset_id)
    if not s:
        raise HTTPException(status_code=404, detail="资产不存在")
    return _server_response(db, s)


@router.post("/servers", response_model=ServerAssetResponse)
def create_server(payload: ServerAssetCreate, db: Session = Depends(get_db)):
    _validate_server(db, payload.rack_id, payload.u_start, payload.u_size, None)
    s = ServerAsset(
        name=payload.name.strip(),
        category=(payload.category or "").strip() or "服务器",
        brand=(payload.brand or "").strip(),
        model=(payload.model or "").strip(),
        serial=(payload.serial or "").strip(),
        asset_tag=(payload.asset_tag or "").strip(),
        rack_id=payload.rack_id,
        u_start=payload.u_start if payload.u_start and payload.u_start >= 1 else 1,
        u_size=payload.u_size if payload.u_size and payload.u_size >= 1 else 1,
        status=_valid(payload.status, ASSET_STATUSES, "在用"),
        management_ip=(payload.management_ip or "").strip(),
        os=(payload.os or "").strip(),
        cpu=(payload.cpu or "").strip(),
        memory=(payload.memory or "").strip(),
        storage_desc=(payload.storage_desc or "").strip(),
        owner=(payload.owner or "").strip(),
        notes=(payload.notes or "").strip(),
    )
    db.add(s)
    db.flush()
    _set_server_ips(db, s, payload.additional_ips)
    db.commit()
    db.refresh(s)
    return _server_response(db, s)


@router.put("/servers/{asset_id}", response_model=ServerAssetResponse)
def update_server(asset_id: int, payload: ServerAssetUpdate, db: Session = Depends(get_db)):
    s = db.query(ServerAsset).get(asset_id)
    if not s:
        raise HTTPException(status_code=404, detail="资产不存在")
    data = payload.model_dump(exclude_unset=True)
    rack_id = data.get("rack_id", s.rack_id)
    u_start = data.get("u_start", s.u_start)
    u_size = data.get("u_size", s.u_size)
    additional_ips = data.pop("additional_ips", None)
    _validate_server(db, rack_id, u_start, u_size, asset_id)
    for key, value in data.items():
        if value is None:
            continue
        if key == "status":
            value = _valid(value, ASSET_STATUSES, s.status)
        elif key == "category":
            value = (value or "").strip() or s.category
        elif key == "u_start":
            value = value if value and value >= 1 else s.u_start
        elif key == "u_size":
            value = value if value and value >= 1 else s.u_size
        elif isinstance(value, str):
            value = value.strip()
        setattr(s, key, value)
    db.flush()
    if additional_ips is not None:
        _set_server_ips(db, s, additional_ips)
    db.commit()
    db.refresh(s)
    return _server_response(db, s)


@router.delete("/servers/{asset_id}")
def delete_server(asset_id: int, db: Session = Depends(get_db)):
    from app.models import VMInstance
    if db.query(VMInstance).filter_by(host_id=asset_id).first():
        raise HTTPException(409, '硬件仍关联虚拟机，请先调整虚拟机宿主关系')
    s = db.query(ServerAsset).get(asset_id)
    if not s:
        raise HTTPException(status_code=404, detail="资产不存在")
    db.delete(s)
    db.commit()
    return {"message": "已删除"}


# ===========================================================================
# 服务器 / 存储「类别」可维护列表（用户自定义任意名称）
# ===========================================================================
@router.get("/server-categories", response_model=list[ServerCategorySchema])
def list_server_categories(db: Session = Depends(get_db)):
    return db.query(ServerCategory).order_by(ServerCategory.id.asc()).all()


@router.post("/server-categories", response_model=ServerCategorySchema)
def create_server_category(payload: ServerCategoryCreate, db: Session = Depends(get_db)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="类别名称不能为空")
    if len(name) > 50:
        raise HTTPException(status_code=400, detail="类别名称不能超过 50 个字符")
    if db.query(ServerCategory).filter(ServerCategory.name == name).first():
        raise HTTPException(status_code=409, detail=f"类别「{name}」已存在")
    c = ServerCategory(name=name, color=_category_color(payload.color))
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.put("/server-categories/{cat_id}", response_model=ServerCategorySchema)
def update_server_category(cat_id: int, payload: ServerCategoryUpdate, db: Session = Depends(get_db)):
    c = db.query(ServerCategory).get(cat_id)
    if not c:
        raise HTTPException(status_code=404, detail="类别不存在")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="类别名称不能为空")
        if len(name) > 50:
            raise HTTPException(status_code=400, detail="类别名称不能超过 50 个字符")
        if name != c.name and db.query(ServerCategory).filter(ServerCategory.name == name).first():
            raise HTTPException(status_code=409, detail=f"类别「{name}」已存在")
        # 同步更新引用该类别的资产（category 为名称字符串）
        db.query(ServerAsset).filter(ServerAsset.category == c.name).update(
            {ServerAsset.category: name}
        )
        c.name = name
    if payload.color is not None:
        c.color = _category_color(payload.color)
    db.commit()
    db.refresh(c)
    return c


@router.delete("/server-categories/{cat_id}")
def delete_server_category(cat_id: int, db: Session = Depends(get_db)):
    c = db.query(ServerCategory).get(cat_id)
    if not c:
        raise HTTPException(status_code=404, detail="类别不存在")
    used = db.query(ServerAsset).filter(ServerAsset.category == c.name).count()
    if used > 0:
        raise HTTPException(
            status_code=409,
            detail=f"该类别正在被 {used} 条服务器/存储使用，请先在资产中改换类别后再删除",
        )
    db.delete(c)
    db.commit()
    return {"message": "已删除"}


def _validate_server(db: Session, rack_id, u_start, u_size, ignore_id):
    """校验关联机柜存在、U 位不越界、且与同机柜其他资产不重叠。"""
    rack = db.query(DCRack).get(rack_id)
    if not rack:
        raise HTTPException(status_code=400, detail="关联机柜不存在")
    u = rack.u_height or 42
    us = u_start or 1
    sz = u_size or 1
    if us < 1:
        raise HTTPException(status_code=400, detail="起始U必须 >= 1")
    if sz < 1:
        raise HTTPException(status_code=400, detail="占用U数必须 >= 1")
    if us + sz - 1 > u:
        raise HTTPException(
            status_code=400,
            detail=f"U位越界：起始U {us} + 占用 {sz} - 1 = {us + sz - 1} 超过机柜 {u}U",
        )
    # 重叠检测（忽略自身）
    lo, hi = us, us + sz - 1
    others = db.query(ServerAsset).filter(ServerAsset.rack_id == rack_id)
    if ignore_id is not None:
        others = others.filter(ServerAsset.id != ignore_id)
    for o in others.all():
        o_lo, o_hi = o.u_start, o.u_start + (o.u_size or 1) - 1
        if not (hi < o_lo or lo > o_hi):
            raise HTTPException(
                status_code=409,
                detail=f"与已有资产「{o.name}」(U{o_lo}-U{o_hi}) 位置重叠",
            )


def _server_response(db: Session, s: ServerAsset) -> ServerAssetResponse:
    rack = db.query(DCRack).get(s.rack_id)
    rack_number = None
    site_name = None
    if rack:
        rack_number = rack.rack_number
        site = db.query(DCSite).get(rack.site_id)
        site_name = site.name if site else None
    return ServerAssetResponse(
        id=s.id,
        name=s.name or "",
        category=s.category or "服务器",
        brand=s.brand or "",
        model=s.model or "",
        serial=s.serial or "",
        asset_tag=s.asset_tag or "",
        rack_id=s.rack_id,
        rack_number=rack_number or "",
        site_name=site_name or "",
        u_start=s.u_start or 1,
        u_size=s.u_size or 1,
        status=s.status or "在用",
        management_ip=s.management_ip or "",
        additional_ips=_server_ip_list(s),
        os=s.os or "",
        cpu=s.cpu or "",
        memory=s.memory or "",
        storage_desc=s.storage_desc or "",
        owner=s.owner or "",
        notes=s.notes or "",
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


# ===========================================================================
# 汇总（仪表盘用）
# ===========================================================================
@router.get("/summary", response_model=AssetSummary)
def asset_summary(db: Session = Depends(get_db)):
    it_count = db.query(func.count(ITAsset.id)).scalar() or 0
    server_count = db.query(func.count(ServerAsset.id)).scalar() or 0
    it_by_type = {}
    for t in IT_TYPES:
        c = db.query(func.count(ITAsset.id)).filter(ITAsset.asset_type == t).scalar() or 0
        if c:
            it_by_type[t] = c
    for t, c in db.query(ITAsset.asset_type, func.count(ITAsset.id)).group_by(ITAsset.asset_type).all():
        key = t or "其他"
        if key not in it_by_type:
            it_by_type[key] = c
    server_by_category = {}
    for cat, c in db.query(ServerAsset.category, func.count(ServerAsset.id)).group_by(ServerAsset.category).all():
        server_by_category[cat or "其他"] = c
    return AssetSummary(
        it_count=it_count,
        server_count=server_count,
        it_by_type=it_by_type,
        server_by_category=server_by_category,
    )
