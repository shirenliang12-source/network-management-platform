"""Pydantic schemas for API request/response."""
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    """Default policy for API request bodies."""
    model_config = ConfigDict(
        extra="forbid",
        str_max_length=4000,
        hide_input_in_errors=True,
    )


# ---- Device Group ----
class DeviceGroupCreate(RequestModel):
    name: str
    description: str = ""

class DeviceGroupResponse(BaseModel):
    id: int
    name: str
    description: str
    device_count: int = 0
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- Credential Profile ----
class CredentialProfileCreate(RequestModel):
    name: str
    device_type: str = "cisco_ios"
    username: str = "admin"
    password: str = ""
    enable_password: str = ""
    port: int = 22
    source_ip: str = ""
    description: str = ""

class CredentialProfileUpdate(RequestModel):
    name: Optional[str] = None
    device_type: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enable_password: Optional[str] = None
    port: Optional[int] = None
    source_ip: Optional[str] = None
    description: Optional[str] = None

class CredentialProfileResponse(BaseModel):
    id: int
    name: str
    device_type: str
    username: str
    port: int
    source_ip: str = ""
    description: str = ""
    has_password: bool = False
    has_enable_password: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- Device ----
class DeviceCreate(RequestModel):
    name: str
    ip_address: str
    device_type: str = "cisco_ios"
    group_id: Optional[int] = None
    company: str = ""
    model: str = ""
    function: str = ""
    credential_profile_id: Optional[int] = None
    username: str = "admin"
    password: str = ""
    enable_password: str = ""
    port: int = 22
    source_ip: str = ""
    is_active: bool = True
    additional_ips: List[str] = []   # 多网卡/多IP：额外的 IP 地址列表
    production_date_manual: str = ""  # 手动维护的出厂日期（YYYY-MM-DD 或 YYYY-MM）

class DeviceUpdate(RequestModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    device_type: Optional[str] = None
    group_id: Optional[int] = None
    company: Optional[str] = None
    model: Optional[str] = None
    function: Optional[str] = None
    credential_profile_id: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enable_password: Optional[str] = None
    port: Optional[int] = None
    source_ip: Optional[str] = None
    is_active: Optional[bool] = None
    additional_ips: Optional[List[str]] = None   # 提供时整体替换额外 IP
    production_date_manual: Optional[str] = None  # 手动维护的出厂日期

class DeviceResponse(BaseModel):
    id: int
    name: str
    ip_address: str
    device_type: str
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    company: str = ""
    model: str = ""
    function: str = ""
    credential_profile_id: Optional[int] = None
    username: str
    port: int
    source_ip: str = ""
    is_active: bool
    status: str
    additional_ips: List[dict] = []   # [{ip_address, interface_name, is_primary, notes}]
    last_seen: Optional[datetime] = None
    last_backup: Optional[datetime] = None
    last_discovery: Optional[datetime] = None
    last_info_collection: Optional[datetime] = None
    production_date_manual: str = ""  # 手动维护的出厂日期
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class DeviceBatchImport(RequestModel):
    devices: List[DeviceCreate]

class DeviceBatchOperation(RequestModel):
    device_ids: List[int]


# ---- Config Backup ----
class ConfigBackupResponse(BaseModel):
    id: int
    device_id: int
    config_hash: str
    backup_time: datetime
    is_changed: bool
    change_summary: str = ""
    backup_size: int = 0
    review_status: str = "pending"
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: Optional[datetime] = None
    is_baseline: bool = False
    model_config = ConfigDict(from_attributes=True)

class ConfigBackupDetail(BaseModel):
    id: int
    device_id: int
    device_name: str
    config_text: str
    config_hash: str
    backup_time: datetime
    is_changed: bool
    change_summary: str = ""
    backup_size: int = 0
    review_status: str = "pending"
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: Optional[datetime] = None
    is_baseline: bool = False
    model_config = ConfigDict(from_attributes=True)


# ---- Device Info ----
class DeviceInfoResponse(BaseModel):
    id: int
    device_id: int
    hostname: str = ""
    vendor: str = "Cisco"
    model: str = ""
    os_type: str = ""
    os_version: str = ""
    serial_number: str = ""
    production_date: str = ""
    # v1.9.35+: production-date provenance for "(自动)" / "(手动)" labels
    production_date_source: str = ""     # "serial" | "auto" | "manual"
    production_date_pattern: str = ""
    production_date_raw_match: str = ""
    uptime: str = ""
    uptime_seconds: int = 0
    cpu_usage: str = ""
    memory_usage: str = ""
    interface_up_count: int = 0
    interface_down_count: int = 0
    management_ip: str = ""
    mac_address: str = ""
    location: str = ""
    contact: str = ""
    interfaces: List[Any] = []
    collected_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- Neighbor ----
class NeighborResponse(BaseModel):
    id: int
    device_id: int
    protocol: str
    local_interface: str = ""
    neighbor_name: str = ""
    neighbor_ip: str = ""
    neighbor_interface: str = ""
    neighbor_platform: str = ""
    neighbor_capability: str = ""
    neighbor_device_id: Optional[int] = None
    discovered_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- Topology ----
class TopologyNode(BaseModel):
    id: str
    label: str
    ip: str = ""
    device_type: str = ""
    model: str = ""
    status: str = "unknown"
    group: str = ""
    is_managed: bool = True
    serial_number: str = ""
    os_version: str = ""

class TopologyEdge(BaseModel):
    source: str
    target: str
    label: str = ""
    source_interface: str = ""
    target_interface: str = ""
    protocol: str = "cdp"

class TopologyData(BaseModel):
    nodes: List[TopologyNode]
    edges: List[TopologyEdge]
    total_nodes: int
    total_edges: int


# ---- Task Log ----
class TaskLogResponse(BaseModel):
    id: int
    task_type: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_devices: int = 0
    success_count: int = 0
    failed_count: int = 0
    error_details: List[Any] = []
    summary: str = ""
    model_config = ConfigDict(from_attributes=True)


# ---- Schedule ----
class ScheduleConfigCreate(RequestModel):
    task_type: str
    cron_expression: str
    is_enabled: bool = True
    description: str = ""

class ScheduleConfigResponse(BaseModel):
    id: int
    task_type: str
    cron_expression: str
    is_enabled: bool
    description: str = ""
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    is_running: bool = False
    last_status: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# ---- IP Inventory ----
class IPIInventoryCreate(RequestModel):
    ip_segment: str = ""
    subnet: str = ""
    mask: str = ""
    vlan: str = ""
    usage: str = ""
    company: str = ""
    remarks: str = ""
    sort_order: Optional[int] = None
    device_id: Optional[int] = None
    asset_sn: str = ""
    network_type: str = "有线"
    firewall: str = ""
    zone_interface_name: str = ""

class IPIInventoryUpdate(RequestModel):
    ip_segment: Optional[str] = None
    subnet: Optional[str] = None
    mask: Optional[str] = None
    vlan: Optional[str] = None
    usage: Optional[str] = None
    company: Optional[str] = None
    remarks: Optional[str] = None
    sort_order: Optional[int] = None
    device_id: Optional[int] = None
    asset_sn: Optional[str] = None
    network_type: Optional[str] = None
    firewall: Optional[str] = None
    zone_interface_name: Optional[str] = None

class IPIInventoryResponse(BaseModel):
    id: int
    ip_segment: str = ""
    subnet: str = ""
    mask: str = ""
    vlan: str = ""
    usage: str = ""
    company: str = ""
    remarks: str = ""
    sort_order: int = 0
    device_id: Optional[int] = None
    asset_sn: str = ""
    network_type: str = "有线"
    firewall: str = ""
    zone_interface_name: str = ""
    device_name: str = ""                # 关联/反查到的设备名称
    device_ips: List[dict] = []          # 关联设备的全部IP（主IP + 额外IP）
    reverse_linked: bool = False         # 是否由 IP 反查得到设备关联
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- IP 地址规划 (IPAM) ----
class IPAMAggregateCreate(RequestModel):
    name: str
    prefix: str
    description: str = ""
    date_added: str = ""

class IPAMAggregateUpdate(RequestModel):
    name: Optional[str] = None
    prefix: Optional[str] = None
    description: Optional[str] = None
    date_added: Optional[str] = None

class IPAMAggregateResponse(BaseModel):
    id: int
    name: str
    prefix: str
    description: str = ""
    date_added: str = ""
    prefix_count: int = 0
    ip_count: int = 0
    utilization: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class IPAMPrefixCreate(RequestModel):
    aggregate_id: Optional[int] = None
    parent_id: Optional[int] = None
    prefix: str
    status: str = "规划"
    role: str = ""
    vlan: str = ""
    company: str = ""
    firewall: str = ""
    zone_interface_name: str = ""
    description: str = ""
    is_pool: bool = False

class IPAMPrefixUpdate(RequestModel):
    aggregate_id: Optional[int] = None
    parent_id: Optional[int] = None
    prefix: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None
    vlan: Optional[str] = None
    company: Optional[str] = None
    firewall: Optional[str] = None
    zone_interface_name: Optional[str] = None
    description: Optional[str] = None
    is_pool: Optional[bool] = None

class IPAMPrefixResponse(BaseModel):
    id: int
    aggregate_id: Optional[int] = None
    parent_id: Optional[int] = None
    prefix: str
    status: str = "规划"
    role: str = ""
    vlan: str = ""
    company: str = ""
    firewall: str = ""
    zone_interface_name: str = ""
    description: str = ""
    is_pool: bool = False
    total_ips: int = 0
    usable_ips: int = 0
    allocated_ips: int = 0
    in_use_ips: int = 0
    utilization: float = 0.0
    child_count: int = 0
    # 静态 / DHCP 分配统计
    static_used: int = 0          # 已使用的静态地址（已绑定设备）
    static_unused: int = 0        # 未使用的静态地址
    dhcp_count: int = 0           # DHCP 地址数量
    static_device_list: List[dict] = []  # 已使用静态地址的设备清单 [{device_id,device_name,device_model,device_ip,address,description}]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class IPAMIPAddressCreate(RequestModel):
    prefix_id: int
    address: str
    status: str = "规划"
    allocation_type: str = "静态"   # 静态 / DHCP
    dns_name: str = ""
    description: str = ""
    assigned_device_id: Optional[int] = None
    device_name: str = ""           # 手工录入：设备名称
    device_model: str = ""          # 手工录入：设备型号
    device_ip: str = ""             # 手工录入：设备 IP

class IPAMIPAddressUpdate(RequestModel):
    prefix_id: Optional[int] = None
    address: Optional[str] = None
    status: Optional[str] = None
    allocation_type: Optional[str] = None
    dns_name: Optional[str] = None
    description: Optional[str] = None
    assigned_device_id: Optional[int] = None
    device_name: Optional[str] = None    # 手工录入：设备名称
    device_model: Optional[str] = None   # 手工录入：设备型号
    device_ip: Optional[str] = None      # 手工录入：设备 IP

class IPAMIPAddressResponse(BaseModel):
    id: int
    prefix_id: int
    address: str
    status: str = "规划"
    allocation_type: str = "静态"
    dns_name: str = ""
    description: str = ""
    assigned_device_id: Optional[int] = None
    assigned_device_name: Optional[str] = None
    device_name: Optional[str] = None    # 手工录入：设备名称
    device_model: Optional[str] = None   # 手工录入：设备型号
    device_ip: Optional[str] = None      # 手工录入：设备 IP
    primary_ip: str = ""                 # 关联设备的管理IP（主IP）
    device_ips: List[dict] = []          # 关联设备的全部IP（主IP + 额外IP）
    reverse_linked: bool = False         # 是否由 IP 反查得到设备关联
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- Dashboard ----
class DashboardStats(BaseModel):
    total_devices: int = 0
    online_devices: int = 0
    offline_devices: int = 0
    unknown_devices: int = 0
    total_backups: int = 0
    backups_today: int = 0
    last_backup_time: Optional[datetime] = None
    total_neighbors: int = 0
    last_discovery_time: Optional[datetime] = None
    groups: List[dict] = []
    recent_tasks: List[dict] = []
    devices_never_backed_up: int = 0
    devices_with_changes: int = 0


# ---- Serial Decode ----
class SerialDecodeResult(BaseModel):
    serial_number: str
    format: str = ""
    location_code: str = ""
    location_name: str = ""
    production_date: str = ""
    is_estimated: bool = True
    notes: str = ""


# ---- 数据中心 (Data Center) ----
class DCSiteCreate(RequestModel):
    name: str
    company: str = ""
    region: str = ""
    address: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    description: str = ""


class DCSiteUpdate(RequestModel):
    name: Optional[str] = None
    company: Optional[str] = None
    region: Optional[str] = None
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None


class DCSiteResponse(BaseModel):
    id: int
    name: str
    company: str = ""
    region: str = ""
    address: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    description: str = ""
    rack_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class DCRackCreate(RequestModel):
    site_id: int
    rack_number: str
    name: str = ""
    role: str = ""
    type: str = "机柜"
    width: str = "19英寸"
    u_height: int = 42
    status: str = "在用"
    serial: str = ""
    asset_tag: str = ""
    location_detail: str = ""
    description: str = ""


class DCRackUpdate(RequestModel):
    site_id: Optional[int] = None
    rack_number: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    type: Optional[str] = None
    width: Optional[str] = None
    u_height: Optional[int] = None
    status: Optional[str] = None
    serial: Optional[str] = None
    asset_tag: Optional[str] = None
    location_detail: Optional[str] = None
    description: Optional[str] = None


class DCRackResponse(BaseModel):
    id: int
    site_id: int
    site_name: str = ""
    rack_number: str
    name: str = ""
    role: str = ""
    type: str = "机柜"
    width: str = "19英寸"
    u_height: int = 42
    status: str = "在用"
    serial: str = ""
    asset_tag: str = ""
    location_detail: str = ""
    description: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class DCSummary(BaseModel):
    sites_count: int = 0
    racks_count: int = 0
    racks_by_status: dict = {}
    racks_by_role: dict = {}
    model_config = ConfigDict(from_attributes=True)


# ---- 资产模块（其他 IT 资产 + 服务器/存储） ----
class ITAssetCreate(RequestModel):
    name: str
    asset_type: str = "其他"
    brand: str = ""
    model: str = ""
    serial: str = ""
    asset_tag: str = ""
    management_ip: str = ""
    linked_device_id: Optional[int] = None
    site_id: Optional[int] = None
    location_detail: str = ""
    rack_id: Optional[int] = None
    rack_position: str = ""
    status: str = "在用"
    notes: str = ""


class ITAssetUpdate(RequestModel):
    name: Optional[str] = None
    asset_type: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    asset_tag: Optional[str] = None
    management_ip: Optional[str] = None
    linked_device_id: Optional[int] = None
    site_id: Optional[int] = None
    location_detail: Optional[str] = None
    rack_id: Optional[int] = None
    rack_position: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class ITAssetResponse(BaseModel):
    id: int
    name: str
    asset_type: str = "其他"
    brand: str = ""
    model: str = ""
    serial: str = ""
    asset_tag: str = ""
    management_ip: str = ""
    linked_device_id: Optional[int] = None
    linked_device_name: Optional[str] = None
    site_id: Optional[int] = None
    site_name: Optional[str] = None
    location_detail: str = ""
    rack_id: Optional[int] = None
    rack_number: Optional[str] = None
    rack_position: str = ""
    status: str = "在用"
    notes: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class ServerAssetCreate(RequestModel):
    name: str
    category: str = "服务器"
    brand: str = ""
    model: str = ""
    serial: str = ""
    asset_tag: str = ""
    rack_id: int
    u_start: int = 1
    u_size: int = 1
    status: str = "在用"
    management_ip: str = ""
    additional_ips: List[str] = []   # 多网卡/多IP：额外的 IP 地址列表
    os: str = ""
    cpu: str = ""
    memory: str = ""
    storage_desc: str = ""
    owner: str = ""
    notes: str = ""


class ServerAssetUpdate(RequestModel):
    name: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    asset_tag: Optional[str] = None
    rack_id: Optional[int] = None
    u_start: Optional[int] = None
    u_size: Optional[int] = None
    status: Optional[str] = None
    management_ip: Optional[str] = None
    additional_ips: Optional[List[str]] = None   # 提供时整体替换额外 IP
    os: Optional[str] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    storage_desc: Optional[str] = None
    owner: Optional[str] = None
    notes: Optional[str] = None


class ServerAssetResponse(BaseModel):
    id: int
    name: str
    category: str = "服务器"
    brand: str = ""
    model: str = ""
    serial: str = ""
    asset_tag: str = ""
    rack_id: int
    rack_number: Optional[str] = None
    site_name: Optional[str] = None
    u_start: int = 1
    u_size: int = 1
    status: str = "在用"
    management_ip: str = ""
    additional_ips: List[dict] = []   # [{ip_address, is_primary, notes}]
    os: str = ""
    cpu: str = ""
    memory: str = ""
    storage_desc: str = ""
    owner: str = ""
    notes: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class AssetSummary(BaseModel):
    it_count: int = 0
    server_count: int = 0
    it_by_type: dict = {}
    server_by_category: dict = {}
    model_config = ConfigDict(from_attributes=True)


# ---- 服务器/存储「类别」可维护列表 ----
class ServerCategorySchema(BaseModel):
    id: int
    name: str
    color: str = ""
    model_config = ConfigDict(from_attributes=True)


class ServerCategoryCreate(RequestModel):
    name: str
    color: str = ""


class ServerCategoryUpdate(RequestModel):
    name: Optional[str] = None
    color: Optional[str] = None


# ---- 虚拟机清单 (VM Inventory) ----
class VMInstanceCreate(RequestModel):
    name: str
    function: str = ""
    os_type: str = "Linux"        # Windows / Linux / 其他
    os_version: str = ""
    status: str = "运行中"         # 运行中 / 已关机 / 挂起 / 模板 / 其他
    host_id: Optional[int] = None  # 关联宿主机（服务器存储 ServerAsset）
    host_name: str = ""            # 宿主机名称（关联带出或手填）
    cpu: str = ""                  # CPU（如 4 vCPU）
    memory: str = ""               # 内存（如 8 GB）
    disk_size: str = ""            # 磁盘大小（如 200 GB）
    disk_count: int = 1            # 磁盘数量（块数）
    storage_lun: str = ""          # 存储 LUN 信息（如 LUN0:200GB,LUN1:500GB）
    disks: List[dict] = []         # 磁盘明细：[{"name":"系统盘","size":"100 GB"}]
    management_ip: str = ""        # 主管理 IP
    additional_ips: List[str] = []  # 额外 IP 列表（多网卡/多IP）
    notes: str = ""


class VMInstanceUpdate(RequestModel):
    name: Optional[str] = None
    function: Optional[str] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    status: Optional[str] = None
    host_id: Optional[int] = None
    host_name: Optional[str] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    disk_size: Optional[str] = None
    disk_count: Optional[int] = None
    storage_lun: Optional[str] = None
    disks: Optional[List[dict]] = None            # 提供时整体替换磁盘明细
    management_ip: Optional[str] = None
    additional_ips: Optional[List[str]] = None   # 提供时整体替换额外 IP
    notes: Optional[str] = None
    sync_locked_fields: Optional[List[str]] = None


class VMInstanceResponse(BaseModel):
    id: int
    name: str
    function: str = ""
    os_type: str = "Linux"
    os_version: str = ""
    status: str = "运行中"
    host_id: Optional[int] = None
    host_name: str = ""
    cpu: str = ""
    memory: str = ""
    disk_size: str = ""
    disk_count: int = 1
    storage_lun: str = ""
    disks: List[dict] = []         # [{"name":"系统盘","size":"100 GB"}]
    management_ip: str = ""
    additional_ips: List[dict] = []   # [{ip_address, notes}]
    notes: str = ""
    source_type: str = "manual"
    external_id: Optional[str] = None
    source_endpoint: str = ""
    last_synced_at: Optional[datetime] = None
    sync_state: str = "active"
    stale_since: Optional[datetime] = None
    sync_locked_fields: List[str] = []
    storage_assets: List[dict] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class VMSummary(BaseModel):
    total: int = 0
    by_os_type: dict = {}
    by_status: dict = {}
    model_config = ConfigDict(from_attributes=True)


# ---- 账号密码管理 (Account Vault) ----
class AccountCreate(RequestModel):
    name: str
    username: str = ""
    password: str = ""
    category: str = "其他"
    source: str = "手动"
    address: str = ""
    linked_device_id: Optional[int] = None
    email: str = ""
    notes: str = ""


class AccountUpdate(RequestModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    address: Optional[str] = None
    linked_device_id: Optional[int] = None
    email: Optional[str] = None
    notes: Optional[str] = None


class AccountResponse(BaseModel):
    id: int
    name: str
    username: str = ""
    category: str = "其他"
    source: str = "手动"
    has_password: bool = False
    ad_managed: bool = False
    address: str = ""
    linked_device_id: Optional[int] = None
    linked_device_name: Optional[str] = None
    email: str = ""
    ad_dn: str = ""
    ad_sam: str = ""
    notes: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ---- AD / LDAP 集成配置 ----
class ADConfig(RequestModel):
    host: str = ""
    port: int = 389
    use_ssl: bool = False
    bind_dn: str = ""
    bind_password: str = ""          # 仅用于提交；返回配置时始终为空（不回显）
    base_dn: str = ""
    user_filter: str = "(objectClass=user)"
    sam_attr: str = "sAMAccountName"
    name_attr: str = "displayName"
    mail_attr: str = "mail"

    model_config = ConfigDict(from_attributes=True)


class ADVerifyRequest(RequestModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(max_length=4096)
