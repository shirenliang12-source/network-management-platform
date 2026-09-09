"""IP 地址规划 (IPAM) API routes — NetBox 风格三层结构：聚合 -> 网段 -> IP 地址。"""
import csv
import io
import ipaddress
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from app.api_models import StrictRequest
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import IPAMAggregate, IPAMPrefix, IPAMIPAddress, Device
from app.services.device_ip_link import build_ip_to_device_map, device_ip_entries
from app.schemas import (
    IPAMAggregateCreate,
    IPAMAggregateUpdate,
    IPAMAggregateResponse,
    IPAMPrefixCreate,
    IPAMPrefixUpdate,
    IPAMPrefixResponse,
    IPAMIPAddressCreate,
    IPAMIPAddressUpdate,
    IPAMIPAddressResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ipam", tags=["ipam"])

# 统一四态：规划 / 预分配 / 使用中 / 已停用
IPAM_STATUSES = ["规划", "预分配", "使用中", "已停用"]
# IP 分配类型：静态（手配、需登记使用设备）/ DHCP（自动分配、不做后续统计）
IPAM_ALLOCATION_TYPES = ["静态", "DHCP"]

# 批量生成整个网段 IP 时的最大数量（避免 /16 等大网段把数据库撑爆）。
MAX_BULK_IPS = 4096


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _prefix_counts(cidr: str):
    """返回 (total, usable) 地址数。usable = total - 2（/31、/32 特殊）。"""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except (ValueError, TypeError):
        return 0, 0
    total = net.num_addresses
    if total <= 2:
        usable = total
    else:
        usable = total - 2
    return total, usable


def _valid_status(value: str) -> str:
    return value if value in IPAM_STATUSES else "规划"


def _valid_allocation(value: str) -> str:
    return value if value in IPAM_ALLOCATION_TYPES else "静态"


def _device_name(db: Session, device_id):
    if not device_id:
        return None
    dev = db.query(Device).get(device_id)
    return dev.name if dev else None


# ---------------------------------------------------------------------------
# Aggregates (聚合)
# ---------------------------------------------------------------------------
@router.get("/aggregates", response_model=list[IPAMAggregateResponse])
def list_aggregates(db: Session = Depends(get_db), search: str = Query("", description="搜索名称/前缀/描述")):
    q = db.query(IPAMAggregate)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (IPAMAggregate.name.ilike(like))
            | (IPAMAggregate.prefix.ilike(like))
            | (IPAMAggregate.description.ilike(like))
        )
    items = q.order_by(IPAMAggregate.name.asc()).all()
    return [_aggregate_response(db, a) for a in items]


@router.get("/aggregates/{agg_id}", response_model=IPAMAggregateResponse)
def get_aggregate(agg_id: int, db: Session = Depends(get_db)):
    agg = db.query(IPAMAggregate).get(agg_id)
    if not agg:
        raise HTTPException(status_code=404, detail="聚合不存在")
    return _aggregate_response(db, agg)


@router.post("/aggregates", response_model=IPAMAggregateResponse)
def create_aggregate(payload: IPAMAggregateCreate, db: Session = Depends(get_db)):
    try:
        ipaddress.ip_network(payload.prefix, strict=False)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="前缀格式不正确，示例：10.20.0.0/16")
    agg = IPAMAggregate(
        name=payload.name or "",
        prefix=payload.prefix or "",
        description=payload.description or "",
        date_added=payload.date_added or "",
    )
    db.add(agg)
    db.commit()
    db.refresh(agg)
    return _aggregate_response(db, agg)


@router.put("/aggregates/{agg_id}", response_model=IPAMAggregateResponse)
def update_aggregate(agg_id: int, payload: IPAMAggregateUpdate, db: Session = Depends(get_db)):
    agg = db.query(IPAMAggregate).get(agg_id)
    if not agg:
        raise HTTPException(status_code=404, detail="聚合不存在")
    data = payload.model_dump(exclude_unset=True)
    if "prefix" in data and data["prefix"]:
        try:
            ipaddress.ip_network(data["prefix"], strict=False)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="前缀格式不正确，示例：10.20.0.0/16")
    for key, value in data.items():
        setattr(agg, key, value if value is not None else "")
    db.commit()
    db.refresh(agg)
    return _aggregate_response(db, agg)


@router.delete("/aggregates/{agg_id}")
def delete_aggregate(agg_id: int, db: Session = Depends(get_db)):
    agg = db.query(IPAMAggregate).get(agg_id)
    if not agg:
        raise HTTPException(status_code=404, detail="聚合不存在")
    # 级联删除其下所有网段与 IP（避免孤儿数据）
    prefixes = db.query(IPAMPrefix).filter(IPAMPrefix.aggregate_id == agg_id).all()
    for p in prefixes:
        db.query(IPAMIPAddress).filter(IPAMIPAddress.prefix_id == p.id).delete()
    db.query(IPAMPrefix).filter(IPAMPrefix.aggregate_id == agg_id).delete()
    db.delete(agg)
    db.commit()
    return {"message": "已删除"}


def _aggregate_response(db: Session, agg: IPAMAggregate) -> IPAMAggregateResponse:
    prefixes = db.query(IPAMPrefix).filter(IPAMPrefix.aggregate_id == agg.id).all()
    pids = [p.id for p in prefixes]
    ip_count = 0
    allocated = 0
    if pids:
        ip_count = db.query(func.count(IPAMIPAddress.id)).filter(IPAMIPAddress.prefix_id.in_(pids)).scalar() or 0
        allocated = db.query(func.count(IPAMIPAddress.id)).filter(
            IPAMIPAddress.prefix_id.in_(pids), IPAMIPAddress.status == "使用中"
        ).scalar() or 0
    # 聚合利用率 = 已用 IP / 聚合可用地址
    _, usable = _prefix_counts(agg.prefix)
    utilization = round(allocated / usable * 100, 2) if usable else 0.0
    return IPAMAggregateResponse(
        id=agg.id,
        name=agg.name or "",
        prefix=agg.prefix or "",
        description=agg.description or "",
        date_added=agg.date_added or "",
        prefix_count=len(prefixes),
        ip_count=ip_count,
        utilization=min(utilization, 100.0),
        created_at=agg.created_at,
        updated_at=agg.updated_at,
    )


# ---------------------------------------------------------------------------
# Prefixes (网段)
# ---------------------------------------------------------------------------
@router.get("/prefixes", response_model=list[IPAMPrefixResponse])
def list_prefixes(
    db: Session = Depends(get_db),
    aggregate_id: int = Query(None),
    parent_id: int = Query(None),
    status: str = Query(""),
    search: str = Query(""),
):
    q = db.query(IPAMPrefix)
    if aggregate_id is not None:
        q = q.filter(IPAMPrefix.aggregate_id == aggregate_id)
    if parent_id is not None:
        q = q.filter(IPAMPrefix.parent_id == parent_id)
    if status:
        q = q.filter(IPAMPrefix.status == status)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (IPAMPrefix.prefix.ilike(like))
            | (IPAMPrefix.role.ilike(like))
            | (IPAMPrefix.vlan.ilike(like))
            | (IPAMPrefix.company.ilike(like))
            | (IPAMPrefix.firewall.ilike(like))
            | (IPAMPrefix.zone_interface_name.ilike(like))
            | (IPAMPrefix.description.ilike(like))
        )
    items = q.order_by(IPAMPrefix.prefix.asc()).all()
    return [_prefix_response(db, p) for p in items]


@router.get("/prefixes/{prefix_id}", response_model=IPAMPrefixResponse)
def get_prefix(prefix_id: int, db: Session = Depends(get_db)):
    p = db.query(IPAMPrefix).get(prefix_id)
    if not p:
        raise HTTPException(status_code=404, detail="网段不存在")
    return _prefix_response(db, p)


@router.post("/prefixes", response_model=IPAMPrefixResponse)
def create_prefix(payload: IPAMPrefixCreate, db: Session = Depends(get_db)):
    try:
        ipaddress.ip_network(payload.prefix, strict=False)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="网段前缀格式不正确，示例：10.20.1.0/24")
    # 选了父网段则继承其所属聚合
    agg_id = payload.aggregate_id
    if payload.parent_id:
        parent = db.query(IPAMPrefix).get(payload.parent_id)
        if not parent:
            raise HTTPException(status_code=400, detail="父网段不存在")
        agg_id = agg_id or parent.aggregate_id
    prefix = IPAMPrefix(
        aggregate_id=agg_id,
        parent_id=payload.parent_id if payload.parent_id else None,
        prefix=payload.prefix or "",
        status=_valid_status(payload.status),
        role=payload.role or "",
        vlan=payload.vlan or "",
        company=payload.company or "",
        firewall=payload.firewall or "",
        zone_interface_name=payload.zone_interface_name or "",
        description=payload.description or "",
        is_pool=bool(payload.is_pool),
    )
    db.add(prefix)
    db.commit()
    db.refresh(prefix)
    return _prefix_response(db, prefix)


@router.put("/prefixes/{prefix_id}", response_model=IPAMPrefixResponse)
def update_prefix(prefix_id: int, payload: IPAMPrefixUpdate, db: Session = Depends(get_db)):
    p = db.query(IPAMPrefix).get(prefix_id)
    if not p:
        raise HTTPException(status_code=404, detail="网段不存在")
    data = payload.model_dump(exclude_unset=True)
    if "prefix" in data and data["prefix"]:
        try:
            ipaddress.ip_network(data["prefix"], strict=False)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="网段前缀格式不正确，示例：10.20.1.0/24")
    if "parent_id" in data and data["parent_id"]:
        parent = db.query(IPAMPrefix).get(data["parent_id"])
        if not parent:
            raise HTTPException(status_code=400, detail="父网段不存在")
        if data["parent_id"] == prefix_id:
            raise HTTPException(status_code=400, detail="不能把网段设为自己的父级")
        data["aggregate_id"] = data.get("aggregate_id") or parent.aggregate_id
    for key, value in data.items():
        if value is None:
            continue
        setattr(p, key, value)
    db.commit()
    db.refresh(p)
    return _prefix_response(db, p)


@router.delete("/prefixes/{prefix_id}")
def delete_prefix(prefix_id: int, db: Session = Depends(get_db)):
    p = db.query(IPAMPrefix).get(prefix_id)
    if not p:
        raise HTTPException(status_code=404, detail="网段不存在")
    db.query(IPAMIPAddress).filter(IPAMIPAddress.prefix_id == prefix_id).delete()
    db.query(IPAMPrefix).filter(IPAMPrefix.parent_id == prefix_id).delete()
    db.delete(p)
    db.commit()
    return {"message": "已删除"}


def _prefix_response(db: Session, p: IPAMPrefix) -> IPAMPrefixResponse:
    total, usable = _prefix_counts(p.prefix)
    allocated = db.query(func.count(IPAMIPAddress.id)).filter(IPAMIPAddress.prefix_id == p.id).scalar() or 0
    in_use = db.query(func.count(IPAMIPAddress.id)).filter(
        IPAMIPAddress.prefix_id == p.id, IPAMIPAddress.status == "使用中"
    ).scalar() or 0
    child_count = db.query(func.count(IPAMPrefix.id)).filter(IPAMPrefix.parent_id == p.id).scalar() or 0
    # 静态 / DHCP 分配统计：静态 IP 按是否绑定设备区分已用/未用；DHCP 单独计数、不做设备统计
    all_ips = db.query(IPAMIPAddress).filter(IPAMIPAddress.prefix_id == p.id).all()
    static_ips = [x for x in all_ips if (x.allocation_type or "静态") == "静态"]
    static_used = sum(1 for x in static_ips if x.assigned_device_id)
    static_unused = len(static_ips) - static_used
    dhcp_count = sum(1 for x in all_ips if (x.allocation_type or "静态") == "DHCP")
    device_list = []
    for x in static_ips:
        if x.assigned_device_id:
            dev = db.query(Device).get(x.assigned_device_id)
            device_list.append({
                "device_id": x.assigned_device_id,
                "device_name": dev.name if dev else (x.device_name or "未知设备"),
                "device_model": x.device_model or (dev.device_type if dev else ""),
                "device_ip": x.device_ip or x.address or "",
                "device_ips": device_ip_entries(dev) if dev else [],
                "address": x.address or "",
                "description": x.description or "",
            })
        elif x.device_name:
            device_list.append({
                "device_id": None,
                "device_name": x.device_name,
                "device_model": x.device_model or "",
                "device_ip": x.device_ip or x.address or "",
                "device_ips": [],
                "address": x.address or "",
                "description": x.description or "",
            })
    utilization = round(allocated / usable * 100, 2) if usable else 0.0
    return IPAMPrefixResponse(
        id=p.id,
        aggregate_id=p.aggregate_id,
        parent_id=p.parent_id,
        prefix=p.prefix or "",
        status=p.status or "规划",
        role=p.role or "",
        vlan=p.vlan or "",
        company=p.company or "",
        firewall=p.firewall or "",
        zone_interface_name=p.zone_interface_name or "",
        description=p.description or "",
        is_pool=bool(p.is_pool),
        total_ips=total,
        usable_ips=usable,
        allocated_ips=allocated,
        in_use_ips=in_use,
        utilization=min(utilization, 100.0),
        child_count=child_count,
        static_used=static_used,
        static_unused=static_unused,
        dhcp_count=dhcp_count,
        static_device_list=device_list,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# ---------------------------------------------------------------------------
# Tree (聚合 -> 网段 -> 子网段，递归层级)
# ---------------------------------------------------------------------------
@router.get("/tree")
def ipam_tree(db: Session = Depends(get_db)):
    """返回 聚合 -> 网段(含递归子网段) 的层级结构，用于概览树与网段树视图。"""
    aggregates = db.query(IPAMAggregate).order_by(IPAMAggregate.name.asc()).all()
    all_prefixes = db.query(IPAMPrefix).all()
    by_parent = {}
    for p in all_prefixes:
        by_parent.setdefault(p.parent_id, []).append(p)
    by_agg = {}
    for p in all_prefixes:
        if p.aggregate_id:
            by_agg.setdefault(p.aggregate_id, []).append(p.id)

    def build_prefix_node(p):
        node = _prefix_response(db, p)
        node_dict = node.model_dump()
        children = by_parent.get(p.id, [])
        node_dict["children"] = [build_prefix_node(c) for c in children]
        return node_dict

    result = []
    for agg in aggregates:
        agg_resp = _aggregate_response(db, agg)
        agg_dict = agg_resp.model_dump()
        # 顶层网段 = parent_id 为空 且 属于该聚合
        top = [p for p in all_prefixes if p.aggregate_id == agg.id and (p.parent_id is None)]
        agg_dict["children"] = [build_prefix_node(p) for p in top]
        result.append(agg_dict)
    return result


# ---------------------------------------------------------------------------
# IP Addresses (IP 地址)
# ---------------------------------------------------------------------------
@router.get("/ips", response_model=list[IPAMIPAddressResponse])
def list_ips(
    db: Session = Depends(get_db),
    prefix_id: int = Query(None),
    status: str = Query(""),
    allocation_type: str = Query(""),
    search: str = Query(""),
):
    q = db.query(IPAMIPAddress)
    if prefix_id is not None:
        q = q.filter(IPAMIPAddress.prefix_id == prefix_id)
    if status:
        q = q.filter(IPAMIPAddress.status == status)
    if allocation_type:
        q = q.filter(IPAMIPAddress.allocation_type == allocation_type)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (IPAMIPAddress.address.ilike(like))
            | (IPAMIPAddress.dns_name.ilike(like))
            | (IPAMIPAddress.description.ilike(like))
        )
    items = q.order_by(IPAMIPAddress.address.asc()).all()
    ip_map = build_ip_to_device_map(db)
    return [_ip_response(db, ip, ip_map) for ip in items]


@router.get("/ips/{ip_id}", response_model=IPAMIPAddressResponse)
def get_ip(ip_id: int, db: Session = Depends(get_db)):
    ip = db.query(IPAMIPAddress).get(ip_id)
    if not ip:
        raise HTTPException(status_code=404, detail="IP 不存在")
    return _ip_response(db, ip)


@router.post("/ips", response_model=IPAMIPAddressResponse)
def create_ip(payload: IPAMIPAddressCreate, db: Session = Depends(get_db)):
    prefix = db.query(IPAMPrefix).get(payload.prefix_id)
    if not prefix:
        raise HTTPException(status_code=400, detail="所属网段不存在")
    try:
        ip_obj = ipaddress.ip_address(payload.address)
        net = ipaddress.ip_network(prefix.prefix, strict=False)
        if ip_obj not in net:
            raise HTTPException(status_code=400, detail=f"IP {payload.address} 不在网段 {prefix.prefix} 范围内")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="IP 地址格式不正确")
    # 去重：同一网段下相同 IP 不重复添加
    existing = (
        db.query(IPAMIPAddress)
        .filter(IPAMIPAddress.prefix_id == payload.prefix_id, IPAMIPAddress.address == payload.address)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="该网段下已存在此 IP")
    allocation = _valid_allocation(payload.allocation_type)
    # DHCP 地址不做静态设备绑定，清空关联设备
    assigned = None if allocation == "DHCP" else (payload.assigned_device_id if payload.assigned_device_id else None)
    ip = IPAMIPAddress(
        prefix_id=payload.prefix_id,
        address=payload.address or "",
        status=_valid_status(payload.status),
        allocation_type=allocation,
        dns_name=payload.dns_name or "",
        description=payload.description or "",
        assigned_device_id=assigned,
        device_name=payload.device_name or "",
        device_model=payload.device_model or "",
        device_ip=payload.device_ip or "",
    )
    db.add(ip)
    db.commit()
    db.refresh(ip)
    return _ip_response(db, ip)


@router.put("/ips/{ip_id}", response_model=IPAMIPAddressResponse)
def update_ip(ip_id: int, payload: IPAMIPAddressUpdate, db: Session = Depends(get_db)):
    ip = db.query(IPAMIPAddress).get(ip_id)
    if not ip:
        raise HTTPException(status_code=404, detail="IP 不存在")
    data = payload.model_dump(exclude_unset=True)
    if "address" in data and data["address"]:
        prefix = db.query(IPAMPrefix).get(ip.prefix_id)
        try:
            ip_obj = ipaddress.ip_address(data["address"])
            net = ipaddress.ip_network(prefix.prefix, strict=False)
            if ip_obj not in net:
                raise HTTPException(status_code=400, detail=f"IP 不在网段 {prefix.prefix} 范围内")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="IP 地址格式不正确")
    # 分配类型规范化；DHCP 时清空关联设备
    if "allocation_type" in data:
        data["allocation_type"] = _valid_allocation(data["allocation_type"])
        if data["allocation_type"] == "DHCP":
            data["assigned_device_id"] = None
    for key, value in data.items():
        # 关联设备为可空外键，None 应保持为 None（清空），不能变成空字符串
        if key == "assigned_device_id":
            setattr(ip, key, value)
        else:
            setattr(ip, key, value if value is not None else "")
    db.commit()
    db.refresh(ip)
    return _ip_response(db, ip)


@router.delete("/ips/{ip_id}")
def delete_ip(ip_id: int, db: Session = Depends(get_db)):
    ip = db.query(IPAMIPAddress).get(ip_id)
    if not ip:
        raise HTTPException(status_code=404, detail="IP 不存在")
    db.delete(ip)
    db.commit()
    return {"message": "已删除"}


@router.post("/prefixes/{prefix_id}/generate", response_model=dict)
def generate_ips(prefix_id: int, db: Session = Depends(get_db)):
    """批量生成「整个网段」的可用 IP（默认状态=规划），已存在的跳过。

    大网段（> MAX_BULK_IPS）会被拒绝，避免一次性写入过多数据。
    """
    prefix = db.query(IPAMPrefix).get(prefix_id)
    if not prefix:
        raise HTTPException(status_code=404, detail="网段不存在")
    try:
        net = ipaddress.ip_network(prefix.prefix, strict=False)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="网段前缀格式不正确")
    hosts = list(net.hosts())
    if len(hosts) > MAX_BULK_IPS:
        raise HTTPException(
            status_code=400,
            detail=f"网段可用 IP 过多（{len(hosts)} 个），请缩小到 /{32 - (len(hosts)+2).bit_length()} 以内或逐条添加",
        )
    existing = {
        r[0]
        for r in db.query(IPAMIPAddress.address)
        .filter(IPAMIPAddress.prefix_id == prefix_id)
        .all()
    }
    created = 0
    skipped = 0
    for host in hosts:
        addr = str(host)
        if addr in existing:
            skipped += 1
            continue
        db.add(IPAMIPAddress(
            prefix_id=prefix_id,
            address=addr,
            status="规划",
            dns_name="",
            description="",
        ))
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped}


def _ip_response(db: Session, ip: IPAMIPAddress, ip_map: dict = None) -> IPAMIPAddressResponse:
    # 设备名称优先取「关联设备」名称，未关联时回退到手工录入的名称
    assigned_name = _device_name(db, ip.assigned_device_id)
    display_name = assigned_name or (ip.device_name or "")
    device_ips = []
    primary_ip = ""
    reverse_linked = False
    dev = db.query(Device).get(ip.assigned_device_id) if ip.assigned_device_id else None
    # 双向反查：未显式关联设备时，用 IP 地址匹配设备的「主IP + 额外IP」
    if dev is None:
        if ip_map is None:
            ip_map = build_ip_to_device_map(db)
        dev = ip_map.get((ip.address or "").strip())
        if dev is not None:
            reverse_linked = True
    if dev is not None:
        device_ips = device_ip_entries(dev)
        primary_ip = dev.ip_address or ""
        if reverse_linked and not assigned_name:
            assigned_name = dev.name
            display_name = dev.name
    return IPAMIPAddressResponse(
        id=ip.id,
        prefix_id=ip.prefix_id,
        address=ip.address or "",
        status=ip.status or "规划",
        allocation_type=ip.allocation_type or "静态",
        dns_name=ip.dns_name or "",
        description=ip.description or "",
        assigned_device_id=ip.assigned_device_id if ip.assigned_device_id else (dev.id if reverse_linked else None),
        assigned_device_name=display_name or None,
        device_name=ip.device_name or "",
        device_model=ip.device_model or "",
        device_ip=ip.device_ip or "",
        primary_ip=primary_ip,
        device_ips=device_ips,
        reverse_linked=reverse_linked,
        created_at=ip.created_at,
        updated_at=ip.updated_at,
    )


# ---------------------------------------------------------------------------
# Devices picker (关联设备下拉)
# ---------------------------------------------------------------------------
@router.get("/devices")
def list_devices_for_select(db: Session = Depends(get_db)):
    devices = db.query(Device).order_by(Device.name).all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "ip_address": d.ip_address,
            "ip_addresses": [e["ip"] for e in device_ip_entries(d)],
        }
        for d in devices
    ]


# ---------------------------------------------------------------------------
# Import (CSV 批量导入：聚合 / 网段 / IP 地址)
# ---------------------------------------------------------------------------
class _ImportPayload(StrictRequest):
    csv: str  # raw CSV text (supports BOM / quoted fields)


def _clean_csv(raw: str) -> str:
    if raw and raw.startswith("\ufeff"):
        raw = raw[1:]
    return raw or ""


_PAREN = __import__("re").compile(r"[（(][^）)]*[）)]")


def _header_variants(h):
    """生成表头的所有候选归一化形态，兼容「前缀(CIDR)」「ip 地址」等写法。"""
    h = (h or "").strip().lower()
    if not h:
        return set()
    ns = h.replace(" ", "")
    dp = _PAREN.sub("", h)
    dp_ns = dp.replace(" ", "")
    return {h, ns, dp, dp_ns}


def _build_norm_map(mapping):
    norm = {}
    for k, v in mapping.items():
        for variant in _header_variants(k):
            norm[variant] = v
    return norm


def _map_headers(header, mapping):
    """把 CSV 表头映射到内部字段。兼容中文/英文、括号注解、空格等写法。"""
    norm = _build_norm_map(mapping)
    col_index = {}
    for idx, h in enumerate(header):
        if not h:
            continue
        for variant in _header_variants(h):
            mapped = norm.get(variant)
            if mapped and mapped not in col_index:
                col_index[mapped] = idx
                break
    return col_index


def _resolve_aggregate(db, agg_ref, created_aggs, agg_by_name, agg_by_prefix, errors, lineno):
    """按名称或 CIDR 匹配聚合；找不到时若 agg_ref 是合法 CIDR 则自动创建。返回 agg_id 或 None。"""
    if not agg_ref:
        return None
    if agg_ref in agg_by_name:
        return agg_by_name[agg_ref]
    if agg_ref in agg_by_prefix:
        return agg_by_prefix[agg_ref]
    try:
        ipaddress.ip_network(agg_ref, strict=False)
        agg = IPAMAggregate(name=agg_ref, prefix=agg_ref, description="由导入自动创建")
        db.add(agg)
        db.flush()
        agg_by_name[agg_ref] = agg.id
        agg_by_prefix[agg_ref] = agg.id
        created_aggs.append(agg_ref)
        return agg.id
    except (ValueError, TypeError):
        errors.append(f"第 {lineno} 行：聚合「{agg_ref}」在库中不存在且不是合法 CIDR，网段将不关联聚合")
        return None


AGG_IMPORT_MAP = {
    "名称": "name", "name": "name", "聚合名称": "name",
    "前缀": "prefix", "prefix": "prefix", "cidr": "prefix", "网段前缀": "prefix",
    "规划日期": "date_added", "date_added": "date_added", "date": "date_added",
    "描述": "description", "description": "description", "备注": "description",
}


@router.post("/aggregates/import")
def import_aggregates(payload: _ImportPayload, db: Session = Depends(get_db)):
    """批量导入聚合（大子网）。表头自动识别，前缀重复自动跳过。"""
    raw = _clean_csv(payload.csv)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="CSV 内容为空")
    rows = [r for r in csv.reader(io.StringIO(raw))]
    if not rows:
        raise HTTPException(status_code=400, detail="CSV 没有可读的行")
    col_index = _map_headers(rows[0], AGG_IMPORT_MAP)
    if not col_index:
        raise HTTPException(status_code=400, detail="未识别到已知列（需包含 名称 / 前缀(CIDR) 之一）")
    existing = {a.prefix for a in db.query(IPAMAggregate.prefix).all()}
    created = 0
    skipped = 0
    errors = []
    for lineno, row in enumerate(rows[1:], start=2):
        if not row or all((c or "").strip() == "" for c in row):
            skipped += 1
            continue
        data = {f: (row[i].strip() if i < len(row) else "") for f, i in col_index.items()}
        name = data.get("name")
        prefix = data.get("prefix")
        if not name or not prefix:
            skipped += 1
            continue
        if prefix in existing:
            skipped += 1
            continue
        try:
            ipaddress.ip_network(prefix, strict=False)
        except (ValueError, TypeError):
            errors.append(f"第 {lineno} 行：前缀「{prefix}」格式不正确，已跳过")
            skipped += 1
            continue
        db.add(IPAMAggregate(
            name=name, prefix=prefix,
            description=data.get("description", ""), date_added=data.get("date_added", ""),
        ))
        existing.add(prefix)
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors[:50]}


PREFIX_IMPORT_MAP = {
    "网段": "prefix", "prefix": "prefix", "cidr": "prefix", "subnet": "prefix",
    "聚合": "aggregate", "aggregate": "aggregate",
    "状态": "status", "status": "status",
    "用途": "role", "role": "role", "角色": "role",
    "vlan": "vlan", "vlanid": "vlan", "vlan id": "vlan",
    "公司": "company", "company": "company",
    "firewall": "firewall",
    "zone": "zone_interface_name", "zone/interface name": "zone_interface_name",
    "zone_interface_name": "zone_interface_name",
    "描述": "description", "description": "description", "备注": "description",
    "分配池": "is_pool", "is_pool": "is_pool",
}


@router.post("/prefixes/import")
def import_prefixes(payload: _ImportPayload, db: Session = Depends(get_db)):
    """批量导入网段。聚合列可写名称或 CIDR，找不到时若是 CIDR 自动建聚合。"""
    raw = _clean_csv(payload.csv)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="CSV 内容为空")
    rows = [r for r in csv.reader(io.StringIO(raw))]
    if not rows:
        raise HTTPException(status_code=400, detail="CSV 没有可读的行")
    col_index = _map_headers(rows[0], PREFIX_IMPORT_MAP)
    if not col_index:
        raise HTTPException(status_code=400, detail="未识别到已知列（需包含 网段(CIDR) 之一）")
    aggs = db.query(IPAMAggregate).all()
    agg_by_name = {a.name: a.id for a in aggs}
    agg_by_prefix = {a.prefix: a.id for a in aggs}
    created_aggs = []
    existing_pfx = {p.prefix for p in db.query(IPAMPrefix.prefix).all()}
    created = 0
    skipped = 0
    errors = []
    for lineno, row in enumerate(rows[1:], start=2):
        if not row or all((c or "").strip() == "" for c in row):
            skipped += 1
            continue
        data = {f: (row[i].strip() if i < len(row) else "") for f, i in col_index.items()}
        prefix = data.get("prefix")
        if not prefix:
            skipped += 1
            continue
        if prefix in existing_pfx:
            skipped += 1
            continue
        try:
            ipaddress.ip_network(prefix, strict=False)
        except (ValueError, TypeError):
            errors.append(f"第 {lineno} 行：网段「{prefix}」格式不正确，已跳过")
            skipped += 1
            continue
        agg_id = _resolve_aggregate(db, data.get("aggregate"), created_aggs, agg_by_name, agg_by_prefix, errors, lineno)
        pool = data.get("is_pool")
        is_pool = str(pool).lower() in ("1", "true", "yes", "y", "是", "分配池")
        db.add(IPAMPrefix(
            aggregate_id=agg_id,
            prefix=prefix,
            status=_valid_status(data.get("status", "规划")),
            role=data.get("role", ""),
            vlan=data.get("vlan", ""),
            company=data.get("company", ""),
            firewall=data.get("firewall", ""),
            zone_interface_name=data.get("zone_interface_name", ""),
            description=data.get("description", ""),
            is_pool=is_pool,
        ))
        existing_pfx.add(prefix)
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped, "created_aggregates": created_aggs, "errors": errors[:50]}


IP_IMPORT_MAP = {
    "网段": "prefix", "prefix": "prefix", "cidr": "prefix", "subnet": "prefix",
    "ip地址": "address", "ip 地址": "address", "ip": "address", "address": "address",
    "状态": "status", "status": "status",
    "分配类型": "allocation_type", "allocation_type": "allocation_type", "类型": "allocation_type",
    "dns名称": "dns_name", "dns": "dns_name", "dns_name": "dns_name",
    "描述": "description", "description": "description", "备注": "description",
    "设备": "device", "关联设备": "device", "device": "device",
    "设备名称": "device_name", "设备名": "device_name", "devicename": "device_name", "主机名": "device_name",
    "设备型号": "device_model", "型号": "device_model", "model": "device_model",
    "设备ip": "device_ip", "设备ip地址": "device_ip", "deviceip": "device_ip",
    "聚合": "aggregate", "aggregate": "aggregate",
}


@router.post("/ips/import")
def import_ips(payload: _ImportPayload, db: Session = Depends(get_db)):
    """批量导入 IP 地址。网段不存在则自动创建；关联设备按名称匹配；分配类型默认静态。"""
    raw = _clean_csv(payload.csv)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="CSV 内容为空")
    rows = [r for r in csv.reader(io.StringIO(raw))]
    if not rows:
        raise HTTPException(status_code=400, detail="CSV 没有可读的行")
    col_index = _map_headers(rows[0], IP_IMPORT_MAP)
    if not col_index:
        raise HTTPException(status_code=400, detail="未识别到已知列（需包含 网段(CIDR) / IP地址 之一）")
    aggs = db.query(IPAMAggregate).all()
    agg_by_name = {a.name: a.id for a in aggs}
    agg_by_prefix = {a.prefix: a.id for a in aggs}
    created_aggs = []
    pfx_by_cidr = {p.prefix: p.id for p in db.query(IPAMPrefix).all()}
    devs = db.query(Device).all()
    dev_by_name = {d.name: d.id for d in devs}
    seen = set(
        (pid, addr)
        for pid, addr in db.query(IPAMIPAddress.prefix_id, IPAMIPAddress.address).all()
    )
    created = 0
    skipped = 0
    errors = []
    for lineno, row in enumerate(rows[1:], start=2):
        if not row or all((c or "").strip() == "" for c in row):
            skipped += 1
            continue
        data = {f: (row[i].strip() if i < len(row) else "") for f, i in col_index.items()}
        address = data.get("address")
        prefix_str = data.get("prefix")
        if not address or not prefix_str:
            skipped += 1
            continue
        try:
            ip_obj = ipaddress.ip_address(address)
        except (ValueError, TypeError):
            errors.append(f"第 {lineno} 行：IP「{address}」格式不正确，已跳过")
            skipped += 1
            continue
        try:
            net = ipaddress.ip_network(prefix_str, strict=False)
        except (ValueError, TypeError):
            errors.append(f"第 {lineno} 行：网段「{prefix_str}」格式不正确，已跳过")
            skipped += 1
            continue
        if ip_obj not in net:
            errors.append(f"第 {lineno} 行：IP {address} 不在网段 {prefix_str} 范围内，已跳过")
            skipped += 1
            continue
        pid = pfx_by_cidr.get(prefix_str)
        if pid is None:
            agg_id = _resolve_aggregate(db, data.get("aggregate"), created_aggs, agg_by_name, agg_by_prefix, errors, lineno)
            p = IPAMPrefix(
                aggregate_id=agg_id, prefix=prefix_str, status="规划",
                role="", vlan="", company="", firewall="", zone_interface_name="",
                description="由导入自动创建", is_pool=False,
            )
            db.add(p)
            db.flush()
            pid = p.id
            pfx_by_cidr[prefix_str] = pid
        if (pid, address) in seen:
            skipped += 1
            continue
        allocation = _valid_allocation(data.get("allocation_type", "静态"))
        device_id = None
        if allocation != "DHCP" and data.get("device"):
            device_id = dev_by_name.get(data.get("device"))
            if device_id is None:
                errors.append(f"第 {lineno} 行：关联设备「{data.get('device')}」在设备库中未找到，该 IP 仍会创建但不关联设备")
        db.add(IPAMIPAddress(
            prefix_id=pid,
            address=address,
            status=_valid_status(data.get("status", "规划")),
            allocation_type=allocation,
            dns_name=data.get("dns_name", ""),
            description=data.get("description", ""),
            assigned_device_id=device_id,
            device_name=data.get("device_name", "") or "",
            device_model=data.get("device_model", "") or "",
            device_ip=data.get("device_ip", "") or "",
        ))
        seen.add((pid, address))
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped, "created_aggregates": created_aggs, "errors": errors[:50]}


# ---------------------------------------------------------------------------
# 导出 CSV（与导入列一一对应，可直接再导入，实现模板导出/导入闭环）
# ---------------------------------------------------------------------------
def _csv_response(header, rows, filename):
    """生成带 UTF-8 BOM 的 CSV 响应，便于 Excel 直接打开。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/aggregates")
def export_aggregates(db: Session = Depends(get_db)):
    """导出聚合（大子网）为 CSV 模板/数据文件。"""
    aggs = db.query(IPAMAggregate).order_by(IPAMAggregate.name.asc()).all()
    header = ["名称", "前缀(CIDR)", "规划日期", "描述"]
    rows = [[a.name, a.prefix, a.date_added, a.description] for a in aggs]
    return _csv_response(header, rows, "ipam_aggregates.csv")


@router.get("/export/prefixes")
def export_prefixes(db: Session = Depends(get_db)):
    """导出网段为 CSV，聚合列回写为其 CIDR，便于再次导入。"""
    prefs = db.query(IPAMPrefix).order_by(IPAMPrefix.prefix.asc()).all()
    agg_by_id = {a.id: a for a in db.query(IPAMAggregate).all()}
    header = ["网段(CIDR)", "聚合(CIDR或名称)", "状态", "用途", "VLAN", "公司", "Firewall", "Zone/Interface Name", "描述"]
    rows = []
    for p in prefs:
        agg = agg_by_id.get(p.aggregate_id)
        rows.append([
            p.prefix, agg.prefix if agg else "", p.status, p.role,
            p.vlan, p.company, p.firewall, p.zone_interface_name, p.description,
        ])
    return _csv_response(header, rows, "ipam_prefixes.csv")


@router.get("/export/ips")
def export_ips(db: Session = Depends(get_db)):
    """导出 IP 地址为 CSV。关联设备回写为已登记设备名；手工录入的设备信息写入对应列。"""
    ips = db.query(IPAMIPAddress).order_by(IPAMIPAddress.address.asc()).all()
    pfx_by_id = {p.id: p for p in db.query(IPAMPrefix).all()}
    dev_by_id = {d.id: d for d in db.query(Device).all()}
    header = ["网段(CIDR)", "IP地址", "状态", "分配类型", "关联设备", "设备名称", "设备型号", "设备IP", "DNS名称", "描述"]
    rows = []
    for ip in ips:
        pfx = pfx_by_id.get(ip.prefix_id)
        dev = dev_by_id.get(ip.assigned_device_id) if ip.assigned_device_id else None
        rows.append([
            pfx.prefix if pfx else "", ip.address, ip.status, ip.allocation_type,
            dev.name if dev else "",
            ip.device_name or "", ip.device_model or "", ip.device_ip or "",
            ip.dns_name or "", ip.description or "",
        ])
    return _csv_response(header, rows, "ipam_ips.csv")


# ---------------------------------------------------------------------------
# Stats (概览统计)
# ---------------------------------------------------------------------------
@router.get("/stats")
def ipam_stats(db: Session = Depends(get_db)):
    agg_count = db.query(func.count(IPAMAggregate.id)).scalar() or 0
    p_total = db.query(func.count(IPAMPrefix.id)).scalar() or 0
    ip_total = db.query(func.count(IPAMIPAddress.id)).scalar() or 0

    def status_counts(model, col):
        out = {s: 0 for s in IPAM_STATUSES}
        rows = db.query(col, func.count(model.id)).group_by(col).all()
        for st, cnt in rows:
            if st in out:
                out[st] = cnt
        return out

    prefixes_by_status = status_counts(IPAMPrefix, IPAMPrefix.status)
    ips_by_status = status_counts(IPAMIPAddress, IPAMIPAddress.status)
    in_use_ips = ips_by_status.get("使用中", 0)

    # 各聚合利用率
    agg_util = []
    aggregates = db.query(IPAMAggregate).order_by(IPAMAggregate.name.asc()).all()
    for agg in aggregates:
        resp = _aggregate_response(db, agg)
        agg_util.append({
            "id": agg.id,
            "name": agg.name,
            "prefix": agg.prefix,
            "ip_count": resp.ip_count,
            "utilization": resp.utilization,
        })

    return {
        "aggregates_count": agg_count,
        "prefixes_total": p_total,
        "prefixes_by_status": prefixes_by_status,
        "ips_total": ip_total,
        "ips_by_status": ips_by_status,
        "in_use_ips": in_use_ips,
        "aggregate_utilization": agg_util,
    }


@router.get("/prefix-utilization")
def prefix_utilization(db: Session = Depends(get_db)):
    """网段地址利用率：根据网段 CIDR（掩码）自动计算总地址/可用地址，并结合已划分的 IP 统计
    已使用（已分配）、未使用数量与利用率。供 Dashboard 展示。"""
    prefixes = db.query(IPAMPrefix).order_by(IPAMPrefix.prefix.asc()).all()
    rows = []
    sum_total = 0
    sum_usable = 0
    sum_allocated = 0
    sum_in_use = 0
    for p in prefixes:
        total, usable = _prefix_counts(p.prefix)
        allocated = db.query(func.count(IPAMIPAddress.id)).filter(IPAMIPAddress.prefix_id == p.id).scalar() or 0
        in_use = db.query(func.count(IPAMIPAddress.id)).filter(
            IPAMIPAddress.prefix_id == p.id, IPAMIPAddress.status == "使用中"
        ).scalar() or 0
        unused = max(usable - allocated, 0)
        utilization = round(allocated / usable * 100, 2) if usable else 0.0
        sum_total += total
        sum_usable += usable
        sum_allocated += allocated
        sum_in_use += in_use
        rows.append({
            "id": p.id,
            "prefix": p.prefix or "",
            "status": p.status or "规划",
            "role": p.role or "",
            "vlan": p.vlan or "",
            "company": p.company or "",
            "total_ips": total,
            "usable_ips": usable,
            "allocated_ips": allocated,
            "in_use_ips": in_use,
            "unused_ips": unused,
            "utilization": min(utilization, 100.0),
        })
    overall_util = round(sum_allocated / sum_usable * 100, 2) if sum_usable else 0.0
    return {
        "prefixes": rows,
        "overall": {
            "total_ips": sum_total,
            "usable_ips": sum_usable,
            "allocated_ips": sum_allocated,
            "in_use_ips": sum_in_use,
            "unused_ips": max(sum_usable - sum_allocated, 0),
            "utilization": min(overall_util, 100.0),
            "prefix_count": len(rows),
        },
    }
