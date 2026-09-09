"""Command configuration service.

Manages a JSON-based configuration file that stores all SSH commands
used for device data collection. Users can customize these commands
through the Web UI without modifying source code.

The commands.json file is stored in the data directory (next to the exe
in portable mode, or in the project root in development mode).

JSON schema (as of v1.9.35):
{
    "_type_labels": { "cisco_ios": "Cisco IOS", "huawei_vrp": "Huawei VRP", ... },
    "cisco_ios": { "running_config": {...}, ... },
    "huawei_vrp": { ... },
    ...
}

The "_type_labels" key is reserved (leading underscore) and stores the
human-readable display name for each device type. Built-in types have
sensible defaults so old commands.json files (which only had the command
maps) keep working as-is.
"""
import os
import json
import logging
import re
from pathlib import Path
from typing import Optional

from app.config import settings, DATA_DIR

logger = logging.getLogger(__name__)

# Path to the editable commands.json file (respects custom data dir via
# CISCO_NM_DATA_DIR / --data-dir)
COMMANDS_FILE = DATA_DIR / "commands.json"

# Reserved top-level keys (underscore prefix) used for metadata within
# commands.json. User-defined device types must NOT clash with these.
RESERVED_META_KEYS = ("_type_labels",)

# Built-in default device types (commands populated from DEFAULT_COMMANDS).
# Their labels (and the labels of any custom types added at runtime) live in
# commands.json under the reserved key "_type_labels". When that key is
# missing (e.g. commands.json from an older release), we fall back to the
# hard-coded BUILTIN_DEVICE_TYPE_LABELS dict below.
BUILTIN_DEVICE_TYPE_KEYS = ("cisco_ios", "cisco_xe", "cisco_nxos", "cisco_wlc_ssh")

# Display labels for the built-in types. Acts as the source of truth for the
# 4 tabs that ship by default; custom types take their labels from
# commands.json instead.
BUILTIN_DEVICE_TYPE_LABELS = {
    "cisco_ios": "Cisco IOS",
    "cisco_xe": "Cisco IOS-XE",
    "cisco_nxos": "Cisco NX-OS",
    "cisco_wlc_ssh": "Cisco WLC (AireOS)",
}

# Legacy constant kept for back-compat with code that imported the old
# name. Returns the merged label map (built-ins + user-defined types).
def get_device_type_labels() -> dict:
    """Return human-readable labels for every known device type.

    Merges the hard-coded built-in labels with any user-defined types
    recorded in commands.json under the "_type_labels" key.
    """
    return _all_labels()

# Default commands for each device type
# Structure: { device_type: { command_key: { "command": str, "description": str } } }
DEFAULT_COMMANDS = {
    "cisco_ios": {
        "running_config": {
            "command": "show running-config",
            "description": "获取设备运行配置",
            "delay_factor": 2.0,
        },
        "cdp_neighbors": {
            "command": "show cdp neighbors detail",
            "description": "获取CDP邻居详细信息",
            "delay_factor": 1.5,
        },
        "lldp_neighbors": {
            "command": "show lldp neighbors detail",
            "description": "获取LLDP邻居详细信息",
            "delay_factor": 1.5,
        },
        "show_version": {
            "command": "show version",
            "description": "获取设备版本信息（型号、序列号、运行时间等）",
            "delay_factor": 1.5,
        },
        "show_inventory": {
            "command": "show inventory",
            "description": "获取设备硬件清单（序列号、模块等）",
            "delay_factor": 1.5,
        },
        "show_interfaces": {
            "command": "show ip interface brief",
            "description": "获取接口状态摘要",
            "delay_factor": 1.5,
        },
        "show_interface_description": {
            "command": "show interfaces description",
            "description": "获取接口描述信息",
            "delay_factor": 1.0,
        },
        "show_environment": {
            "command": "show env all",
            "description": "获取环境信息（电源、温度、风扇）",
            "delay_factor": 1.0,
        },
        "show_cpu": {
            "command": "show processes cpu",
            "description": "获取CPU利用率（5秒/1分钟/5分钟）",
            "delay_factor": 1.0,
        },
        "show_memory": {
            "command": "show processes memory",
            "description": "获取内存使用情况",
            "delay_factor": 1.0,
        },
        "save_config": {
            "command": "write memory",
            "description": "保存配置到启动配置",
            "delay_factor": 2.0,
        },
    },
    "cisco_xe": {
        "running_config": {
            "command": "show running-config",
            "description": "获取设备运行配置",
            "delay_factor": 2.0,
        },
        "cdp_neighbors": {
            "command": "show cdp neighbors detail",
            "description": "获取CDP邻居详细信息",
            "delay_factor": 1.5,
        },
        "lldp_neighbors": {
            "command": "show lldp neighbors detail",
            "description": "获取LLDP邻居详细信息",
            "delay_factor": 1.5,
        },
        "show_version": {
            "command": "show version",
            "description": "获取设备版本信息",
            "delay_factor": 1.5,
        },
        "show_inventory": {
            "command": "show inventory",
            "description": "获取设备硬件清单",
            "delay_factor": 1.5,
        },
        "show_interfaces": {
            "command": "show ip interface brief",
            "description": "获取接口状态摘要",
            "delay_factor": 1.5,
        },
        "show_interface_description": {
            "command": "show interfaces description",
            "description": "获取接口描述信息",
            "delay_factor": 1.0,
        },
        "show_environment": {
            "command": "show env all",
            "description": "获取环境信息",
            "delay_factor": 1.0,
        },
        "show_cpu": {
            "command": "show processes cpu",
            "description": "获取CPU利用率",
            "delay_factor": 1.0,
        },
        "show_memory": {
            "command": "show processes memory",
            "description": "获取内存使用情况",
            "delay_factor": 1.0,
        },
        "save_config": {
            "command": "write memory",
            "description": "保存配置",
            "delay_factor": 2.0,
        },
    },
    "cisco_nxos": {
        "running_config": {
            "command": "show running-config",
            "description": "获取NX-OS设备运行配置",
            "delay_factor": 2.0,
        },
        "cdp_neighbors": {
            "command": "show cdp neighbors detail",
            "description": "获取CDP邻居详细信息",
            "delay_factor": 1.5,
        },
        "lldp_neighbors": {
            "command": "show lldp neighbors detail",
            "description": "获取LLDP邻居详细信息",
            "delay_factor": 1.5,
        },
        "show_version": {
            "command": "show version",
            "description": "获取设备版本信息",
            "delay_factor": 1.5,
        },
        "show_inventory": {
            "command": "show inventory",
            "description": "获取设备硬件清单",
            "delay_factor": 1.5,
        },
        "show_interfaces": {
            "command": "show interface status",
            "description": "获取接口状态",
            "delay_factor": 1.5,
        },
        "show_interface_description": {
            "command": "show interface description",
            "description": "获取接口描述",
            "delay_factor": 1.0,
        },
        "show_environment": {
            "command": "show environment",
            "description": "获取环境信息",
            "delay_factor": 1.0,
        },
        "show_cpu": {
            "command": "show processes cpu",
            "description": "获取CPU利用率",
            "delay_factor": 1.0,
        },
        "show_memory": {
            "command": "show processes memory",
            "description": "获取内存使用情况",
            "delay_factor": 1.0,
        },
        "save_config": {
            "command": "copy running-config startup-config",
            "description": "保存配置",
            "delay_factor": 2.0,
        },
    },
    "cisco_wlc_ssh": {
        "running_config": {
            "command": "show run-config",
            "description": "获取WLC运行配置",
            "delay_factor": 2.0,
        },
        "cdp_neighbors": {
            "command": "show cdp neighbors detail",
            "description": "获取CDP邻居信息",
            "delay_factor": 1.5,
        },
        "lldp_neighbors": {
            "command": "show lldp neighbors detail",
            "description": "获取LLDP邻居信息",
            "delay_factor": 1.5,
        },
        "show_version": {
            "command": "show sysinfo",
            "description": "获取WLC系统信息",
            "delay_factor": 1.5,
        },
        "show_inventory": {
            "command": "show inventory",
            "description": "获取硬件清单",
            "delay_factor": 1.5,
        },
        "show_interfaces": {
            "command": "show interface summary",
            "description": "获取接口摘要",
            "delay_factor": 1.5,
        },
        "show_interface_description": {
            "command": "show interface detail",
            "description": "获取接口详情",
            "delay_factor": 1.0,
        },
        "show_environment": {
            "command": "show env all",
            "description": "获取环境信息",
            "delay_factor": 1.0,
        },
        "show_cpu": {
            "command": "show sysinfo",
            "description": "获取WLC系统负载信息",
            "delay_factor": 1.0,
        },
        "show_memory": {
            "command": "show memory",
            "description": "获取内存使用情况",
            "delay_factor": 1.0,
        },
        "save_config": {
            "command": "save config",
            "description": "保存WLC配置",
            "delay_factor": 2.0,
        },
    },
}

# Human-readable labels for command keys
COMMAND_LABELS = {
    "running_config": "运行配置备份",
    "cdp_neighbors": "CDP邻居发现",
    "lldp_neighbors": "LLDP邻居发现",
    "show_version": "版本信息",
    "show_inventory": "硬件清单",
    "show_interfaces": "接口状态",
    "show_interface_description": "接口描述",
    "show_environment": "环境信息",
    "show_cpu": "CPU利用率",
    "show_memory": "内存使用",
    "save_config": "保存配置",
}

# ---- Device-type label management (built-ins + custom types) ----

_TYPE_LABEL_KEY = "_type_labels"   # reserved key inside commands.json


def _all_labels() -> dict:
    """Return the full label map (built-ins merged with custom types)."""
    data = _load_raw()
    user_labels = data.get(_TYPE_LABEL_KEY) or {}
    if not isinstance(user_labels, dict):
        user_labels = {}
    merged = dict(BUILTIN_DEVICE_TYPE_LABELS)
    merged.update({k: str(v) for k, v in user_labels.items()})
    return merged


def get_device_type_label(type_key: str) -> str:
    """Return the label for one device type (or the key itself when unknown)."""
    return _all_labels().get(type_key, type_key)


# ---- JSON I/O helpers ----

def _load_raw() -> dict:
    """Load the raw JSON file (including any "_type_labels" metadata).

    Falls back to an empty dict when the file is missing or invalid.
    """
    try:
        if COMMANDS_FILE.exists():
            with open(COMMANDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        logger.error(f"Failed to load commands.json: {e}")
    return {}


def _ensure_file():
    """Write a fresh commands.json with the built-in types if it is missing.

    The initial file contains the four built-in types and their labels, so
    users see tabs immediately on first launch.
    """
    if COMMANDS_FILE.exists():
        return
    os.makedirs(COMMANDS_FILE.parent, exist_ok=True)
    initial = {
        _TYPE_LABEL_KEY: dict(BUILTIN_DEVICE_TYPE_LABELS),
    }
    for key in BUILTIN_DEVICE_TYPE_KEYS:
        initial[key] = json.loads(json.dumps(DEFAULT_COMMANDS[key]))
    _write_raw(initial)
    logger.info(f"Created default commands.json at {COMMANDS_FILE}")


def _write_raw(data: dict):
    """Persist the full JSON content (commands + labels)."""
    os.makedirs(COMMANDS_FILE.parent, exist_ok=True)
    with open(COMMANDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"commands.json updated at {COMMANDS_FILE}")


# ---- Public command-config API (legacy) ----

def ensure_commands_file():
    """Create commands.json with defaults if it doesn't exist."""
    _ensure_file()


def load_commands() -> dict:
    """Return the command map (no metadata). Keys are device-type keys.

    A type's command map is a dict of {command_key: {command, description,
    delay_factor}}. Types with no persisted commands get a default map.
    """
    _ensure_file()
    data = _load_raw()
    result = {}
    # Built-ins always have a default map; merge in any user customisations.
    for key in BUILTIN_DEVICE_TYPE_KEYS:
        result[key] = json.loads(json.dumps(DEFAULT_COMMANDS[key]))
    for key, value in data.items():
        if key in RESERVED_META_KEYS or not isinstance(value, dict):
            continue
        result[key] = value
    return result


def save_commands(commands: dict):
    """Replace the command map in commands.json, preserving any labels.

    Accepts the same shape as `load_commands()` returns. The reserved
    metadata key (_type_labels) is preserved so users keep their custom
    type names across full-config overwrites.
    """
    existing = _load_raw()
    if _TYPE_LABEL_KEY in existing and isinstance(existing[_TYPE_LABEL_KEY], dict):
        # Drop any labels for types that no longer exist, otherwise
        # preserve the user's custom-name edits.
        kept_labels = {k: v for k, v in existing[_TYPE_LABEL_KEY].items() if k in commands}
        merged_labels = {**kept_labels, **{
            k: BUILTIN_DEVICE_TYPE_LABELS.get(k, v) for k, v in kept_labels.items()
            if k in BUILTIN_DEVICE_TYPE_LABELS
        }}
        existing[_TYPE_LABEL_KEY] = merged_labels
    # Replace the command payloads.
    for key, value in commands.items():
        existing[key] = value
    # Drop any command keys that no longer have a payload.
    for key in list(existing.keys()):
        if key in RESERVED_META_KEYS:
            continue
        if key not in commands:
            existing.pop(key, None)
    _write_raw(existing)


def reset_commands():
    """Reset commands to defaults (keeps any user-added types)."""
    data = _load_raw()
    data[_TYPE_LABEL_KEY] = {**BUILTIN_DEVICE_TYPE_LABELS, **data.get(_TYPE_LABEL_KEY, {})}
    for key in BUILTIN_DEVICE_TYPE_KEYS:
        data[key] = json.loads(json.dumps(DEFAULT_COMMANDS[key]))
    # Drop any non-built-in types: a full reset clears custom additions too.
    for key in list(data.keys()):
        if key in RESERVED_META_KEYS:
            continue
        if key not in BUILTIN_DEVICE_TYPE_KEYS:
            data.pop(key, None)
    _write_raw(data)
    return load_commands()


# ---- Device-type CRUD ----

def _slugify(label: str) -> str:
    """Convert a human label into a safe device-type key.

    Examples:
        "Huawei VRP"        -> "huawei_vrp"
        "Cisco Wireless AP" -> "cisco_wireless_ap"
        "Juniper (Junos)"   -> "juniper_junos"
    """
    s = (label or "").strip().lower()
    # Replace anything non-alphanumeric with underscore, then collapse runs.
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "custom_type"


def add_device_type(label: str, commands: dict | None = None) -> dict:
    """Register a new device type.

    Args:
        label: Human-readable display name (required, e.g. "Huawei VRP").
        commands: Optional initial command map; defaults to the cisco_ios
            template so users can start tweaking right away.

    Returns:
        {"key": "huawei_vrp", "label": "Huawei VRP", "created": True}

    Raises:
        ValueError when the resolved key already exists.
    """
    label = (label or "").strip()
    if not label:
        raise ValueError("设备类型名称不能为空")

    existing_data = _load_raw()
    existing_labels = dict(BUILTIN_DEVICE_TYPE_LABELS)
    if _TYPE_LABEL_KEY in existing_data and isinstance(existing_data[_TYPE_LABEL_KEY], dict):
        existing_labels.update(existing_data[_TYPE_LABEL_KEY])

    base_key = _slugify(label)
    key = base_key
    suffix = 2
    while key in existing_labels or key in BUILTIN_DEVICE_TYPE_LABELS or key in DEFAULT_COMMANDS:
        key = f"{base_key}_{suffix}"
        suffix += 1
    if key in existing_data and not isinstance(existing_data.get(key), dict):
        # Reserved metadata key collision (shouldn't happen because we slugify away from '_').
        raise ValueError(f"设备类型 key '{key}' 与保留键冲突，请换一个名称")

    initial_cmds = commands or json.loads(json.dumps(DEFAULT_COMMANDS["cisco_ios"]))
    existing_data[key] = initial_cmds
    existing_labels[key] = label
    existing_data[_TYPE_LABEL_KEY] = existing_labels
    _write_raw(existing_data)
    return {"key": key, "label": label, "created": True}


def delete_device_type(type_key: str) -> dict:
    """Remove a custom device type.

    Built-in types (cisco_ios, cisco_xe, cisco_nxos, cisco_wlc_ssh) cannot
    be removed; clear this in the UI before deletion.

    Raises:
        ValueError for built-in types or unknown types.
    """
    if type_key in BUILTIN_DEVICE_TYPE_LABELS:
        raise ValueError(f"内置设备类型「{type_key}」不可删除")
    data = _load_raw()
    if type_key not in data:
        raise ValueError(f"设备类型「{type_key}」不存在")
    data.pop(type_key, None)
    if _TYPE_LABEL_KEY in data and isinstance(data[_TYPE_LABEL_KEY], dict):
        data[_TYPE_LABEL_KEY].pop(type_key, None)
    _write_raw(data)
    return {"deleted": type_key}


def rename_device_type_label(type_key: str, new_label: str) -> dict:
    """Update the display label for a device type (key unchanged)."""
    new_label = (new_label or "").strip()
    if not new_label:
        raise ValueError("设备类型名称不能为空")
    data = _load_raw()
    labels = data.get(_TYPE_LABEL_KEY) or {}
    if not isinstance(labels, dict):
        labels = {}
    # Ensure the type actually exists (either built-in or in commands).
    known_keys = set(BUILTIN_DEVICE_TYPE_LABELS) | set(load_commands().keys())
    if type_key not in known_keys:
        raise ValueError(f"设备类型「{type_key}」不存在")
    labels[type_key] = new_label
    data[_TYPE_LABEL_KEY] = labels
    _write_raw(data)
    return {"key": type_key, "label": new_label}


def list_device_types() -> list:
    """Return all device types (built-in + custom) ordered built-in first."""
    data = _load_raw()
    labels = data.get(_TYPE_LABEL_KEY) or {}
    if not isinstance(labels, dict):
        labels = {}
    merged = []
    for k in BUILTIN_DEVICE_TYPE_KEYS:
        merged.append({"key": k, "label": BUILTIN_DEVICE_TYPE_LABELS[k], "builtin": True})
    for k, v in labels.items():
        if k in BUILTIN_DEVICE_TYPE_LABELS:
            continue
        merged.append({"key": k, "label": str(v), "builtin": False})
    return merged


# ---- Command lookup (legacy) ----

def get_command(device_type: str, command_key: str) -> str:
    """Get a specific command for a device type.

    Falls back to cisco_ios defaults if the device type is not found.
    Falls back to the command_key itself if nothing is found.
    """
    commands = load_commands()

    # Map device type to config key
    type_key = _map_device_type(device_type)

    # Try the specific device type first, then fall back to cisco_ios
    for dt in [type_key, "cisco_ios"]:
        if dt in commands and command_key in commands[dt]:
            return commands[dt][command_key].get("command", "")

    # Ultimate fallback: use the key as a command
    return command_key.replace("_", " ")


def get_command_delay(device_type: str, command_key: str) -> float:
    """Get the delay factor for a specific command."""
    commands = load_commands()
    type_key = _map_device_type(device_type)

    for dt in [type_key, "cisco_ios"]:
        if dt in commands and command_key in commands[dt]:
            return commands[dt][command_key].get("delay_factor", 1.5)

    return 1.5


# Per-command fallback lists. When the primary command yields empty/unhelpful
# output, info_service tries these in order. Order matters: cheapest / most
# universal first.
COMMAND_FALLBACKS = {
    "show_memory": [
        # IOS / IOS-XE summary memory (Processor Pool Total)
        "show memory",
        # IOS-XE platform-specific
        "show platform software status control-processor",
        # NX-OS
        "show system resources",
    ],
    "show_cpu": [
        # NX-OS uses a different verb
        "show system resources",
    ],
    "show_interfaces": [
        # IOS-XE sometimes renders blank or different headers
        "show interfaces status",
        # NX-OS
        "show interface status",
    ],
}


def get_command_with_fallbacks(device_type: str, command_key: str) -> list:
    """Return primary command followed by fallback commands.

    Useful when the primary command returns empty output (e.g. wrong OS variant
    on the device) and we want to try alternative commands automatically.
    """
    primary = get_command(device_type, command_key)
    fallbacks = [c for c in COMMAND_FALLBACKS.get(command_key, []) if c != primary]
    return [primary] + fallbacks


def _map_device_type(device_type: str) -> str:
    """Map the device type from the model to the config key."""
    mapping = {
        "cisco_ios": "cisco_ios",
        "cisco_ios_xe": "cisco_xe",
        "cisco_xe": "cisco_xe",
        "cisco_nxos": "cisco_nxos",
        "cisco_wlc": "cisco_wlc_ssh",
        "cisco_wlc_ssh": "cisco_wlc_ssh",
        "cisco_ap": "cisco_ios",
    }
    return mapping.get(device_type, "cisco_ios")
