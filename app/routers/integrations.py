"""Zabbix and vCenter inventory integration APIs."""
from __future__ import annotations

import json
from typing import Any, Literal, Annotated
from pydantic import StringConstraints
from app.services.integration_sources import config_key, provider

IntegrationSource = Annotated[str, StringConstraints(pattern=r'^(?:zabbix|vcenter(?:-[0-9a-f]{12})?)$')]

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api_models import (
    IntegrationImportRequest, IntegrationStaleActionRequest, IntegrationSyncRequest,
    VCenterConfigRequest, ZabbixConfigRequest,
)
from app.database import get_db
from app.models import IntegrationStorage, IntegrationSyncRun, SystemSetting, VMInstance, decrypt_password, encrypt_password
from app.services import integration_service, integration_sync_service
from app.services.nic_service import get_default_source_ip, get_network_interfaces, get_preferred_source_ip


router = APIRouter(prefix="/api/integrations", tags=["integrations"])

CONFIG_KEYS = {"zabbix": "integration_zabbix", "vcenter": "integration_vcenter"}
DEFAULTS: dict[str, dict[str, Any]] = {
    "zabbix": {
        "url": "", "username": "", "password": "", "verify_ssl": True, "timeout": 20,
        "source_ip": "",
    },
    "vcenter": {
        "host": "", "port": 443, "username": "", "password": "",
        "verify_ssl": True, "timeout": 30,
    },
}


def _stored_config(db: Session, source: str, with_password: bool = False) -> dict[str, Any]:
    config = dict(DEFAULTS[provider(source)])
    row = db.get(SystemSetting, config_key(source))
    if row and row.value:
        try:
            saved = json.loads(row.value)
            if isinstance(saved, dict):
                config.update({key: value for key, value in saved.items() if key in config})
        except (TypeError, ValueError):
            pass
    if with_password and source == "zabbix" and not config.get("source_ip"):
        config["source_ip"] = get_preferred_source_ip(db)
    encrypted = str(config.get("password") or "")
    if with_password:
        config["password"] = decrypt_password(encrypted) if encrypted else ""
    else:
        config["password"] = ""
        config["password_configured"] = bool(encrypted)
    return config


def _save_config(db: Session, source: str, payload: dict[str, Any]) -> dict[str, Any]:
    existing = _stored_config(db, source, with_password=False)
    config = dict(DEFAULTS[provider(source)])
    config.update({key: value for key, value in payload.items() if key in config and key != "password"})
    row = db.get(SystemSetting, config_key(source))
    existing_encrypted = ""
    if row and row.value:
        try:
            existing_encrypted = str(json.loads(row.value).get("password") or "")
        except (TypeError, ValueError, AttributeError):
            existing_encrypted = ""
    raw_password = payload.get("password") or ""
    config["password"] = encrypt_password(raw_password) if raw_password else existing_encrypted
    if not config["password"] and not existing.get("password_configured"):
        raise HTTPException(status_code=400, detail="首次保存必须填写连接密码")
    serialized = json.dumps(config, ensure_ascii=False)
    if row:
        row.value = serialized
    else:
        db.add(SystemSetting(key=config_key(source), value=serialized))
    db.commit()
    return _stored_config(db, source, with_password=False)


def _configured(db: Session, source: str) -> dict[str, Any]:
    config = _stored_config(db, source, with_password=True)
    identity = config.get("url") if source == "zabbix" else config.get("host")
    if not identity or not config.get("username") or not config.get("password"):
        raise HTTPException(status_code=400, detail=f"请先完整保存 {source} 连接配置")
    return config


def _discover(source: str, config: dict[str, Any], limit: int) -> dict[str, Any]:
    try:
        if source == "zabbix":
            result = integration_service.discover_zabbix(config, limit=limit)
        else:
            result = integration_service.discover_vcenter(config, limit=limit)
        return integration_sync_service.filter_sync_inventory(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{source} 清单获取失败: {exc}") from exc


@router.get("/config")
def get_configs(db: Session = Depends(get_db)):
    return {
        "zabbix": _stored_config(db, "zabbix"),
        "vcenter": _stored_config(db, "vcenter"),
    }


@router.get("/network-interfaces")
def integration_network_interfaces():
    """Expose local IPv4 adapters under the integrations permission boundary."""
    interfaces = get_network_interfaces()
    return {
        "interfaces": interfaces,
        "default_ip": get_default_source_ip(),
        "count": len(interfaces),
    }


@router.put("/zabbix/config")
def save_zabbix_config(
    payload: ZabbixConfigRequest, request: Request, db: Session = Depends(get_db),
):
    result = _save_config(db, "zabbix", payload.model_dump())
    request.state.audit_action = "integration.zabbix.configure"
    request.state.audit_resource_type = "integration"
    request.state.audit_resource_id = "zabbix"
    request.state.audit_detail = {
        "url": result["url"], "username": result["username"],
        "source_ip": result.get("source_ip", ""),
    }
    return result


@router.put("/vcenter/config")
def save_vcenter_config(
    payload: VCenterConfigRequest, request: Request, db: Session = Depends(get_db),
):
    result = _save_config(db, "vcenter", payload.model_dump())
    request.state.audit_action = "integration.vcenter.configure"
    request.state.audit_resource_type = "integration"
    request.state.audit_resource_id = "vcenter"
    request.state.audit_detail = {
        "host": result["host"], "port": result["port"], "username": result["username"],
    }
    return result


@router.get('/vcenter-sources')
def list_vcenter_sources(db: Session = Depends(get_db)):
    rows = db.query(SystemSetting).filter(SystemSetting.key.like('integration_vcenter%')).all()
    sources = []
    for row in rows:
        source = row.key.removeprefix('integration_')
        try:
            sources.append({'id': source, **_stored_config(db, source)})
        except ValueError:
            continue
    return sources


@router.post('/vcenter-sources')
def add_vcenter_source(payload: VCenterConfigRequest, request: Request, db: Session = Depends(get_db)):
    import secrets
    for existing in list_vcenter_sources(db):
        if existing['host'].strip().casefold() == payload.host.strip().casefold() and existing['port'] == payload.port:
            raise HTTPException(409, '此 vCenter / ESXi 地址已存在，请选择原来源编辑')
    source = 'vcenter-' + secrets.token_hex(6)
    result = _save_config(db, source, payload.model_dump())
    request.state.audit_action = 'integration.vcenter.add_source'
    request.state.audit_resource_id = source
    return {'id': source, **result}


@router.put('/vcenter-sources/{source}')
def update_vcenter_source(source: IntegrationSource, payload: VCenterConfigRequest, request: Request, db: Session = Depends(get_db)):
    if provider(source) != 'vcenter' or not db.get(SystemSetting, config_key(source)):
        raise HTTPException(404, '来源不存在')
    current = _stored_config(db, source)
    if (current['host'].strip().casefold(), current['port']) != (payload.host.strip().casefold(), payload.port):
        raise HTTPException(409, '来源地址不可修改；连接其他主机请新增来源，避免混淆已有资产')
    result = _save_config(db, source, payload.model_dump())
    request.state.audit_action = 'integration.vcenter.update_source'
    request.state.audit_resource_id = source
    return {'id': source, **result}


@router.post("/{source}/test")
def test_connection(
    source: IntegrationSource, request: Request, db: Session = Depends(get_db),
):
    config = _configured(db, source)
    try:
        result = (
            integration_service.test_zabbix(config)
            if source == "zabbix"
            else integration_service.test_vcenter(config)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{source} 连接测试失败: {exc}") from exc
    request.state.audit_action = f"integration.{source}.test"
    request.state.audit_resource_type = "integration"
    request.state.audit_resource_id = source
    request.state.audit_detail = {"ok": True, "version": result.get("version", "")}
    return result


@router.post("/{source}/discover")
def discover_inventory(
    source: IntegrationSource,
    request: Request,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    result = _discover(source, _configured(db, source), limit)
    integration_sync_service.preview_inventory(db, source, result, detect_missing=False)
    request.state.audit_action = f"integration.{source}.discover"
    request.state.audit_resource_type = "integration"
    request.state.audit_resource_id = source
    request.state.audit_detail = {
        "vm_count": len(result.get("vms", [])), "storage_count": len(result.get("storage", [])),
    }
    return result


@router.post("/{source}/preview")
def preview_sync(
    source: IntegrationSource,
    request: Request,
    db: Session = Depends(get_db),
):
    """Full dry-run diff, including objects no longer present at the source."""
    result = _discover(source, _configured(db, source), 2000)
    integration_sync_service.preview_inventory(
        db, source, result, detect_missing=not bool(result.get("truncated"))
    )
    if result.get("truncated"):
        result["diff_summary"]["stale_skipped_reason"] = "inventory_truncated"
    request.state.audit_action = f"integration.{source}.preview"
    request.state.audit_resource_type = "integration"
    request.state.audit_resource_id = source
    request.state.audit_detail = result.get("diff_summary", {})
    return result


@router.post("/{source}/import")
def import_inventory(
    source: IntegrationSource,
    payload: IntegrationImportRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    config = _configured(db, source)
    # Import re-fetches authoritative source data instead of trusting browser
    # payloads. Use the same upper bound as discovery so selections made from a
    # large inventory (items 501-2000) cannot disappear during import.
    discovered = _discover(source, config, 2000)
    selected_ids = set(payload.external_ids)
    found_ids = {
        str(item.get("external_id") or "") for item in discovered.get("vms", [])
        if not item.get("error")
    }
    missing = sorted(selected_ids - found_ids)
    try:
        result = integration_sync_service.apply_inventory(
            db, source, config, discovered, selected_ids=selected_ids,
            update_existing=payload.update_existing, mark_missing=False, mode="manual",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{source} 导入失败: {exc}") from exc
    request.state.audit_action = f"integration.{source}.import"
    request.state.audit_resource_type = "vm_inventory"
    request.state.audit_resource_id = source
    request.state.audit_detail = {
        "created": result["created"], "updated": result["updated"],
        "skipped": result["skipped"], "missing": len(missing), "run_id": result["run_id"],
    }
    result["missing_external_ids"] = missing
    return result


@router.post("/{source}/sync")
def execute_sync(
    source: IntegrationSource,
    payload: IntegrationSyncRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    config = _configured(db, source)
    inventory = _discover(source, config, 2000)
    try:
        result = integration_sync_service.apply_inventory(
            db, source, config, inventory,
            selected_ids=set(payload.external_ids) if payload.external_ids is not None else None,
            update_existing=payload.update_existing,
            mark_missing=payload.mark_missing and payload.external_ids is None,
            mode="manual",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{source} 同步失败: {exc}") from exc
    request.state.audit_action = f"integration.{source}.sync"
    request.state.audit_resource_type = "integration_sync"
    request.state.audit_resource_id = str(result["run_id"])
    request.state.audit_detail = {key: result[key] for key in ("created", "updated", "unchanged", "stale")}
    return result


def _run_response(row: IntegrationSyncRun, include_snapshot: bool = False) -> dict[str, Any]:
    result = {
        "id": row.id, "source_type": row.source_type, "mode": row.mode,
        "status": row.status, "attempt": row.attempt, "max_attempts": row.max_attempts,
        "started_at": row.started_at, "completed_at": row.completed_at,
        "discovered_vms": row.discovered_vms, "discovered_storage": row.discovered_storage,
        "created": row.created_count, "updated": row.updated_count,
        "unchanged": row.unchanged_count, "stale": row.stale_count,
        "diff_summary": row.diff_summary or {}, "error_message": row.error_message or "",
    }
    if include_snapshot:
        result["snapshot"] = row.snapshot or {}
    return result


@router.get("/sync-runs")
def list_sync_runs(
    source: Literal["zabbix", "vcenter"] | None = None,
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(IntegrationSyncRun)
    if source:
        query = query.filter(IntegrationSyncRun.source_type == source)
    return [_run_response(row) for row in query.order_by(IntegrationSyncRun.started_at.desc()).limit(limit)]


@router.get("/sync-runs/{run_id}")
def get_sync_run(run_id: int, db: Session = Depends(get_db)):
    row = db.get(IntegrationSyncRun, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="同步记录不存在")
    return _run_response(row, include_snapshot=True)


@router.get("/storage")
def list_integration_storage(
    source: Literal["zabbix", "vcenter"] | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(IntegrationStorage)
    if source:
        query = query.filter(IntegrationStorage.source_type == source)
    return [{
        "id": row.id, "source_type": row.source_type, "external_id": row.external_id,
        "name": row.name, "type": row.storage_type, "capacity": row.capacity,
        "used_space": row.used_space, "free_space": row.free_space,
        "accessible": row.accessible, "vm_count": row.vm_count,
        "sync_state": row.sync_state, "stale_since": row.stale_since,
        "last_synced_at": row.last_synced_at,
    } for row in query.order_by(IntegrationStorage.source_type, IntegrationStorage.name)]


@router.post("/vms/{vm_id}/stale")
def manage_stale_vm(
    vm_id: int,
    payload: IntegrationStaleActionRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    vm = db.get(VMInstance, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    old_source = vm.source_type
    if payload.action == "confirm":
        vm.sync_state = "stale_confirmed"
    elif payload.action == "detach":
        vm.source_type = "manual"
        vm.external_id = None
        vm.source_endpoint = ""
        vm.sync_state = "manual"
        vm.stale_since = None
        vm.storage_assets = []
    else:
        vm.sync_state = "active"
        vm.stale_since = None
    db.commit()
    request.state.audit_action = f"integration.vm_stale.{payload.action}"
    request.state.audit_resource_type = "vm_inventory"
    request.state.audit_resource_id = str(vm.id)
    request.state.audit_detail = {"source": old_source, "state": vm.sync_state}
    return {"ok": True, "id": vm.id, "sync_state": vm.sync_state}


@router.post("/storage/{storage_id}/stale")
def manage_stale_storage(
    storage_id: int,
    payload: IntegrationStaleActionRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    row = db.get(IntegrationStorage, storage_id)
    if not row:
        raise HTTPException(status_code=404, detail="存储资产不存在")
    if payload.action == "detach":
        raise HTTPException(status_code=400, detail="外部存储不支持转为手工，请选择确认或恢复")
    row.sync_state = "stale_confirmed" if payload.action == "confirm" else "active"
    if payload.action == "restore":
        row.stale_since = None
    db.commit()
    request.state.audit_action = f"integration.storage_stale.{payload.action}"
    request.state.audit_resource_type = "integration_storage"
    request.state.audit_resource_id = str(row.id)
    request.state.audit_detail = {"source": row.source_type, "state": row.sync_state}
    return {"ok": True, "id": row.id, "sync_state": row.sync_state}
