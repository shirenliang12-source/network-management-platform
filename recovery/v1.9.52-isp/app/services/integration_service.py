"""Read-only inventory discovery for Zabbix and VMware vCenter."""
from __future__ import annotations

import ipaddress
import functools
import http.client
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any


def _format_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    precision = 0 if size >= 10 else 1
    return f"{size:.{precision}f} {unit}"


def _unique_ips(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip().split("%")[0]
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _os_type(description: str) -> str:
    lowered = (description or "").lower()
    if "windows" in lowered:
        return "Windows"
    if any(name in lowered for name in (
        "linux", "ubuntu", "centos", "debian", "red hat", "rhel", "rocky", "alma",
        "suse", "oracle linux", "amazon linux", "openeuler", "euleros", "kylin", "anolis",
    )):
        return "Linux"
    return "其他"


class _SourceHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, source_ip: str):
        super().__init__()
        self.source_ip = source_ip

    def http_open(self, request):
        factory = functools.partial(
            http.client.HTTPConnection, source_address=(self.source_ip, 0),
        )
        return self.do_open(factory, request)


class _SourceHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, source_ip: str, context: ssl.SSLContext):
        super().__init__(context=context)
        self.source_ip = source_ip

    def https_open(self, request):
        factory = functools.partial(
            http.client.HTTPSConnection, source_address=(self.source_ip, 0),
        )
        return self.do_open(
            factory, request, context=self._context, check_hostname=self._check_hostname,
        )


def _zabbix_api_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    if not url:
        raise ValueError("未配置 Zabbix 地址")
    if not url.lower().endswith("api_jsonrpc.php"):
        url += "/api_jsonrpc.php"
    return url


class _ZabbixClient:
    def __init__(self, config: dict[str, Any]):
        self.url = _zabbix_api_url(config.get("url", ""))
        self.timeout = int(config.get("timeout") or 20)
        self.token = ""
        self._request_id = 0
        if config.get("verify_ssl", True):
            self.context = ssl.create_default_context()
        else:
            self.context = ssl._create_unverified_context()
        self.source_ip = str(config.get("source_ip") or "").strip()
        self.opener = None
        if self.source_ip:
            # Binding the client socket is required on multi-homed management
            # hosts where the Zabbix network is reachable through only one NIC.
            self.opener = urllib.request.build_opener(
                _SourceHTTPHandler(self.source_ip),
                _SourceHTTPSHandler(self.source_ip, self.context),
            )

    def call(self, method: str, params: Any, authenticated: bool = True):
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0", "method": method, "params": params, "id": self._request_id,
        }
        if authenticated and self.token:
            payload["auth"] = self.token
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json-rpc", "User-Agent": "CiscoNetMgr/1.9"},
            method="POST",
        )
        try:
            if self.opener:
                response_context = self.opener.open(request, timeout=self.timeout)
            else:
                response_context = urllib.request.urlopen(
                    request, timeout=self.timeout, context=self.context,
                )
            with response_context as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RuntimeError(f"Zabbix API 请求失败: {exc}") from exc
        if result.get("error"):
            error = result["error"]
            message = error.get("data") or error.get("message") or "未知错误"
            raise RuntimeError(f"Zabbix API 错误: {message}")
        return result.get("result")

    def login(self, username: str, password: str) -> str:
        try:
            token = self.call(
                "user.login", {"username": username, "password": password}, authenticated=False
            )
        except RuntimeError:
            # Zabbix 5.x/6.x used ``user`` while current versions use ``username``.
            token = self.call(
                "user.login", {"user": username, "password": password}, authenticated=False
            )
        self.token = str(token or "")
        if not self.token:
            raise RuntimeError("Zabbix 登录未返回认证令牌")
        return self.token

    def logout(self) -> None:
        if not self.token:
            return
        try:
            self.call("user.logout", [])
        except Exception:
            pass
        self.token = ""


def test_zabbix(config: dict[str, Any]) -> dict[str, Any]:
    client = _ZabbixClient(config)
    version = client.call("apiinfo.version", [], authenticated=False)
    client.login(config.get("username", ""), config.get("password", ""))
    try:
        hosts = client.call("host.get", {"output": ["hostid"], "limit": 1}) or []
    finally:
        client.logout()
    return {
        "ok": True,
        "message": f"Zabbix {version} 连接及认证成功",
        "version": str(version or ""),
        "sample_accessible": bool(hosts),
    }


def _zabbix_items(client: _ZabbixClient, host_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    by_host: dict[str, list[dict[str, Any]]] = {host_id: [] for host_id in host_ids}
    seen: set[tuple[str, str, str]] = set()
    if not host_ids:
        return by_host
    for key_fragment in (
        "system.cpu.num", "memory.size", "vfs.fs.size", "system.sw.os", "system.uname",
    ):
        try:
            rows = client.call(
                "item.get",
                {
                    "output": ["hostid", "name", "key_", "lastvalue", "units"],
                    "hostids": host_ids,
                    "search": {"key_": key_fragment},
                    "filter": {"status": "0"},
                    "limit": 10000,
                },
            ) or []
        except RuntimeError:
            rows = []
        for row in rows:
            host_id = str(row.get("hostid") or "")
            identity = str(row.get("itemid") or row.get("key_") or "")
            marker = (host_id, identity, str(row.get("lastvalue") or ""))
            if marker in seen:
                continue
            seen.add(marker)
            by_host.setdefault(host_id, []).append(row)
    return by_host


def _zabbix_os_description(inventory: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Resolve OS details from inventory first, then standard agent items.

    Zabbix host inventory is frequently disabled. Official Linux/Windows
    templates still collect system.sw.os[...] and system.uname, so those values
    provide a reliable fallback without requiring inventory auto-population.
    """
    for field in ("os_full", "os", "os_short"):
        value = str(inventory.get(field) or "").strip()
        if value:
            return value[:200]

    candidates: list[tuple[int, str]] = []
    for item in items:
        key = str(item.get("key_") or "").lower()
        raw = str(item.get("lastvalue") or "").strip()
        if not raw or key not in {"system.uname"} and not key.startswith("system.sw.os"):
            continue
        value = raw
        if raw[:1] in "[{":
            try:
                decoded = json.loads(raw)
                rows = decoded if isinstance(decoded, list) else [decoded]
                parts: list[str] = []
                for row in rows:
                    if isinstance(row, dict):
                        pretty = row.get("versionPretty") or row.get("pretty_name")
                        name = row.get("name") or row.get("productName")
                        version = row.get("version") or row.get("versionFull")
                        text = str(pretty or " ".join(str(x) for x in (name, version) if x) or "").strip()
                        if text and text not in parts:
                            parts.append(text)
                if parts:
                    value = "; ".join(parts)
            except (TypeError, ValueError):
                pass
        priority = 0 if key.startswith("system.sw.os") else 1
        candidates.append((priority, value[:200]))
    return min(candidates, default=(9, ""))[1]


def discover_zabbix(config: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    client = _ZabbixClient(config)
    version = client.call("apiinfo.version", [], authenticated=False)
    client.login(config.get("username", ""), config.get("password", ""))
    try:
        requested_limit = max(1, min(int(limit), 2000))
        hosts = client.call(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status"],
                "selectInterfaces": ["ip", "dns", "main", "type"],
                "selectInventory": "extend",
                "sortfield": "name",
                "limit": requested_limit + 1,
            },
        ) or []
        truncated = len(hosts) > requested_limit
        hosts = hosts[:requested_limit]
        item_map = _zabbix_items(client, [str(row.get("hostid")) for row in hosts])
    finally:
        client.logout()

    vms: list[dict[str, Any]] = []
    storage: list[dict[str, Any]] = []
    for host in hosts:
        host_id = str(host.get("hostid") or "")
        inventory = host.get("inventory") if isinstance(host.get("inventory"), dict) else {}
        interfaces = host.get("interfaces") if isinstance(host.get("interfaces"), list) else []
        ips = _unique_ips(
            [entry.get("ip") for entry in interfaces]
            + [inventory.get("oob_ip"), inventory.get("url_a"), inventory.get("url_b")]
        )
        items = item_map.get(host_id, [])
        cpu = ""
        memory = ""
        disks: list[dict[str, str]] = []
        filesystems: dict[str, dict[str, float]] = {}
        for item in items:
            key = str(item.get("key_") or "")
            value = item.get("lastvalue")
            if "cpu.num" in key and not cpu:
                try:
                    cpu = f"{int(float(value))} vCPU"
                except (TypeError, ValueError):
                    pass
            elif "memory.size" in key and "total" in key and not memory:
                memory = _format_bytes(value)
            elif key.startswith("vfs.fs.size[") and key.endswith("]"):
                inner = key[len("vfs.fs.size["):-1]
                if "," not in inner:
                    continue
                mount, mode = inner.rsplit(",", 1)
                mount = mount.strip().strip('"') or "/"
                mode = mode.strip().lower()
                if mode not in {"total", "free", "used"}:
                    continue
                try:
                    filesystems.setdefault(mount, {})[mode] = float(value or 0)
                except (TypeError, ValueError):
                    continue
                if mode == "total" and float(value or 0) > 0:
                    disks.append({"name": mount[:100], "size": _format_bytes(value)})
        disks = [disk for disk in disks if disk["size"]]
        total_disk = 0.0
        for item in items:
            key = str(item.get("key_") or "")
            if "vfs.fs.size" in key and "total" in key:
                try:
                    total_disk += float(item.get("lastvalue") or 0)
                except (TypeError, ValueError):
                    pass
        host_name = str(host.get("name") or host.get("host") or host_id)
        for mount, values in filesystems.items():
            total = values.get("total", 0)
            free = values.get("free", 0)
            used = values.get("used", max(0, total - free))
            if total <= 0:
                continue
            storage.append({
                "external_id": f"{host_id}:{mount}"[:255],
                "name": f"{host_name} · {mount}"[:300],
                "type": "文件系统",
                "capacity": _format_bytes(total),
                "free_space": _format_bytes(free),
                "used_space": _format_bytes(used),
                "accessible": str(host.get("status")) == "0",
                "vm_count": 1,
            })
        os_description = _zabbix_os_description(inventory, items)
        vms.append(
            {
                "external_id": host_id,
                "name": host_name,
                "function": str(inventory.get("type_full") or inventory.get("type") or ""),
                "os_type": _os_type(os_description),
                "os_version": os_description,
                "status": "运行中" if str(host.get("status")) == "0" else "其他",
                "host_name": str(inventory.get("host_networks") or ""),
                "cpu": cpu,
                "memory": memory,
                "disk_size": _format_bytes(total_disk),
                "disk_count": len(disks) or 1,
                "disks": disks,
                "storage_lun": ", ".join(disk["name"] for disk in disks)[:500],
                "management_ip": ips[0] if ips else "",
                "additional_ips": ips[1:],
                "storage_external_ids": [f"{host_id}:{mount}"[:255] for mount in filesystems],
            }
        )
    return {
        "source": "zabbix",
        "truncated": truncated,
        "version": str(version or ""),
        "fetched_at": datetime.utcnow().isoformat(),
        "summary": {"vms": len(vms), "storage": len(storage)},
        "vms": vms,
        "storage": storage,
    }


def _vcenter_connect(config: dict[str, Any]):
    try:
        from pyVim.connect import SmartConnect
    except ImportError as exc:
        raise RuntimeError("未安装 pyVmomi，无法连接 vCenter") from exc
    context = ssl.create_default_context()
    if not config.get("verify_ssl", True):
        context = ssl._create_unverified_context()
    return SmartConnect(
        host=str(config.get("host") or "").strip(),
        port=int(config.get("port") or 443),
        user=str(config.get("username") or "").strip(),
        pwd=config.get("password") or "",
        sslContext=context,
        httpConnectionTimeout=int(config.get("timeout") or 30),
        connectionPoolTimeout=int(config.get("timeout") or 30),
    )


def test_vcenter(config: dict[str, Any]) -> dict[str, Any]:
    from pyVim.connect import Disconnect

    service_instance = _vcenter_connect(config)
    try:
        about = service_instance.RetrieveContent().about
        return {
            "ok": True,
            "message": f"vCenter {about.version} 连接及认证成功",
            "version": str(about.version or ""),
            "api_version": str(about.apiVersion or ""),
            "instance_uuid": str(about.instanceUuid or ""),
        }
    finally:
        Disconnect(service_instance)


def discover_vcenter(config: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    from pyVim.connect import Disconnect
    from pyVmomi import vim

    service_instance = _vcenter_connect(config)
    vm_view = datastore_view = None
    try:
        content = service_instance.RetrieveContent()
        about = content.about
        manager = content.viewManager
        vm_view = manager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        datastore_view = manager.CreateContainerView(content.rootFolder, [vim.Datastore], True)
        requested_limit = max(1, min(int(limit), 2000))
        all_vms = list(vm_view.view)
        truncated = len(all_vms) > requested_limit
        vms: list[dict[str, Any]] = []
        for vm in all_vms[:requested_limit]:
            try:
                summary = vm.summary
                config_info = vm.config
                guest = vm.guest
                runtime = vm.runtime
                devices = list(getattr(getattr(config_info, "hardware", None), "device", []) or [])
                disks: list[dict[str, str]] = []
                datastore_names: list[str] = []
                datastore_ids: list[str] = []
                total_bytes = 0
                for device in devices:
                    if not isinstance(device, vim.vm.device.VirtualDisk):
                        continue
                    capacity = int(
                        getattr(device, "capacityInBytes", 0)
                        or int(getattr(device, "capacityInKB", 0) or 0) * 1024
                    )
                    total_bytes += capacity
                    label = str(getattr(getattr(device, "deviceInfo", None), "label", "磁盘"))
                    datastore = getattr(getattr(device, "backing", None), "datastore", None)
                    datastore_name = str(getattr(datastore, "name", "") or "")
                    if datastore_name and datastore_name not in datastore_names:
                        datastore_names.append(datastore_name)
                    datastore_id = str(getattr(datastore, "_moId", "") or "")
                    if datastore_id and datastore_id not in datastore_ids:
                        datastore_ids.append(datastore_id)
                    disks.append({
                        "name": f"{label}{' / ' + datastore_name if datastore_name else ''}",
                        "size": _format_bytes(capacity),
                    })
                ip_values: list[Any] = [getattr(guest, "ipAddress", "")]
                for network in list(getattr(guest, "net", []) or []):
                    ip_values.extend(list(getattr(network, "ipAddress", []) or []))
                ips = _unique_ips(ip_values)
                guest_name = str(
                    getattr(guest, "guestFullName", "")
                    or getattr(getattr(summary, "config", None), "guestFullName", "")
                    or ""
                )
                is_template = bool(getattr(config_info, "template", False))
                power_state = str(getattr(runtime, "powerState", ""))
                if is_template:
                    status = "模板"
                elif power_state == "poweredOn":
                    status = "运行中"
                elif power_state == "poweredOff":
                    status = "已关机"
                elif power_state == "suspended":
                    status = "挂起"
                else:
                    status = "其他"
                host = getattr(runtime, "host", None)
                num_cpu = int(getattr(getattr(summary, "config", None), "numCpu", 0) or 0)
                memory_mb = int(getattr(getattr(summary, "config", None), "memorySizeMB", 0) or 0)
                vms.append(
                    {
                        "external_id": str(getattr(vm, "_moId", "")),
                        "name": str(getattr(getattr(summary, "config", None), "name", "") or vm.name),
                        "function": "",
                        "os_type": _os_type(guest_name),
                        "os_version": guest_name,
                        "status": status,
                        "host_name": str(getattr(host, "name", "") or ""),
                        "cpu": f"{num_cpu} vCPU" if num_cpu else "",
                        "memory": _format_bytes(memory_mb * 1024 * 1024),
                        "disk_size": _format_bytes(total_bytes),
                        "disk_count": len(disks) or 1,
                        "disks": disks,
                        "storage_lun": ", ".join(datastore_names)[:500],
                        "management_ip": ips[0] if ips else "",
                        "additional_ips": ips[1:],
                        "storage_external_ids": datastore_ids,
                    }
                )
            except Exception as exc:
                vms.append({
                    "external_id": str(getattr(vm, "_moId", "")),
                    "name": str(getattr(vm, "name", "无法读取的虚拟机")),
                    "error": str(exc),
                })

        storage: list[dict[str, Any]] = []
        for datastore in list(datastore_view.view):
            try:
                summary = datastore.summary
                capacity = int(getattr(summary, "capacity", 0) or 0)
                free_space = int(getattr(summary, "freeSpace", 0) or 0)
                storage.append(
                    {
                        "external_id": str(getattr(datastore, "_moId", "")),
                        "name": str(getattr(summary, "name", "") or datastore.name),
                        "type": str(getattr(summary, "type", "") or ""),
                        "capacity": _format_bytes(capacity),
                        "free_space": _format_bytes(free_space),
                        "used_space": _format_bytes(max(0, capacity - free_space)),
                        "accessible": bool(getattr(summary, "accessible", False)),
                        "vm_count": len(list(getattr(datastore, "vm", []) or [])),
                    }
                )
            except Exception:
                continue
        return {
            "source": "vcenter",
            "truncated": truncated,
            "version": str(about.version or ""),
            "fetched_at": datetime.utcnow().isoformat(),
            "summary": {"vms": len(vms), "storage": len(storage)},
            "vms": vms,
            "storage": storage,
        }
    finally:
        if vm_view is not None:
            vm_view.Destroy()
        if datastore_view is not None:
            datastore_view.Destroy()
        Disconnect(service_instance)
