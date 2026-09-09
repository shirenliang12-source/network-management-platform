"""虚拟机清单 API routes — 收集汇总 Windows / Linux 等虚拟机的系统信息。

字段：服务器名称 / 服务器功能 / 多IP / CPU·内存·磁盘资源 / 宿主机名称。
宿主机通过 host_id 关联「服务器存储」模块中的 ServerAsset，实现与服务器/存储联动。
"""
import csv
import io
import json
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import VMInstance, VMIP, ServerAsset
from app.schemas import (
    VMInstanceCreate, VMInstanceUpdate, VMInstanceResponse, VMSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vms", tags=["vms"])

VM_OS_TYPES = ["Windows", "Linux", "其他"]
VM_STATUSES = ["运行中", "已关机", "挂起", "模板", "其他"]
VM_SYNC_FIELDS = {
    "name", "function", "os_type", "os_version", "status", "host_name",
    "cpu", "memory", "disk_size", "disk_count", "storage_lun", "disks",
    "management_ip", "additional_ips",
}

# CSV 导入/导出/模板列顺序（host_name 可手填，若与「服务器存储」已有宿主机同名会自动关联）
VM_CSV_COLUMNS = [
    "name", "function", "os_type", "os_version", "status",
    "host_name", "management_ip", "ip2", "ip3", "ip4", "ip5",
    "cpu", "memory", "disk_size", "disk_count", "disks", "storage_lun", "notes",
]


def _valid(value: str, allowed: list, default: str) -> str:
    return value if value in allowed else default


# ---- 磁盘明细 helpers ----
def _dump_disks(disks) -> str:
    """把 [{name, size}] 列表序列化为 JSON 字符串存储。"""
    out = []
    for d in disks or []:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        size = str(d.get("size") or "").strip()
        if name or size:
            out.append({"name": name, "size": size})
    return json.dumps(out, ensure_ascii=False) if out else ""


def _parse_disks(raw) -> list:
    """把 JSON 字符串解析回 [{name, size}] 列表；解析失败返回 []。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [
                {"name": str(d.get("name") or ""), "size": str(d.get("size") or "")}
                for d in data if isinstance(d, dict)
            ]
    except (ValueError, TypeError):
        pass
    return []


def _disks_to_csv(disks) -> str:
    """CSV 单元格序列化：'系统盘:100 GB;数据盘:500 GB'。"""
    return ";".join(f"{d.get('name','')}:{d.get('size','')}" for d in disks or [])


def _parse_disks_csv(text: str) -> list:
    """解析 CSV 单元格 '名称:大小;名称:大小' 为 [{name, size}]。"""
    out = []
    for part in (text or "").split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, size = part.partition(":")
        out.append({"name": name.strip(), "size": size.strip()})
    return out


# ---- Multi-IP helpers (vm) ----
def _normalize_ips(ips: list) -> list:
    seen, out = set(), []
    for ip in ips or []:
        ip = (ip or "").strip()
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _set_vm_ips(db: Session, vm: VMInstance, additional_ips: list):
    additional_ips = _normalize_ips(additional_ips)
    kept = []
    for ip in additional_ips:
        if ip == vm.management_ip:
            continue
        kept.append(ip)
    db.query(VMIP).filter(VMIP.vm_id == vm.id).delete()
    for ip in kept:
        db.add(VMIP(vm_id=vm.id, ip_address=ip))


def _vm_ip_list(vm: VMInstance) -> list:
    return [
        {"ip_address": ip.ip_address, "notes": ip.notes or ""}
        for ip in (vm.additional_ips or [])
    ]


def integration_sync_locked_fields(vm: VMInstance) -> list[str]:
    try:
        values = json.loads(vm.sync_locked_fields or "[]")
    except (TypeError, ValueError):
        values = []
    return [str(value) for value in values if str(value) in VM_SYNC_FIELDS]


def _resolve_host_name(db: Session, host_id: int = None, host_name: str = "") -> str:
    """若提供了 host_id 则带出其名称；否则用传入的 host_name。"""
    if host_id:
        s = db.query(ServerAsset).get(host_id)
        if s:
            return s.name or host_name
    return host_name or ""


# ===========================================================================
# 虚拟机清单 (vm_instances)
# ===========================================================================
@router.get("", response_model=list[VMInstanceResponse])
def list_vms(
    db: Session = Depends(get_db),
    host_id: int = Query(None),
    os_type: str = Query(""),
    status: str = Query(""),
    search: str = Query(""),
):
    q = db.query(VMInstance)
    if host_id is not None:
        q = q.filter(VMInstance.host_id == host_id)
    if os_type:
        q = q.filter(VMInstance.os_type == os_type)
    if status:
        q = q.filter(VMInstance.status == status)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (VMInstance.name.ilike(like))
            | (VMInstance.function.ilike(like))
            | (VMInstance.host_name.ilike(like))
            | (VMInstance.os_version.ilike(like))
            | (VMInstance.management_ip.ilike(like))
            | (VMInstance.cpu.ilike(like))
            | (VMInstance.memory.ilike(like))
            | (VMInstance.disk_size.ilike(like))
            | (VMInstance.storage_lun.ilike(like))
        )
    items = q.order_by(VMInstance.name.asc()).all()
    return [_vm_response(db, v) for v in items]


@router.get("/summary", response_model=VMSummary)
def vm_summary(db: Session = Depends(get_db)):
    total = db.query(func.count(VMInstance.id)).scalar() or 0
    by_os_type = {}
    for t, c in db.query(VMInstance.os_type, func.count(VMInstance.id)).group_by(VMInstance.os_type).all():
        by_os_type[t or "其他"] = c
    by_status = {}
    for st, c in db.query(VMInstance.status, func.count(VMInstance.id)).group_by(VMInstance.status).all():
        by_status[st or "其他"] = c
    return VMSummary(total=total, by_os_type=by_os_type, by_status=by_status)


@router.get("/{vm_id}", response_model=VMInstanceResponse)
def get_vm(vm_id: int, db: Session = Depends(get_db)):
    v = db.query(VMInstance).get(vm_id)
    if not v:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    return _vm_response(db, v)


@router.post("", response_model=VMInstanceResponse)
def create_vm(payload: VMInstanceCreate, db: Session = Depends(get_db)):
    if not payload.name:
        raise HTTPException(status_code=400, detail="服务器名称不能为空")
    if payload.host_id:
        if not db.query(ServerAsset).get(payload.host_id):
            raise HTTPException(status_code=400, detail="关联宿主机不存在")
    host_name = _resolve_host_name(db, payload.host_id, payload.host_name)
    disks_json = _dump_disks(payload.disks)
    # 提供了磁盘明细时，磁盘数量自动取明细条数
    disk_count = len(payload.disks or []) if disks_json else (
        payload.disk_count if payload.disk_count is not None else 1
    )
    v = VMInstance(
        name=payload.name.strip(),
        function=(payload.function or "").strip(),
        os_type=_valid(payload.os_type, VM_OS_TYPES, "Linux"),
        os_version=(payload.os_version or "").strip(),
        status=_valid(payload.status, VM_STATUSES, "运行中"),
        host_id=payload.host_id,
        host_name=host_name,
        cpu=(payload.cpu or "").strip(),
        memory=(payload.memory or "").strip(),
        disk_size=(payload.disk_size or "").strip(),
        disk_count=disk_count,
        storage_lun=(payload.storage_lun or "").strip(),
        disks=disks_json,
        management_ip=(payload.management_ip or "").strip(),
        notes=(payload.notes or "").strip(),
    )
    db.add(v)
    db.flush()
    _set_vm_ips(db, v, payload.additional_ips)
    db.commit()
    db.refresh(v)
    return _vm_response(db, v)


@router.put("/{vm_id}", response_model=VMInstanceResponse)
def update_vm(vm_id: int, payload: VMInstanceUpdate, db: Session = Depends(get_db)):
    v = db.query(VMInstance).get(vm_id)
    if not v:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    data = payload.model_dump(exclude_unset=True)
    locked_fields = data.pop("sync_locked_fields", None)
    if locked_fields is not None:
        invalid = sorted(set(locked_fields) - VM_SYNC_FIELDS)
        if invalid:
            raise HTTPException(status_code=400, detail=f"不支持保护的同步字段: {', '.join(invalid)}")
        v.sync_locked_fields = json.dumps(list(dict.fromkeys(locked_fields)), ensure_ascii=False)
    additional_ips = data.pop("additional_ips", None)
    host_id = data.get("host_id", v.host_id)
    host_name = data.get("host_name", v.host_name)
    if host_id:
        if not db.query(ServerAsset).get(host_id):
            raise HTTPException(status_code=400, detail="关联宿主机不存在")
        # 只要传了 host_id（或显式清空 host_name），都重新带出宿主机名称
        if "host_name" not in data or host_name == "":
            host_name = _resolve_host_name(db, host_id, host_name)
        data["host_name"] = host_name
    for key, value in data.items():
        if value is None:
            continue
        if key == "status":
            value = _valid(value, VM_STATUSES, v.status)
        elif key == "os_type":
            value = _valid(value, VM_OS_TYPES, v.os_type)
        elif key == "disk_count":
            value = int(value) if value else 1
        elif key == "disks":
            value = _dump_disks(value)
        elif isinstance(value, str):
            value = value.strip()
        setattr(v, key, value)
    # 提供了磁盘明细时，磁盘数量自动取明细条数（明细优先于手工数量）
    if "disks" in data:
        parsed = _parse_disks(v.disks)
        if parsed:
            v.disk_count = len(parsed)
    db.flush()
    if additional_ips is not None:
        _set_vm_ips(db, v, additional_ips)
    db.commit()
    db.refresh(v)
    return _vm_response(db, v)


@router.delete("/{vm_id}")
def delete_vm(vm_id: int, db: Session = Depends(get_db)):
    v = db.query(VMInstance).get(vm_id)
    if not v:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    db.delete(v)
    db.commit()
    return {"message": "已删除"}


# ===========================================================================
# 导入 / 导出 / 模板 (CSV)
# ===========================================================================
@router.get("/template/csv")
def vm_template_csv():
    """下载虚拟机 CSV 导入模板（含表头与一条示例数据）。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(VM_CSV_COLUMNS)
    writer.writerow([
        "VM-WEB-01", "Web服务器", "Linux", "CentOS 7.9", "运行中",
        "ESXi-01", "192.168.1.10", "192.168.1.11", "192.168.1.12", "", "",
        "4 vCPU", "8 GB", "200 GB", 2, "系统盘:100 GB;数据盘:100 GB",
        "LUN0:100GB(datastore1),LUN1:200GB(datastore2)", "示例备注",
    ])
    output.seek(0)
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=vm_template.csv"},
    )


@router.get("/export/csv")
def export_vms_csv(db: Session = Depends(get_db)):
    """导出全部虚拟机为 CSV。"""
    items = db.query(VMInstance).order_by(VMInstance.name.asc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(VM_CSV_COLUMNS)
    for v in items:
        extras = [ip.ip_address for ip in (v.additional_ips or [])]
        writer.writerow([
            v.name or "", v.function or "", v.os_type or "Linux", v.os_version or "",
            v.status or "运行中", v.host_name or "", v.management_ip or "",
            extras[0] if len(extras) > 0 else "",
            extras[1] if len(extras) > 1 else "",
            extras[2] if len(extras) > 2 else "",
            extras[3] if len(extras) > 3 else "",
            v.cpu or "", v.memory or "", v.disk_size or "",
            v.disk_count if v.disk_count is not None else 1,
            _disks_to_csv(_parse_disks(v.disks)),
            v.storage_lun or "",
            v.notes or "",
        ])
    output.seek(0)
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=vm_export.csv"},
    )


@router.post("/import-csv")
async def import_vms_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """从 CSV 批量导入虚拟机。

    列（顺序见 VM_CSV_COLUMNS）：name, function, os_type, os_version, status,
    host_name, management_ip, ip2..ip5, cpu, memory, disk_size, notes。
    - name 必填；重复名称(name)自动跳过。
    - host_name 若与「服务器存储」已有宿主机名称一致，自动关联 host_id。
    - os_type / status 非法值回退到默认（Linux / 运行中）。
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码无法识别，请使用 UTF-8 编码的 CSV")
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
            name = (row.get("name") or "").strip()
            if not name:
                errors.append(f"第 {row_num} 行: 缺少服务器名称(name)")
                continue
            if db.query(VMInstance).filter(VMInstance.name == name).first():
                skipped += 1
                continue

            # 多网卡额外 IP：ip2, ip3, ... ip5
            extra_by_idx = {}
            for key in row.keys():
                m = re.match(r"^ip(\d+)$", (key or "").strip(), re.IGNORECASE)
                if m and int(m.group(1)) >= 2:
                    val = (row.get(key) or "").strip()
                    if val:
                        extra_by_idx[int(m.group(1))] = val
            extras = [extra_by_idx[n] for n in sorted(extra_by_idx)]

            # 宿主机名称 -> host_id（匹配「服务器存储」已有宿主机）
            host_name = (row.get("host_name") or "").strip()
            host_id = None
            if host_name:
                s = db.query(ServerAsset).filter(ServerAsset.name == host_name).first()
                if s:
                    host_id = s.id

            v = VMInstance(
                name=name,
                function=(row.get("function") or "").strip(),
                os_type=_valid((row.get("os_type") or "").strip(), VM_OS_TYPES, "Linux"),
                os_version=(row.get("os_version") or "").strip(),
                status=_valid((row.get("status") or "").strip(), VM_STATUSES, "运行中"),
                host_id=host_id,
                host_name=host_name,
                cpu=(row.get("cpu") or "").strip(),
                memory=(row.get("memory") or "").strip(),
                disk_size=(row.get("disk_size") or "").strip(),
                disk_count=0,  # 占位，下方按明细条数覆盖
                disks="",
                storage_lun=(row.get("storage_lun") or "").strip(),
                management_ip=(row.get("management_ip") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
            # 磁盘明细：有明细时数量自动取明细条数，否则用 disk_count 列
            disks_json = _dump_disks(_parse_disks_csv(row.get("disks") or ""))
            v.disks = disks_json
            if disks_json:
                v.disk_count = len(_parse_disks(disks_json))
            else:
                v.disk_count = int(row.get("disk_count") or 1) if (row.get("disk_count") or "").strip().isdigit() else 1
            db.add(v)
            db.flush()
            _set_vm_ips(db, v, extras)
            imported += 1
        except Exception as e:
            db.rollback()
            errors.append(f"第 {row_num} 行: {str(e)}")
    db.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "message": f"成功导入 {imported} 条，跳过重复 {skipped} 条，失败 {len(errors)} 条",
    }

def _vm_response(db: Session, v: VMInstance) -> VMInstanceResponse:
    return VMInstanceResponse(
        id=v.id,
        name=v.name or "",
        function=v.function or "",
        os_type=v.os_type or "Linux",
        os_version=v.os_version or "",
        status=v.status or "运行中",
        host_id=v.host_id,
        host_name=v.host_name or "",
        cpu=v.cpu or "",
        memory=v.memory or "",
        disk_size=v.disk_size or "",
        disk_count=v.disk_count if v.disk_count is not None else 1,
        storage_lun=v.storage_lun or "",
        disks=_parse_disks(v.disks),
        management_ip=v.management_ip or "",
        additional_ips=_vm_ip_list(v),
        notes=v.notes or "",
        source_type=v.source_type or "manual",
        external_id=v.external_id,
        source_endpoint=v.source_endpoint or "",
        last_synced_at=v.last_synced_at,
        sync_state=v.sync_state or ("manual" if (v.source_type or "manual") == "manual" else "active"),
        stale_since=v.stale_since,
        sync_locked_fields=integration_sync_locked_fields(v),
        storage_assets=[{
            "id": row.id, "name": row.name, "source_type": row.source_type,
            "capacity": row.capacity, "free_space": row.free_space,
            "sync_state": row.sync_state,
        } for row in v.storage_assets],
        created_at=v.created_at,
        updated_at=v.updated_at,
    )
