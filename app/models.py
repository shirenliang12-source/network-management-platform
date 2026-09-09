"""SQLAlchemy database models."""
import os
import base64
import json
import uuid
import hashlib
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Float,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.config import settings


_CREDENTIAL_AAD = b"cisco-netmgr-credentials-v2"
_LEGACY_SECRET_KEY = "c1sco-n3tmgR-s3cret-key-2026"


def _credential_key() -> bytes:
    return hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()


def encrypt_password(password: str) -> str:
    """Encrypt a stored credential with authenticated AES-GCM encryption."""
    if not password:
        return ""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ciphertext = AESGCM(_credential_key()).encrypt(
        nonce,
        password.encode("utf-8"),
        _CREDENTIAL_AAD,
    )
    return "v2:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _decrypt_legacy_password(encrypted: str) -> str:
    """Read credentials written by versions that used the fixed XOR key."""
    try:
        raw = bytes.fromhex(encrypted).decode("latin-1")
        key = _LEGACY_SECRET_KEY.encode("utf-8")
        result = [chr(ch ^ key[i % len(key)]) for i, ch in enumerate(raw.encode("latin-1"))]
        return "".join(result)
    except Exception:
        return ""


def decrypt_password(encrypted: str) -> str:
    """Decrypt a stored credential."""
    if not encrypted:
        return ""
    if not encrypted.startswith("v2:"):
        return _decrypt_legacy_password(encrypted)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        payload = base64.urlsafe_b64decode(encrypted[3:].encode("ascii"))
        if len(payload) < 29:
            return ""
        plaintext = AESGCM(_credential_key()).decrypt(
            payload[:12],
            payload[12:],
            _CREDENTIAL_AAD,
        )
        return plaintext.decode("utf-8")
    except Exception:
        return ""


class DeviceGroup(Base):
    """Device group for organizing devices (e.g., core, access, edge)."""
    __tablename__ = "device_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    devices = relationship("Device", back_populates="group")


class CredentialProfile(Base):
    """Reusable SSH credential profile, can be matched by device_type.

    When auto-adding discovered devices, the system looks up a credential
    profile whose device_type matches the inferred type and applies its
    credentials instead of blindly inheriting the source device's.
    """
    __tablename__ = "credential_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    device_type = Column(String(50), nullable=False, default="cisco_ios")
    username = Column(String(100), nullable=False, default="admin")
    password_enc = Column(String(500), default="")
    enable_password_enc = Column(String(500), default="")
    port = Column(Integer, default=22)
    source_ip = Column(String(45), default="")
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password: str):
        self.password_enc = encrypt_password(password)

    def get_password(self) -> str:
        return decrypt_password(self.password_enc)

    def set_enable_password(self, password: str):
        self.enable_password_enc = encrypt_password(password)

    def get_enable_password(self) -> str:
        return decrypt_password(self.enable_password_enc)


class Device(Base):
    """Network device (switch, WLC, AP)."""
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    device_type = Column(String(50), nullable=False, default="cisco_ios")
    # device_type options: cisco_ios, cisco_ios_xe, cisco_nxos, cisco_wlc_ssh, cisco_ap
    group_id = Column(Integer, ForeignKey("device_groups.id"), nullable=True)

    # Company grouping (e.g. different customer/subsidiary networks)
    company = Column(String(100), default="", index=True)

    # Device classification: model (型号) and function/role (功能)
    model = Column(String(100), default="", index=True)
    function = Column(String(100), default="", index=True)

    # Optional link to a reusable credential profile; when set, the profile's
    # credentials take precedence over the device's own stored credentials.
    credential_profile_id = Column(Integer, ForeignKey("credential_profiles.id"), nullable=True)

    # SSH credentials
    username = Column(String(100), nullable=False, default="admin")
    password_enc = Column(String(500), default="")
    enable_password_enc = Column(String(500), default="")
    port = Column(Integer, default=22)

    # Source IP for binding SSH connection (empty = auto/default)
    source_ip = Column(String(45), default="")

    # Status
    is_active = Column(Boolean, default=True)
    last_seen = Column(DateTime, nullable=True)
    last_backup = Column(DateTime, nullable=True)
    last_discovery = Column(DateTime, nullable=True)
    last_info_collection = Column(DateTime, nullable=True)
    status = Column(String(20), default="unknown")  # online, offline, unknown

    # Manually entered production date (Cisco serials don't reliably encode it,
    # so we let the operator override the auto-decoded value)
    production_date_manual = Column(String(50), default="")

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    group = relationship("DeviceGroup", back_populates="devices")
    credential_profile = relationship("CredentialProfile")
    backups = relationship("ConfigBackup", back_populates="device", cascade="all, delete-orphan")
    info_records = relationship("DeviceInfo", back_populates="device", cascade="all, delete-orphan")
    neighbors = relationship("Neighbor", back_populates="device", cascade="all, delete-orphan",
                             foreign_keys="Neighbor.device_id")
    # Additional IP addresses (multi-NIC). The primary management IP stays in
    # `ip_address`; every other IP lives here.
    extra_ips = relationship("DeviceIP", back_populates="device", cascade="all, delete-orphan",
                             order_by="DeviceIP.id", lazy="select")

    def set_password(self, password: str):
        self.password_enc = encrypt_password(password)

    def get_password(self) -> str:
        return decrypt_password(self.password_enc)

    def set_enable_password(self, password: str):
        self.enable_password_enc = encrypt_password(password)

    def get_enable_password(self) -> str:
        return decrypt_password(self.enable_password_enc)


class DeviceIP(Base):
    """额外的设备 IP 地址（多网卡 / 多 IP 场景）。

    主管理 IP 仍保存在 Device.ip_address；其余 IP（如带外管理口、业务口、
    loopback、SVI 等）记录在表中，导入时通过 ip2/ip3… 列写入。
    """
    __tablename__ = "device_ips"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    interface_name = Column(String(100), default="")   # 接口/网卡名（可选）
    is_primary = Column(Boolean, default=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    device = relationship("Device", back_populates="extra_ips")


class ConfigBackup(Base):
    """Configuration backup record."""
    __tablename__ = "config_backups"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    config_text = Column(Text, nullable=False)
    config_hash = Column(String(64), nullable=False, index=True)
    backup_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    file_path = Column(String(500), nullable=True)
    is_changed = Column(Boolean, default=True)  # Whether config changed from previous backup
    change_summary = Column(Text, default="")
    backup_size = Column(Integer, default=0)
    # Operator workflow for configuration changes. Exactly one backup per
    # device can be marked as the intended baseline by application logic.
    review_status = Column(String(20), nullable=False, default="pending", index=True)
    review_note = Column(Text, default="")
    reviewed_by = Column(String(100), default="")
    reviewed_at = Column(DateTime, nullable=True)
    is_baseline = Column(Boolean, nullable=False, default=False, index=True)

    device = relationship("Device", back_populates="backups")


class DeviceInfo(Base):
    """Device hardware/software information snapshot."""
    __tablename__ = "device_info"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    hostname = Column(String(200), default="")
    vendor = Column(String(100), default="Cisco")
    model = Column(String(200), default="")
    os_type = Column(String(50), default="")
    os_version = Column(String(200), default="")
    serial_number = Column(String(100), default="")
    production_date = Column(String(50), default="")  # Final value: manual or auto
    # v1.9.35+: production-date provenance. `production_date_source` is one of
    # "serial" (decoded from the serial itself), "auto" (extracted from raw
    # show output), "manual" (operator-provided override), or "" (unknown).
    production_date_source = Column(String(20), default="")
    production_date_pattern = Column(String(50), default="")
    production_date_raw_match = Column(String(500), default="")
    uptime = Column(String(200), default="")
    uptime_seconds = Column(Integer, default=0)

    # Basic status information (live health snapshot)
    cpu_usage = Column(String(50), default="")          # e.g. "5% / 8% / 7% (5s/1m/5m)"
    memory_usage = Column(String(50), default="")       # e.g. "42% (512M/1.2G)"
    interface_up_count = Column(Integer, default=0)
    interface_down_count = Column(Integer, default=0)

    management_ip = Column(String(45), default="")
    mac_address = Column(String(50), default="")
    location = Column(String(200), default="")
    contact = Column(String(200), default="")
    interfaces = Column(JSON, default=list)  # List of interface info dicts
    raw_data = Column(JSON, default=dict)    # Full raw output for reference
    collected_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    device = relationship("Device", back_populates="info_records")


class Neighbor(Base):
    """CDP/LLDP neighbor relationship."""
    __tablename__ = "neighbors"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    protocol = Column(String(10), default="cdp")  # cdp or lldp
    local_interface = Column(String(100), default="")
    neighbor_name = Column(String(200), default="")
    neighbor_ip = Column(String(45), default="")
    neighbor_interface = Column(String(100), default="")
    neighbor_platform = Column(String(200), default="")
    neighbor_capability = Column(String(200), default="")
    # Try to link to a known device
    neighbor_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    discovered_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    device = relationship("Device", back_populates="neighbors", foreign_keys=[device_id])


class TaskLog(Base):
    """Task execution log."""
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String(50), nullable=False)  # backup, discovery, info, topology
    status = Column(String(20), default="pending")  # pending, running, success, failed, partial
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    total_devices = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    error_details = Column(JSON, default=list)  # List of {device, error} dicts
    summary = Column(Text, default="")


class DataLog(Base):
    """Per-step system log entry for diagnostics (info collection, SSH errors,
    parser failures, etc.). Keeps raw command output and parse results so the
    operator can see why a value is missing or wrong.

    Distinct from TaskLog (which tracks batch-level task execution):
    DataLog records one row per probe / per parse step.
    """
    __tablename__ = "data_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    level = Column(String(20), nullable=False, default="INFO")  # DEBUG / INFO / WARN / ERROR
    category = Column(String(50), nullable=False, index=True)   # ssh / cpu / memory / interface / info / api
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True, index=True)
    device_name = Column(String(200), default="")
    device_ip = Column(String(45), default="")
    action = Column(String(200), default="")   # e.g. "show processes cpu"
    message = Column(Text, default="")         # human-readable summary
    detail = Column(JSON, default=dict)        # parsed_result, duration_ms, etc.
    raw_output = Column(Text, default="")      # raw SSH output (truncated)
    error = Column(Text, default="")


class AuditLog(Base):
    """Immutable record of security-sensitive management actions."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(100), default="", index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), default="", index=True)
    resource_id = Column(String(100), default="")
    method = Column(String(10), default="")
    path = Column(String(500), default="", index=True)
    status_code = Column(Integer, default=0, index=True)
    success = Column(Boolean, default=True, index=True)
    client_ip = Column(String(100), default="")
    detail = Column(JSON, default=dict)


class ScheduleConfig(Base):
    """Scheduled task configuration."""
    __tablename__ = "schedule_configs"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String(50), nullable=False)  # backup, discovery, info
    cron_expression = Column(String(100), nullable=False)
    is_enabled = Column(Boolean, default=True)
    description = Column(String(200), default="")
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    last_run_status = Column(String(20), nullable=True)  # success/failed/partial/running
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IPInventory(Base):
    """IP address inventory: IP segment, subnet, mask, VLAN, usage, company, remarks."""
    __tablename__ = "ip_inventory"

    id = Column(Integer, primary_key=True, index=True)
    ip_segment = Column(String(100), default="", index=True)       # IP段 (e.g. 10.0.1.0-10.0.1.255)
    subnet = Column(String(100), default="", index=True)          # 子网网段 / CIDR (e.g. 10.0.1.0/24)
    mask = Column(String(20), default="")                         # 掩码 (e.g. 255.255.255.0)
    vlan = Column(String(50), default="")                         # VLAN信息 (e.g. VLAN 10 / 10)
    usage = Column(String(255), default="")                       # 用途
    company = Column(String(100), default="", index=True)         # 公司
    remarks = Column(Text, default="")                            # 备注
    sort_order = Column(Integer, default=0, index=True)          # 自定义排序权重（越小越靠前）
    device_id = Column(Integer, nullable=True, index=True)       # 关联设备ID（从设备表获取资产信息）
    asset_sn = Column(String(100), default="")                   # 资产序列号（关联设备自动带出，可手填）
    network_type = Column(String(20), default="有线")             # 网络类型: 有线 / 无线 / 其他
    firewall = Column(String(100), default="")                   # Firewall 名称
    zone_interface_name = Column(String(100), default="")        # Zone/Interface Name
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# IP 地址规划 (IPAM) — NetBox 风格的三层结构：聚合 -> 网段 -> IP 地址
# 统一四态：规划 / 预分配 / 使用中 / 已停用
# ============================================================================

class IPAMAggregate(Base):
    """顶层聚合块（你拥有的大子网），如 10.20.0.0/16。"""
    __tablename__ = "ipam_aggregates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)       # 聚合名称（如「总部大子网」）
    prefix = Column(String(50), nullable=False, index=True)      # CIDR（如 10.20.0.0/16）
    description = Column(Text, default="")                       # 描述 / 备注
    date_added = Column(String(20), default="")                  # 规划日期（YYYY-MM）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IPAMPrefix(Base):
    """网段（从聚合切分出来的子网），可嵌套子网。"""
    __tablename__ = "ipam_prefixes"

    id = Column(Integer, primary_key=True, index=True)
    aggregate_id = Column(Integer, ForeignKey("ipam_aggregates.id"), nullable=True, index=True)
    parent_id = Column(Integer, ForeignKey("ipam_prefixes.id"), nullable=True, index=True)
    prefix = Column(String(50), nullable=False, index=True)      # CIDR（如 10.20.1.0/24）
    status = Column(String(20), default="规划", index=True)      # 规划 / 预分配 / 使用中 / 已停用
    role = Column(String(100), default="")                       # 用途 / 角色（如 办公网 / 服务器区）
    vlan = Column(String(50), default="")                        # VLAN ID
    company = Column(String(100), default="")                    # 公司 / 客户
    firewall = Column(String(100), default="")                   # Firewall 名称
    zone_interface_name = Column(String(100), default="")        # Zone/Interface Name
    description = Column(Text, default="")                       # 描述 / 备注
    is_pool = Column(Boolean, default=False)                    # 是否作为 IP 分配池（可在其中分配具体 IP）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IPAMIPAddress(Base):
    """具体 IP 地址分配，隶属于某个网段（prefix）。"""
    __tablename__ = "ipam_ip_addresses"

    id = Column(Integer, primary_key=True, index=True)
    prefix_id = Column(Integer, ForeignKey("ipam_prefixes.id"), nullable=False, index=True)
    address = Column(String(45), nullable=False, index=True)     # 单个 IP（如 10.20.1.10）
    status = Column(String(20), default="规划", index=True)      # 规划 / 预分配 / 使用中 / 已停用
    allocation_type = Column(String(10), default="静态", index=True)  # 分配类型：静态 / DHCP
    dns_name = Column(String(255), default="")                   # DNS 名称
    description = Column(Text, default="")                       # 描述 / 备注
    assigned_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True, index=True)
    # 手工录入的设备信息（无需预先在「设备」中登记）：设备名称 / 型号 / IP
    device_name = Column(String(200), default="")                # 设备名称（手工录入）
    device_model = Column(String(100), default="")               # 设备型号（手工录入）
    device_ip = Column(String(45), default="")                   # 设备 IP（手工录入，一般等于该 IP 地址）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# 数据中心 (Data Center) — NetBox 风格：站点 (Site) -> 机柜 (Rack)
# 基础字段：公司站点 + 机柜编号 + 可编辑的机柜属性
# ============================================================================

class DCSite(Base):
    """数据中心站点（公司 / 机房物理位置）。"""
    __tablename__ = "dc_sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, index=True)        # 站点名称（如 上海数据中心 A 栋）
    company = Column(String(100), default="")                     # 公司 / 客户
    region = Column(String(100), default="")                      # 区域 / 城市（如 华东 / 上海）
    address = Column(String(300), default="")                    # 地址
    contact_name = Column(String(100), default="")               # 联系人
    contact_phone = Column(String(100), default="")              # 联系电话
    description = Column(Text, default="")                       # 备注
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DCRack(Base):
    """机柜（隶属于某个站点），属性可编辑，UI 展示。"""
    __tablename__ = "dc_racks"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("dc_sites.id"), nullable=False, index=True)
    rack_number = Column(String(50), nullable=False, index=True)  # 机柜编号（如 A01 / R-01）
    name = Column(String(150), default="")                        # 机柜名称（可选）
    role = Column(String(100), default="")                       # 用途 / 角色（服务器区 / 网络区 / ...）
    type = Column(String(50), default="机柜")                     # 类型：机柜 / 开放式机架 / 壁挂式
    width = Column(String(20), default="19英寸")                  # 宽度
    u_height = Column(Integer, default=42)                       # U 数（默认 42U）
    status = Column(String(20), default="在用", index=True)       # 在用 / 规划中 / 预留 / 停用
    serial = Column(String(100), default="")                     # 序列号
    asset_tag = Column(String(100), default="")                  # 资产编号
    location_detail = Column(String(200), default="")            # 具体位置（机房 / 排 / 列）
    description = Column(Text, default="")                       # 备注
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# 资产模块 — 其他 IT 资产 (it_assets) + 服务器/存储 (server_assets)
# 与「网络设备」(devices) 区分：IT 资产是打印机/AP/电话/摄像头等，
# 可关联一台已发现的网络设备（如所连交换机/AP）；服务器存储则关联机柜并指定 U 位。
# ============================================================================

class ITAsset(Base):
    """其他 IT 资产（打印机 / 无线AP / IP电话 / 摄像头 等）。

    独立于「网络设备」，但可关联一台已发现的网络设备（linked_device_id）。
    """
    __tablename__ = "it_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)       # 资产名称
    asset_type = Column(String(50), default="其他", index=True)    # 类型：打印机/无线AP/IP电话/摄像头/其他
    brand = Column(String(100), default="")                     # 品牌
    model = Column(String(200), default="")                      # 型号
    serial = Column(String(100), default="")                     # 序列号
    asset_tag = Column(String(100), default="")                  # 资产编号
    management_ip = Column(String(45), default="")               # 管理 IP
    linked_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True, index=True)  # 关联网络设备
    site_id = Column(Integer, ForeignKey("dc_sites.id"), nullable=True, index=True)          # 所在站点
    location_detail = Column(String(200), default="")            # 具体位置
    rack_id = Column(Integer, ForeignKey("dc_racks.id"), nullable=True, index=True)           # 关联机柜
    rack_position = Column(String(50), default="")               # 机柜内位置（如 U12 / 顶板 / 中层）
    status = Column(String(20), default="在用", index=True)       # 在用 / 备用 / 停用 / 报废
    notes = Column(Text, default="")                             # 备注
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ServerAsset(Base):
    """服务器 / 存储资产，关联机柜并指定 U 位（起始U + 占用U数）。"""
    __tablename__ = "server_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)       # 名称
    category = Column(String(20), default="服务器", index=True)   # 类别：服务器 / 存储
    brand = Column(String(100), default="")                     # 品牌
    model = Column(String(200), default="")                      # 型号
    serial = Column(String(100), default="")                     # 序列号
    asset_tag = Column(String(100), default="")                  # 资产编号
    rack_id = Column(Integer, ForeignKey("dc_racks.id"), nullable=False, index=True)  # 关联机柜
    u_start = Column(Integer, default=1)                         # 起始 U（从 1 开始）
    u_size = Column(Integer, default=1)                          # 占用 U 数
    status = Column(String(20), default="在用", index=True)       # 在用 / 备用 / 停用 / 报废
    management_ip = Column(String(45), default="")               # 管理 IP
    os = Column(String(200), default="")                         # 操作系统
    cpu = Column(String(200), default="")                        # CPU
    memory = Column(String(200), default="")                     # 内存
    storage_desc = Column(String(300), default="")              # 存储说明
    owner = Column(String(100), default="")                      # 负责人
    notes = Column(Text, default="")                             # 备注
    extra_ips = relationship("ServerIP", back_populates="server", cascade="all, delete-orphan",
                             order_by="ServerIP.id", lazy="select")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ServerCategory(Base):
    """服务器/存储的「类别」可维护列表（用户自定义任意名称）。

    替代原先写死的 SERVER_CATEGORIES = ["服务器", "存储"]。
    ServerAsset.category 仍保存名称字符串，便于与历史数据兼容。
    """
    __tablename__ = "server_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True, index=True)
    color = Column(String(20), default="")   # 自定义展示颜色，空则由前端按名称生成
    created_at = Column(DateTime, default=datetime.utcnow)


class ServerIP(Base):
    """额外的服务器/存储 IP 地址（多网卡场景）。

    主管理 IP 仍保存在 ServerAsset.management_ip；其余 IP 记录在表中。
    """
    __tablename__ = "server_ips"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("server_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    is_primary = Column(Boolean, default=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    server = relationship("ServerAsset", back_populates="extra_ips")


# ============================================================================
# 虚拟机清单 (VM Inventory) — 收集汇总 Windows / Linux 等虚拟机的系统信息
# 字段：服务器名称 / 服务器功能 / 多IP / CPU·内存·磁盘资源 / 宿主机名称
# 宿主机(host) 关联「服务器存储」模块中的 ServerAsset，实现与服务器/存储的联动。
# ============================================================================

class VMInstance(Base):
    """虚拟机实例：收集 Windows / Linux 等虚拟机的系统信息。

    - 宿主机通过 host_id 关联「服务器存储」模块中的 ServerAsset；
      同时冗余 host_name，便于宿主机尚未登记时手填，并便于展示/导出。
    - 多 IP 场景：主管理 IP 存 management_ip，其余 IP 存 vm_ips 表。
    """
    __tablename__ = "vm_instances"
    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_vm_instances_source_external"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)        # 服务器名称（虚拟机名称）
    function = Column(String(200), default="", index=True)         # 服务器功能 / 用途
    os_type = Column(String(20), default="Linux", index=True)      # 操作系统类型: Windows / Linux / 其他
    os_version = Column(String(200), default="")                   # 具体版本（Windows Server 2019 / CentOS 7.9 ...）
    status = Column(String(20), default="运行中", index=True)       # 运行状态: 运行中/已关机/挂起/模板/其他

    # 宿主机关联（与「服务器存储」模块联动）
    host_id = Column(Integer, ForeignKey("server_assets.id"), nullable=True, index=True)
    host_name = Column(String(200), default="")                     # 宿主机名称（冗余：关联带出或手填）

    # 资源
    cpu = Column(String(100), default="")                          # CPU（如 4 vCPU / 8核）
    memory = Column(String(100), default="")                        # 内存（如 8 GB / 16G）
    disk_size = Column(String(100), default="")                     # 磁盘大小（如 200 GB / 1T）
    disk_count = Column(Integer, default=1)                         # 磁盘数量（块数）
    storage_lun = Column(String(500), default="")                  # 存储 LUN 信息（如 LUN0:200GB,LUN1:500GB）
    disks = Column(Text, default="")                                # 磁盘明细 JSON：[{"name":"系统盘","size":"100 GB"}]

    management_ip = Column(String(45), default="")                  # 主管理 IP
    notes = Column(Text, default="")                               # 备注
    source_type = Column(String(20), default="manual", nullable=False, index=True) # manual / zabbix / vcenter
    external_id = Column(String(255), nullable=True, index=True)     # 外部平台对象 ID
    source_endpoint = Column(String(500), default="", nullable=False) # 来源地址（不含凭据）
    last_synced_at = Column(DateTime, nullable=True)                 # 最近一次外部同步时间
    sync_state = Column(String(20), default="active", nullable=False, index=True) # active/stale/stale_confirmed/manual
    stale_since = Column(DateTime, nullable=True)                    # 外部源首次未发现时间
    sync_locked_fields = Column(Text, default="[]", nullable=False) # 手工保护、不被外部同步覆盖的字段
    additional_ips = relationship("VMIP", back_populates="vm", cascade="all, delete-orphan",
                                  order_by="VMIP.id", lazy="select")
    storage_assets = relationship(
        "IntegrationStorage", secondary="vm_storage_links", back_populates="vms", lazy="select"
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    host = relationship("ServerAsset")


class VMIP(Base):
    """虚拟机的额外 IP 地址（多网卡 / 多 IP 场景）。"""
    __tablename__ = "vm_ips"

    id = Column(Integer, primary_key=True, index=True)
    vm_id = Column(Integer, ForeignKey("vm_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    vm = relationship("VMInstance", back_populates="additional_ips")


class IntegrationStorage(Base):
    """Datastore/filesystem inventory persisted from an external platform."""
    __tablename__ = "integration_storage_assets"
    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_integration_storage_source_external"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(20), nullable=False, index=True)
    external_id = Column(String(255), nullable=False)
    source_endpoint = Column(String(500), default="", nullable=False)
    name = Column(String(300), nullable=False, index=True)
    storage_type = Column(String(100), default="")
    capacity = Column(String(100), default="")
    used_space = Column(String(100), default="")
    free_space = Column(String(100), default="")
    accessible = Column(Boolean, default=True, nullable=False, index=True)
    vm_count = Column(Integer, default=0)
    sync_state = Column(String(20), default="active", nullable=False, index=True)
    stale_since = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vms = relationship(
        "VMInstance", secondary="vm_storage_links", back_populates="storage_assets", lazy="select"
    )


class VMStorageLink(Base):
    """Many-to-many relationship between virtual machines and external storage."""
    __tablename__ = "vm_storage_links"

    vm_id = Column(Integer, ForeignKey("vm_instances.id", ondelete="CASCADE"), primary_key=True)
    storage_id = Column(
        Integer, ForeignKey("integration_storage_assets.id", ondelete="CASCADE"), primary_key=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationSyncRun(Base):
    """Durable history and diff snapshot for an external inventory synchronization."""
    __tablename__ = "integration_sync_runs"

    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(20), nullable=False, index=True)
    mode = Column(String(20), default="manual", nullable=False, index=True)
    status = Column(String(20), default="running", nullable=False, index=True)
    attempt = Column(Integer, default=1, nullable=False)
    max_attempts = Column(Integer, default=1, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    discovered_vms = Column(Integer, default=0)
    discovered_storage = Column(Integer, default=0)
    created_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)
    unchanged_count = Column(Integer, default=0)
    stale_count = Column(Integer, default=0)
    diff_summary = Column(JSON, default=dict)
    snapshot = Column(JSON, default=dict)
    error_message = Column(Text, default="")


class SystemSetting(Base):
    """通用键值配置表（目前用于存储 AD / LDAP 集成配置等）。"""
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, default="")                            # JSON 字符串
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Account(Base):
    """通用账号密码保险柜（含 AD 同步导入的账号）。

    与「凭据配置 (Credential Profiles)」的区别：CredentialProfile 专用于
    设备 SSH 登录（按 device_type 匹配）；Account 是面向运维人员的通用
    密码库（网络设备 / 服务器 / 数据库 / 应用系统 / AD 用户 等任意账号），
    密码加密落库，可点击「显示/复制」临时解密查看。
    """
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)      # 用途 / 备注名
    username = Column(String(200), default="")                  # 登录账号
    password_enc = Column(Text, default="")                     # 加密存储的密码
    category = Column(String(50), default="其他", index=True)    # 分类: 网络设备/服务器/数据库/应用系统/AD用户/其他
    source = Column(String(50), default="手动")                  # 来源: 手动 / AD同步
    address = Column(String(300), default="")                   # 管理地址 / IP / URL
    linked_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)  # 关联设备(可选)
    ad_dn = Column(String(500), default="")                      # AD 用户 DN（AD 同步导入时写入）
    ad_sam = Column(String(200), default="")                     # sAMAccountName
    email = Column(String(300), default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password: str):
        self.password_enc = encrypt_password(password)

    def get_password(self) -> str:
        return decrypt_password(self.password_enc)


class User(Base):
    """Web 系统管理账号（与「账号密码」保险库 Account 无关）。

    - is_superuser: 超级管理员，拥有全部模块权限（最大权限）。
    - modules: 非超级管理员时，允许访问的功能模块 key 列表（JSON 字符串）。
    - is_active: 禁用后无法登录，已有会话也会被中间件拦截。
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False, default="")
    is_superuser = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    modules = Column(Text, default="[]")   # JSON list of module keys
    session_version = Column(Integer, default=1, nullable=False)  # 安全变更后立即使旧会话失效
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_modules(self) -> list:
        if self.is_superuser:
            return ["*"]  # 全部
        try:
            return json.loads(self.modules or "[]")
        except Exception:
            return []


def migrate_legacy_credentials(db) -> int:
    """Re-encrypt credentials written with the legacy fixed XOR key.

    The migration is idempotent: AES-GCM values carry a ``v2:`` prefix and
    are skipped on subsequent startups.
    """
    changed = 0
    for model, fields in (
        (CredentialProfile, ("password_enc", "enable_password_enc")),
        (Device, ("password_enc", "enable_password_enc")),
        (Account, ("password_enc",)),
    ):
        for row in db.query(model).all():
            for field in fields:
                encrypted = getattr(row, field, "") or ""
                if not encrypted or encrypted.startswith("v2:"):
                    continue
                plaintext = _decrypt_legacy_password(encrypted)
                if plaintext:
                    setattr(row, field, encrypt_password(plaintext))
                    changed += 1

    for setting_key, secret_field in (
        ("ad_config", "bind_password"),
        ("web_auth", "password_enc"),
    ):
        setting = db.get(SystemSetting, setting_key)
        if not setting or not setting.value:
            continue
        try:
            payload = json.loads(setting.value)
        except Exception:
            continue
        encrypted = payload.get(secret_field) or ""
        if encrypted and not encrypted.startswith("v2:"):
            plaintext = _decrypt_legacy_password(encrypted)
            if plaintext:
                payload[secret_field] = encrypt_password(plaintext)
                setting.value = json.dumps(payload, ensure_ascii=False)
                changed += 1

    if changed:
        db.commit()
    return changed
