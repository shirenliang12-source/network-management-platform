"""Command configuration API routes.

Allows users to view, edit, and reset the SSH commands used for
device data collection through the Web UI. From v1.9.35, device-type
groups (the tabs on the command config page) can be added, renamed,
and deleted via the UI without touching commands.json directly.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Device, CredentialProfile
from typing import Optional

from app.api_models import AllCommandsUpdateRequest, CommandsUpdateRequest, StrictRequest

from app.services.command_config import (
    load_commands, save_commands, reset_commands,
    add_device_type, delete_device_type, rename_device_type_label,
    list_device_types,
    _all_labels,   # runtime snapshot of merged built-in + custom type labels
    COMMAND_LABELS, DEFAULT_COMMANDS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/commands", tags=["commands"])


class DeviceTypeCreate(StrictRequest):
    """Create a new device-type group."""
    label: str
    base_on: Optional[str] = "cisco_ios"   # copy commands from an existing type


class DeviceTypeLabelUpdate(StrictRequest):
    """Rename a device type's display label."""
    label: str


class DeviceTypeDriverUpdate(StrictRequest):
    driver: str


@router.get("/types/drivers")
def list_type_drivers():
    from netmiko.ssh_dispatcher import CLASS_MAPPER
    from app.services.command_config import resolve_device_driver
    return {"drivers": sorted(key for key in CLASS_MAPPER if "telnet" not in key),
            "mapping": {t["key"]: resolve_device_driver(t["key"]) for t in list_device_types()}}


@router.put("/types/{device_type}/driver")
def update_type_driver(device_type: str, body: DeviceTypeDriverUpdate):
    from app.services.command_config import set_device_driver
    try:
        set_device_driver(device_type, body.driver)
        return {"driver": body.driver}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("")
def get_all_commands():
    """Get all command configurations for all device types."""
    commands = load_commands()
    return {
        "commands": commands,
        "labels": COMMAND_LABELS,
        "device_type_labels": _all_labels(),
        "device_types": list_device_types(),
    }


# ---- Device-type group CRUD (v1.9.35+) ----
# NOTE: these /types/... routes MUST be registered before the generic
# /{device_type} routes below, otherwise FastAPI matches
# DELETE /types/{x} against DELETE /{device_type}/{command_key}.

@router.get("/types/all")
def list_types():
    """Return all device types (built-in + custom)."""
    return {"device_types": list_device_types()}


@router.post("/types")
def create_device_type(body: DeviceTypeCreate):
    """Create a new device-type group with its own command map.

    The initial command map is cloned from `base_on` (defaults to
    "cisco_ios") so the new type gets the same set of probes as a normal
    Cisco IOS device. The user can then tweak the commands for that
    specific vendor/role.
    """
    try:
        initial = None
        if body.base_on:
            base = load_commands().get(body.base_on)
            if base:
                import json as _json
                initial = _json.loads(_json.dumps(base))  # deep copy
        if body.base_on and body.base_on not in load_commands():
            raise ValueError("继承的设备类型不存在")
        result = add_device_type(body.label, initial, body.base_on or "cisco_ios")
        return {
            "message": f"已创建设备类型「{result['label']}」",
            **result,
            "device_types": list_device_types(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("add_device_type failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/types/{device_type}")
def remove_device_type(device_type: str, db: Session = Depends(get_db)):
    """Delete a custom device-type group (built-ins cannot be removed).

    Returns the number of devices currently using the type so the UI
    can warn before deletion (the deletion still proceeds — devices
    fall back to cisco_ios at collection time).
    """
    try:
        if db.query(Device).filter_by(device_type=device_type).first() or db.query(CredentialProfile).filter_by(device_type=device_type).first():
            raise ValueError("该类型仍被设备或凭据模板使用，请先修改这些记录的类型，再删除")
        result = delete_device_type(device_type)
        return {
            "message": f"已删除设备类型「{device_type}」",
            **result,
            "device_types": list_device_types(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/types/{device_type}/label")
def update_device_type_label(device_type: str, body: DeviceTypeLabelUpdate):
    """Rename a device-type's display label (the underlying key stays)."""
    try:
        result = rename_device_type_label(device_type, body.label)
        return {
            "message": f"已将「{device_type}」重命名为「{result['label']}」",
            **result,
            "device_types": list_device_types(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/meta/labels")
def get_labels():
    """Get human-readable labels for command keys and device types."""
    return {
        "command_labels": COMMAND_LABELS,
        "device_type_labels": _all_labels(),
        "device_types": list_device_types(),
    }


@router.get("/{device_type}")
def get_commands_by_type(device_type: str):
    """Get commands for a specific device type."""
    commands = load_commands()
    if device_type not in commands:
        raise HTTPException(status_code=404, detail=f"Device type '{device_type}' not found")
    return {
        "device_type": device_type,
        "label": _all_labels().get(device_type, device_type),
        "commands": commands[device_type],
        "labels": {k: COMMAND_LABELS.get(k, k) for k in commands[device_type]},
    }


@router.delete("/{device_type}/{command_key}")
def delete_command(device_type: str, command_key: str):
    """Delete a single custom command key from a device type."""
    commands = load_commands()
    if device_type not in commands:
        raise HTTPException(status_code=404, detail=f"Device type '{device_type}' not found")
    if command_key not in commands[device_type]:
        raise HTTPException(status_code=404, detail=f"Command '{command_key}' not found")
    del commands[device_type][command_key]
    save_commands(commands)
    return {"message": f"Command '{command_key}' deleted", "commands": commands[device_type]}


@router.put("/{device_type}")
def update_commands(device_type: str, update: CommandsUpdateRequest):
    commands = load_commands()
    if device_type not in commands:
        raise HTTPException(status_code=404, detail=f"Device type '{device_type}' not found")
    if update.device_type != device_type:
        raise HTTPException(status_code=400, detail="body.device_type 必须与路径一致")

    # Merge the updates
    for key, value in update.commands.items():
        if key in commands[device_type]:
            commands[device_type][key]["command"] = value.command
            if "description" in value.model_fields_set:
                commands[device_type][key]["description"] = value.description
            if "delay_factor" in value.model_fields_set:
                commands[device_type][key]["delay_factor"] = value.delay_factor
        else:
            # Add new command
            commands[device_type][key] = value.model_dump()

    save_commands(commands)
    return {"message": f"Commands updated for {device_type}", "commands": commands[device_type]}


@router.put("")
def update_all_commands(update_data: AllCommandsUpdateRequest):
    """Replace all command configurations."""
    commands = {
        device_type: {key: value.model_dump() for key, value in entries.items()}
        for device_type, entries in update_data.commands.items()
    }
    save_commands(commands)
    return {"message": "All commands updated", "commands": commands}


@router.post("/reset")
def reset_all_commands():
    """Reset all commands to defaults."""
    commands = reset_commands()
    return {"message": "Commands reset to defaults", "commands": commands}


@router.post("/reset/{device_type}")
def reset_device_type_commands(device_type: str):
    """Reset commands for a specific device type to defaults."""
    if device_type not in DEFAULT_COMMANDS:
        raise HTTPException(status_code=404, detail=f"Device type '{device_type}' not found")

    commands = load_commands()
    commands[device_type] = DEFAULT_COMMANDS[device_type]
    save_commands(commands)
    return {"message": f"Commands reset for {device_type}", "commands": commands[device_type]}
