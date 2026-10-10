"""Strict request models shared by API routers.

Response schemas remain in :mod:`app.schemas`; this module intentionally
contains only request payloads and rejects unknown fields so client mistakes
cannot silently change server behaviour.
"""
import ipaddress
import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import MODULE_KEYS


_COMMAND_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _validate_command_keys(commands: dict, label: str) -> dict:
    invalid = [str(key) for key in commands if not _COMMAND_KEY_RE.fullmatch(str(key))]
    if invalid:
        raise ValueError(f"{label} 只能包含字母、数字、下划线和连字符: {', '.join(invalid[:5])}")
    return commands


class StrictRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_max_length=4000,
        hide_input_in_errors=True,
    )

    @field_validator("username", check_fields=False)
    @classmethod
    def normalize_username(cls, username: Optional[str]) -> Optional[str]:
        """Normalize identifiers without ever transforming password fields."""
        if username is None:
            return None
        username = username.strip()
        if not username:
            raise ValueError("用户名不能为空")
        return username


class InventorySelectionRequest(StrictRequest):
    ids: List[int] = Field(min_length=1, max_length=5000)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, values):
        if any(value <= 0 for value in values):
            raise ValueError("ID 必须为正整数")
        return list(dict.fromkeys(values))


class CompanyCatalogRequest(StrictRequest):
    names: List[str] = Field(max_length=500)

    @field_validator("names")
    @classmethod
    def validate_names(cls, values):
        names = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 100 for value in names):
            raise ValueError("公司名称最长 100 个字符")
        return names


class LoginRequest(StrictRequest):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(default="", max_length=4096)


class PasswordChangeRequest(StrictRequest):
    current_password: str = Field(max_length=4096)
    username: Optional[str] = Field(default=None, min_length=1, max_length=100)
    password: str = Field(default="", max_length=4096)
    confirm_password: str = Field(default="", max_length=4096)


class UserCreateRequest(StrictRequest):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=4096)
    is_superuser: bool = False
    is_active: bool = True
    modules: List[str] = Field(default_factory=list, max_length=len(MODULE_KEYS))

    @field_validator("modules")
    @classmethod
    def validate_modules(cls, modules: List[str]) -> List[str]:
        invalid = sorted(set(modules) - set(MODULE_KEYS))
        if invalid:
            raise ValueError(f"未知模块: {', '.join(invalid)}")
        return list(dict.fromkeys(modules))


class UserUpdateRequest(StrictRequest):
    username: Optional[str] = Field(default=None, min_length=1, max_length=100)
    password: Optional[str] = Field(default=None, max_length=4096)
    is_superuser: Optional[bool] = None
    is_active: Optional[bool] = None
    modules: Optional[List[str]] = Field(default=None, max_length=len(MODULE_KEYS))

    @field_validator("password")
    @classmethod
    def validate_optional_password(cls, password: Optional[str]) -> Optional[str]:
        if password and len(password) < 12:
            raise ValueError("密码至少需要 12 个字符")
        return password

    @field_validator("modules")
    @classmethod
    def validate_optional_modules(cls, modules: Optional[List[str]]) -> Optional[List[str]]:
        if modules is None:
            return None
        invalid = sorted(set(modules) - set(MODULE_KEYS))
        if invalid:
            raise ValueError(f"未知模块: {', '.join(invalid)}")
        return list(dict.fromkeys(modules))


class BatchApplyProfileRequest(StrictRequest):
    device_ids: List[int] = Field(min_length=1, max_length=1000)
    profile_id: int = Field(gt=0)

    @field_validator("device_ids")
    @classmethod
    def unique_positive_ids(cls, device_ids: List[int]) -> List[int]:
        if any(device_id <= 0 for device_id in device_ids):
            raise ValueError("device_ids 必须为正整数")
        return list(dict.fromkeys(device_ids))


class LogSettingsRequest(StrictRequest):
    retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    audit_retention_days: Optional[int] = Field(default=None, ge=30, le=3650)
    task_retention_days: Optional[int] = Field(default=None, ge=7, le=3650)
    sync_retention_days: Optional[int] = Field(default=None, ge=7, le=3650)
    log_level: Optional[Literal["DEBUG", "INFO", "WARN", "ERROR"]] = None


class BackupReviewRequest(StrictRequest):
    status: Literal["pending", "expected", "unexpected", "ignored"]
    note: str = Field(default="", max_length=1000)


class ZabbixConfigRequest(StrictRequest):
    url: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(default="", max_length=4096)
    verify_ssl: bool = True
    timeout: int = Field(default=20, ge=3, le=120)
    source_ip: str = Field(default="", max_length=45)

    @field_validator("source_ip")
    @classmethod
    def validate_source_ip(cls, value: str) -> str:
        value = (value or "").strip()
        if value:
            parsed = ipaddress.ip_address(value)
            if parsed.version != 4:
                raise ValueError("出口网卡暂仅支持 IPv4 地址")
        return value


class NICSelectionRequest(StrictRequest):
    source_ip: str = Field(default="", max_length=45)

    @field_validator("source_ip")
    @classmethod
    def validate_source_ip(cls, value: str) -> str:
        value = (value or "").strip()
        if value:
            parsed = ipaddress.ip_address(value)
            if parsed.version != 4:
                raise ValueError("默认出口网卡暂仅支持 IPv4 地址")
        return value


class VCenterConfigRequest(StrictRequest):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=443, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(default="", max_length=4096)
    verify_ssl: bool = True
    timeout: int = Field(default=30, ge=3, le=120)


class IntegrationImportRequest(StrictRequest):
    external_ids: List[str] = Field(min_length=1, max_length=500)
    update_existing: bool = False

    @field_validator("external_ids")
    @classmethod
    def normalize_external_ids(cls, external_ids: List[str]) -> List[str]:
        cleaned = [str(value).strip() for value in external_ids]
        if any(not value or len(value) > 255 for value in cleaned):
            raise ValueError("外部对象 ID 不能为空且不能超过 255 个字符")
        return list(dict.fromkeys(cleaned))


class IntegrationSyncRequest(StrictRequest):
    """Execute a full or selected synchronization after a preview."""
    external_ids: Optional[List[str]] = Field(default=None, max_length=2000)
    update_existing: bool = True
    mark_missing: bool = False

    @field_validator("external_ids")
    @classmethod
    def normalize_optional_external_ids(cls, external_ids: Optional[List[str]]) -> Optional[List[str]]:
        if external_ids is None:
            return None
        cleaned = [str(value).strip() for value in external_ids]
        if any(not value or len(value) > 255 for value in cleaned):
            raise ValueError("外部对象 ID 不能为空且不能超过 255 个字符")
        return list(dict.fromkeys(cleaned))


class IntegrationStaleActionRequest(StrictRequest):
    action: Literal["confirm", "detach", "restore"]


class CommandPayload(StrictRequest):
    command: str = Field(max_length=4000)
    description: str = Field(default="", max_length=500)
    delay_factor: float = Field(default=1.5, ge=0.1, le=30)


class CommandsUpdateRequest(StrictRequest):
    device_type: str = Field(min_length=1, max_length=100)
    commands: Dict[str, CommandPayload]

    @field_validator("commands")
    @classmethod
    def validate_command_keys(cls, commands):
        return _validate_command_keys(commands, "命令 key")


class AllCommandsUpdateRequest(StrictRequest):
    commands: Dict[str, Dict[str, CommandPayload]]

    @field_validator("commands")
    @classmethod
    def validate_all_keys(cls, commands):
        _validate_command_keys(commands, "设备类型 key")
        for entries in commands.values():
            _validate_command_keys(entries, "命令 key")
        return commands
