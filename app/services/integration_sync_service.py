"""Diff, persistence and scheduled synchronization for external inventories."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    IntegrationStorage,
    IntegrationSyncRun,
    ServerAsset,
    SystemSetting,
    VMIP,
    VMInstance,
    decrypt_password,
)
from app.services import integration_service


CONFIG_KEYS = {"zabbix": "integration_zabbix", "vcenter": "integration_vcenter"}
SYNC_FIELDS = (
    "name", "function", "os_type", "os_version", "status", "host_name",
    "cpu", "memory", "disk_size", "disk_count", "storage_lun",
    "management_ip", "disks", "additional_ips",
)
FIELD_LABELS = {
    "name": "名称", "function": "功能", "os_type": "系统类型", "os_version": "系统版本",
    "status": "状态", "host_name": "宿主机", "cpu": "CPU", "memory": "内存",
    "disk_size": "磁盘容量", "disk_count": "磁盘数", "storage_lun": "存储",
    "management_ip": "管理 IP", "disks": "磁盘明细", "additional_ips": "附加 IP",
}
_SYNC_LOCKS = {"zabbix": threading.Lock(), "vcenter": threading.Lock()}


def locked_fields(vm: VMInstance) -> set[str]:
    """Return validated fields the operator owns instead of the integration."""
    try:
        values = json.loads(vm.sync_locked_fields or "[]")
    except (TypeError, ValueError):
        values = []
    return {str(value) for value in values if str(value) in SYNC_FIELDS}


def load_stored_config(db: Session, source: str) -> dict[str, Any]:
    """Load and decrypt a saved integration configuration for background use."""
    if source not in CONFIG_KEYS:
        raise ValueError(f"不支持的数据源: {source}")
    row = db.get(SystemSetting, CONFIG_KEYS[source])
    try:
        config = json.loads(row.value) if row and row.value else {}
    except (TypeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    encrypted = str(config.get("password") or "")
    config["password"] = decrypt_password(encrypted) if encrypted else ""
    if source == "zabbix" and not config.get("source_ip"):
        from app.services.nic_service import get_preferred_source_ip
        config["source_ip"] = get_preferred_source_ip(db)
    identity = config.get("url") if source == "zabbix" else config.get("host")
    if not identity or not config.get("username") or not config.get("password"):
        raise RuntimeError(f"{source} 连接配置不完整")
    return config


def source_endpoint(source: str, config: dict[str, Any]) -> str:
    if source == "zabbix":
        return str(config.get("url") or "")[:500]
    return f"{config.get('host', '')}:{config.get('port', 443)}"[:500]


def discover(source: str, config: dict[str, Any], limit: int = 2000) -> dict[str, Any]:
    if source == "zabbix":
        return integration_service.discover_zabbix(config, limit=limit)
    if source == "vcenter":
        return integration_service.discover_vcenter(config, limit=limit)
    raise ValueError(f"不支持的数据源: {source}")


def _parse_disks(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        rows = value
    else:
        try:
            rows = json.loads(value or "[]")
        except (TypeError, ValueError):
            rows = []
    return [
        {"name": str(row.get("name") or "")[:200], "size": str(row.get("size") or "")[:100]}
        for row in rows if isinstance(row, dict)
    ]


def _additional_ips(vm: VMInstance) -> list[str]:
    return [str(row.ip_address) for row in vm.additional_ips if row.ip_address]


def _desired_vm(item: dict[str, Any]) -> dict[str, Any]:
    disks = _parse_disks(item.get("disks"))
    os_type = str(item.get("os_type") or "其他")
    status = str(item.get("status") or "其他")
    return {
        "name": str(item.get("name") or item.get("external_id") or "")[:200],
        "function": str(item.get("function") or "")[:200],
        "os_type": os_type if os_type in {"Windows", "Linux", "其他"} else "其他",
        "os_version": str(item.get("os_version") or "")[:200],
        "status": status if status in {"运行中", "已关机", "挂起", "模板", "其他"} else "其他",
        "host_name": str(item.get("host_name") or "").strip()[:200],
        "cpu": str(item.get("cpu") or "")[:100],
        "memory": str(item.get("memory") or "")[:100],
        "disk_size": str(item.get("disk_size") or "")[:100],
        "disk_count": len(disks) or max(1, int(item.get("disk_count") or 1)),
        "storage_lun": str(item.get("storage_lun") or "")[:500],
        "management_ip": str(item.get("management_ip") or "")[:45],
        "disks": disks,
        "additional_ips": list(dict.fromkeys(
            str(value or "").strip()[:45]
            for value in list(item.get("additional_ips") or [])
            if str(value or "").strip()
        )),
    }


def _current_vm(vm: VMInstance) -> dict[str, Any]:
    return {
        "name": vm.name or "", "function": vm.function or "", "os_type": vm.os_type or "其他",
        "os_version": vm.os_version or "", "status": vm.status or "其他",
        "host_name": vm.host_name or "", "cpu": vm.cpu or "", "memory": vm.memory or "",
        "disk_size": vm.disk_size or "", "disk_count": vm.disk_count or 1,
        "storage_lun": vm.storage_lun or "", "management_ip": vm.management_ip or "",
        "disks": _parse_disks(vm.disks), "additional_ips": _additional_ips(vm),
    }


def _display(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def filter_sync_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    """Exclude known templates/off VMs without treating them as missing."""
    excluded = set(inventory.get("excluded_vm_ids", []))
    eligible = []
    for item in inventory.get("vms", []):
        if item.get("status") in {"模板", "已关机"}:
            if item.get("external_id"):
                excluded.add(str(item["external_id"]))
        else:
            eligible.append(item)
    inventory["vms"] = eligible
    inventory["excluded_vm_ids"] = sorted(excluded)
    inventory["excluded_vm_count"] = len(excluded)
    inventory.setdefault("summary", {})["vms"] = len(eligible)
    return inventory


def preview_inventory(
    db: Session,
    source: str,
    inventory: dict[str, Any],
    *,
    detect_missing: bool = False,
) -> dict[str, Any]:
    """Annotate an inventory with deterministic create/update/stale diffs."""
    filter_sync_inventory(inventory)
    existing = {
        str(vm.external_id): vm
        for vm in db.query(VMInstance).filter(
            VMInstance.source_type == source, VMInstance.external_id.is_not(None)
        ).all()
    }
    seen: set[str] = set(inventory.get("excluded_vm_ids", []))
    from app.services.csv_inventory_import import normalized, stored_address
    local_names, local_ips = {}, {}
    for local in db.query(VMInstance).all():
        local_names.setdefault(normalized(local.name), set()).add(local.id)
        for ip in [local.management_ip] + [p.ip_address for p in local.additional_ips]:
            if ip:
                local_ips.setdefault(stored_address(ip), set()).add(local.id)
    incoming_names, incoming_ips = {}, {}
    summary = {"new": 0, "update": 0, "unchanged": 0, "error": 0, "stale": 0,
               "storage_new": 0, "storage_update": 0, "storage_unchanged": 0, "storage_stale": 0}
    for item in inventory.get("vms", []):
        external_id = str(item.get("external_id") or "")
        if external_id:
            seen.add(external_id)
        vm = existing.get(external_id)
        name_key = normalized(item.get('name'))
        ip_keys = {stored_address(ip) for ip in [item.get('management_ip')] + (item.get('additional_ips') or []) if ip}
        if vm is None:
            collision_ids = set(local_names.get(name_key, set())) if name_key else set()
            for ip in ip_keys:
                collision_ids.update(local_ips.get(ip, set()))
            duplicate_source = (name_key and name_key in incoming_names) or any(ip in incoming_ips for ip in ip_keys)
            if collision_ids or duplicate_source:
                item['error'] = '名称/IP 与已有记录或本次其他对象冲突；请先确认身份，不自动合并或新增'
                item['conflict_ids'] = sorted(collision_ids)
        if name_key:
            incoming_names[name_key] = external_id
        for ip in ip_keys:
            incoming_ips[ip] = external_id
        item["imported"] = vm is not None
        item["local_id"] = vm.id if vm else None
        if item.get("error") or not external_id:
            item["change_type"] = "error"
            item["changed_fields"] = []
            summary["error"] += 1
            continue
        if vm is None:
            item["change_type"] = "new"
            item["changed_fields"] = []
            summary["new"] += 1
            continue
        before = _current_vm(vm)
        after = _desired_vm(item)
        protected = locked_fields(vm)
        changes = [
            {"field": field, "label": FIELD_LABELS[field],
             "before": _display(before[field]), "after": _display(after[field])}
            for field in SYNC_FIELDS if field not in protected and before[field] != after[field]
        ]
        item["change_type"] = "update" if changes else "unchanged"
        item["changed_fields"] = changes
        item["protected_fields"] = [FIELD_LABELS[field] for field in SYNC_FIELDS if field in protected]
        summary[item["change_type"]] += 1

    missing_vms = []
    if detect_missing:
        for external_id, vm in existing.items():
            if external_id not in seen:
                missing_vms.append({
                    "id": vm.id, "external_id": external_id, "name": vm.name,
                    "sync_state": vm.sync_state, "stale_since": vm.stale_since,
                })
        summary["stale"] = len(missing_vms)

    existing_storage = {
        str(row.external_id): row
        for row in db.query(IntegrationStorage).filter(IntegrationStorage.source_type == source).all()
    }
    seen_storage: set[str] = set()
    storage_fields = ("name", "storage_type", "capacity", "used_space", "free_space", "accessible", "vm_count")
    for item in inventory.get("storage", []):
        external_id = str(item.get("external_id") or "")
        if not external_id:
            continue
        seen_storage.add(external_id)
        row = existing_storage.get(external_id)
        item["imported"] = row is not None
        item["local_id"] = row.id if row else None
        desired = {
            "name": str(item.get("name") or external_id),
            "storage_type": str(item.get("type") or ""),
            "capacity": str(item.get("capacity") or ""),
            "used_space": str(item.get("used_space") or ""),
            "free_space": str(item.get("free_space") or ""),
            "accessible": bool(item.get("accessible", True)),
            "vm_count": int(item.get("vm_count") or 0),
        }
        if row is None:
            item["change_type"] = "new"
            summary["storage_new"] += 1
        else:
            changed = any(getattr(row, field) != value for field, value in desired.items())
            item["change_type"] = "update" if changed else "unchanged"
            summary[f"storage_{item['change_type']}"] += 1

    missing_storage = []
    if detect_missing:
        missing_storage = [
            {"id": row.id, "external_id": external_id, "name": row.name,
             "sync_state": row.sync_state, "stale_since": row.stale_since}
            for external_id, row in existing_storage.items() if external_id not in seen_storage
        ]
        summary["storage_stale"] = len(missing_storage)

    inventory["diff_summary"] = summary
    inventory["missing_vms"] = missing_vms
    inventory["missing_storage"] = missing_storage
    return inventory


def _set_vm_ips(db: Session, vm: VMInstance, primary: str, additional: list[str]) -> None:
    vm.management_ip = primary
    # The relationship is commonly loaded while calculating a diff. Clear it
    # through the ORM so deleted identities cannot collide with SQLite reusing
    # their primary keys during the same session.
    vm.additional_ips.clear()
    db.flush()
    seen = {primary} if primary else set()
    for ip in additional:
        if ip and ip not in seen:
            seen.add(ip)
            vm.additional_ips.append(VMIP(ip_address=ip))


def _new_run(db: Session, source: str, mode: str, max_attempts: int) -> IntegrationSyncRun:
    run = IntegrationSyncRun(
        source_type=source, mode=mode, status="running", attempt=1,
        max_attempts=max_attempts, started_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _record_failure(db: Session, run_id: int, attempt: int, exc: Exception, final: bool) -> None:
    db.rollback()
    run = db.get(IntegrationSyncRun, run_id)
    if run:
        run.attempt = attempt
        run.status = "failed" if final else "retrying"
        run.error_message = str(exc)[:4000]
        run.completed_at = datetime.utcnow() if final else None
        db.commit()


def _apply_core(
    db: Session,
    run: IntegrationSyncRun,
    source: str,
    config: dict[str, Any],
    inventory: dict[str, Any],
    *,
    selected_ids: set[str] | None,
    update_existing: bool,
    mark_missing: bool,
) -> dict[str, Any]:
    safe_mark_missing = mark_missing and not bool(inventory.get("truncated"))
    preview_inventory(db, source, inventory, detect_missing=safe_mark_missing)
    endpoint = source_endpoint(source, config)
    # Until multi-source migration exists, never move an external identity
    # silently to another endpoint merely because the configured host changed.
    for model in (VMInstance, IntegrationStorage):
        for row in db.query(model).filter(model.source_type == source).all():
            if row.source_endpoint and row.source_endpoint.strip().casefold() != endpoint.strip().casefold():
                raise ValueError('当前来源已绑定其他服务器；请先核对来源身份，禁止覆盖原有同步关联')
    now = datetime.utcnow()
    host_map = {
        (row.name or "").strip().casefold(): row
        for row in db.query(ServerAsset).all() if (row.name or "").strip()
    }

    storage_map: dict[str, IntegrationStorage] = {}
    storage_name_map: dict[str, IntegrationStorage] = {}
    seen_storage: set[str] = set()
    for item in inventory.get("storage", []):
        external_id = str(item.get("external_id") or "")[:255]
        if not external_id:
            continue
        seen_storage.add(external_id)
        row = db.query(IntegrationStorage).filter_by(source_type=source, external_id=external_id).first()
        if row is None:
            row = IntegrationStorage(source_type=source, external_id=external_id)
            db.add(row)
        row.source_endpoint = endpoint
        row.name = str(item.get("name") or external_id)[:300]
        row.storage_type = str(item.get("type") or "")[:100]
        row.capacity = str(item.get("capacity") or "")[:100]
        row.used_space = str(item.get("used_space") or "")[:100]
        row.free_space = str(item.get("free_space") or "")[:100]
        row.accessible = bool(item.get("accessible", True))
        row.vm_count = int(item.get("vm_count") or 0)
        row.sync_state = "active"
        row.stale_since = None
        row.last_synced_at = now
        db.flush()
        storage_map[external_id] = row
        storage_name_map[row.name.casefold()] = row

    created = updated = unchanged = skipped = 0
    imported_ids: dict[str, int] = {}
    seen_vm_ids: set[str] = set(inventory.get("excluded_vm_ids", []))
    for item in inventory.get("vms", []):
        external_id = str(item.get("external_id") or "")[:255]
        if external_id:
            seen_vm_ids.add(external_id)
        if not external_id or item.get("error"):
            if selected_ids is None or external_id in selected_ids:
                skipped += 1
            continue
        if selected_ids is not None and external_id not in selected_ids:
            continue
        vm = db.query(VMInstance).filter_by(source_type=source, external_id=external_id).first()
        if vm is not None and not update_existing:
            skipped += 1
            imported_ids[external_id] = vm.id
            continue
        desired = _desired_vm(item)
        if vm is None:
            vm = VMInstance(source_type=source, external_id=external_id)
            db.add(vm)
            created += 1
            protected = set()
            effective = desired
        else:
            protected = locked_fields(vm)
            current = _current_vm(vm)
            effective = dict(desired)
            for field in protected:
                effective[field] = current[field]
        if vm.id is not None and _current_vm(vm) == effective:
            unchanged += 1
        elif vm.id is not None:
            updated += 1
        matched_host = host_map.get(effective["host_name"].casefold())
        for field in (
            "name", "function", "os_type", "os_version", "status", "host_name",
            "cpu", "memory", "disk_size", "disk_count", "storage_lun",
        ):
            setattr(vm, field, effective[field])
        if "host_name" not in protected:
            vm.host_id = matched_host.id if matched_host else None
        vm.disks = json.dumps(effective["disks"], ensure_ascii=False) if effective["disks"] else ""
        vm.source_endpoint = endpoint
        vm.last_synced_at = now
        vm.sync_state = "active"
        vm.stale_since = None
        if not vm.notes:
            vm.notes = f"由 {source} 清单同步"
        db.flush()
        _set_vm_ips(db, vm, effective["management_ip"], effective["additional_ips"])

        linked: list[IntegrationStorage] = []
        for storage_id in item.get("storage_external_ids") or []:
            row = storage_map.get(str(storage_id))
            if row and row not in linked:
                linked.append(row)
        if not linked and desired["storage_lun"]:
            for name in desired["storage_lun"].split(","):
                row = storage_name_map.get(name.strip().casefold())
                if row and row not in linked:
                    linked.append(row)
        vm.storage_assets = linked
        imported_ids[external_id] = vm.id

    stale = 0
    if safe_mark_missing:
        for vm in db.query(VMInstance).filter(
            VMInstance.source_type == source, VMInstance.external_id.is_not(None)
        ).all():
            if str(vm.external_id) not in seen_vm_ids:
                if vm.stale_since is None:
                    vm.stale_since = now
                if vm.sync_state != "stale_confirmed":
                    vm.sync_state = "stale"
                stale += 1
        for row in db.query(IntegrationStorage).filter(IntegrationStorage.source_type == source).all():
            if str(row.external_id) not in seen_storage:
                if row.stale_since is None:
                    row.stale_since = now
                if row.sync_state != "stale_confirmed":
                    row.sync_state = "stale"

    run.status = "success"
    run.completed_at = now
    run.discovered_vms = len(inventory.get("vms", []))
    run.discovered_storage = len(inventory.get("storage", []))
    run.created_count = created
    run.updated_count = updated
    run.unchanged_count = unchanged
    run.stale_count = stale
    run.diff_summary = inventory.get("diff_summary", {})
    run.diff_summary["excluded_vms"] = inventory.get("excluded_vm_count", 0)
    if mark_missing and not safe_mark_missing:
        run.diff_summary = dict(run.diff_summary or {})
        run.diff_summary["stale_skipped_reason"] = "inventory_truncated"
    run.snapshot = {
        "source": source,
        "version": inventory.get("version", ""),
        "fetched_at": inventory.get("fetched_at"),
        "vms": inventory.get("vms", []),
        "storage": inventory.get("storage", []),
    }
    run.error_message = ""
    db.commit()
    return {
        "ok": True, "run_id": run.id, "created": created, "updated": updated,
        "unchanged": unchanged, "skipped": skipped, "stale": stale,
        "imported_ids": imported_ids,
        "excluded": inventory.get("excluded_vm_count", 0),
        "conflicts": [{"external_id":item.get('external_id'), "existing_ids":item.get('conflict_ids',[]), "reason":item.get('error')}
                      for item in inventory.get('vms',[]) if item.get('error') and (selected_ids is None or str(item.get('external_id')) in selected_ids)],
        "message": f"同步完成：新增 {created}，更新 {updated}，未变化 {unchanged}，跳过/冲突 {skipped}，失联 {stale}，排除模板/关机 {inventory.get('excluded_vm_count', 0)}",
    }


def apply_inventory(
    db: Session,
    source: str,
    config: dict[str, Any],
    inventory: dict[str, Any],
    *,
    selected_ids: set[str] | None = None,
    update_existing: bool = True,
    mark_missing: bool = False,
    mode: str = "manual",
) -> dict[str, Any]:
    lock = _SYNC_LOCKS[source]
    if not lock.acquire(blocking=False):
        raise RuntimeError(f"{source} 同步任务正在运行，请稍后再试")
    try:
        run = _new_run(db, source, mode, 1)
        try:
            return _apply_core(
                db, run, source, config, inventory, selected_ids=selected_ids,
                update_existing=update_existing, mark_missing=mark_missing,
            )
        except Exception as exc:
            _record_failure(db, run.id, 1, exc, True)
            raise
    finally:
        lock.release()


def run_scheduled_sync(source: str, db: Session, max_attempts: int = 3) -> IntegrationSyncRun:
    """Run a full sync with bounded retries and missing-object detection."""
    lock = _SYNC_LOCKS[source]
    if not lock.acquire(blocking=False):
        raise RuntimeError(f"{source} 同步任务正在运行，请稍后再试")
    try:
        run = _new_run(db, source, "scheduled", max(1, max_attempts))
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                run = db.get(IntegrationSyncRun, run.id)
                run.status = "running"
                run.attempt = attempt
                db.commit()
                config = load_stored_config(db, source)
                inventory = discover(source, config, limit=2000)
                _apply_core(
                    db, run, source, config, inventory, selected_ids=None,
                    update_existing=True, mark_missing=True,
                )
                return db.get(IntegrationSyncRun, run.id)
            except Exception as exc:
                _record_failure(db, run.id, attempt, exc, attempt >= max_attempts)
                if attempt >= max_attempts:
                    raise
        raise RuntimeError("同步任务未执行")
    finally:
        lock.release()
