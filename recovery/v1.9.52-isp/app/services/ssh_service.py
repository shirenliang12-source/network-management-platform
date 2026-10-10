"""
SSH service for Cisco device communication using Netmiko.

Provides methods for:
- Connecting to devices via SSH
- Running show commands
- Retrieving running configuration
- Parsing CDP/LLDP neighbor output
- Collecting device info (version, inventory, interfaces)
"""
import re
import time
import socket
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

from app.models import Device
from app.config import settings
from app.services.command_config import get_command, get_command_delay, get_command_with_fallbacks

logger = logging.getLogger(__name__)

# Cisco device type mapping
DEVICE_TYPE_MAP = {
    "cisco_ios": "cisco_ios",
    "cisco_ios_xe": "cisco_xe",
    "cisco_nxos": "cisco_nxos",
    "cisco_wlc": "cisco_wlc_ssh",
    "cisco_ap": "cisco_ios",
}


class SSHService:
    """Handles SSH connections and command execution on Cisco devices."""

    def __init__(self, device: Device):
        self.device = device
        self.connection: Optional[ConnectHandler] = None
        self.device_type = device.device_type

    def connect(self) -> bool:
        """Establish SSH connection to the device.
        
        If device has source_ip set, binds the SSH socket to that source IP
        so traffic goes out through the selected network interface.
        """
        self._last_error = ""
        try:
            from app.services.command_config import resolve_device_driver
            self.device_type = resolve_device_driver(self.device.device_type)
            params = {
                "device_type": self.device_type,
                "host": self.device.ip_address,
                "username": self.device.username,
                "password": self.device.get_password(),
                "port": self.device.port,
                "timeout": settings.SSH_TIMEOUT,
                "global_delay_factor": settings.SSH_GLOBAL_DELAY,
            }

            enable_pwd = self.device.get_enable_password()
            if enable_pwd:
                params["secret"] = enable_pwd

            # Source IP binding: if device has source_ip set, create a bound socket
            source_ip = getattr(self.device, "source_ip", None)
            if source_ip and source_ip.strip() and source_ip.strip() not in ("0.0.0.0", "auto"):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind((source_ip.strip(), 0))
                    sock.settimeout(settings.SSH_TIMEOUT)
                    sock.connect((self.device.ip_address, self.device.port))
                    # CRITICAL: clear socket timeout so Paramiko can control
                    # SSH negotiation timeouts (otherwise handshake fails)
                    sock.settimeout(None)
                    params["sock"] = sock
                    logger.info(f"SSH bound to source IP {source_ip} for {self.device.ip_address}")
                except socket.timeout:
                    msg = f"Source IP {source_ip}: TCP connect timeout to {self.device.ip_address}:{self.device.port}"
                    logger.error(msg)
                    self._last_error = msg
                    self.device.status = "offline"
                    return False
                except OSError as bind_err:
                    msg = f"Source IP {source_ip} bind failed: {bind_err}"
                    logger.error(msg)
                    self._last_error = msg
                    self.device.status = "offline"
                    return False

            self.connection = ConnectHandler(**params)
            return True
        except NetmikoTimeoutException as e:
            msg = f"SSH timeout to {self.device.ip_address}: {e}"
            logger.error(msg)
            self._last_error = msg
            self.device.status = "offline"
            return False
        except NetmikoAuthenticationException as e:
            msg = f"SSH auth failed for {self.device.ip_address}: {e}"
            logger.error(msg)
            self._last_error = msg
            self.device.status = "offline"
            return False
        except Exception as e:
            msg = f"SSH connection error to {self.device.ip_address}: {e}"
            logger.error(msg)
            self._last_error = msg
            self.device.status = "offline"
            return False

    @property
    def last_error(self) -> str:
        """Return the last connection error message (empty if none)."""
        return getattr(self, "_last_error", "")

    def disconnect(self):
        """Close the SSH connection."""
        if self.connection:
            try:
                self.connection.disconnect()
            except Exception:
                pass
            finally:
                self.connection = None

    def send_command(self, command: str, delay_factor: float = 1.0) -> str:
        """Send a command and return the output."""
        if not command or not command.strip():
            return ''
        if not self.connection:
            if not self.connect():
                return ""
        try:
            output = self.connection.send_command(
                command,
                delay_factor=delay_factor,
                read_timeout=settings.SSH_TIMEOUT + 10,
            )
            return output
        except Exception as e:
            logger.error(f"Command error on {self.device.ip_address}: {command} -> {e}")
            return ""

    def send_config_set(self, commands: list) -> str:
        """Send configuration commands."""
        if not self.connection:
            if not self.connect():
                return ""
        try:
            output = self.connection.send_config_set(commands)
            return output
        except Exception as e:
            logger.error(f"Config error on {self.device.ip_address}: {e}")
            return ""

    def get_running_config(self) -> str:
        """Retrieve the running configuration (configurable command)."""
        cmd = get_command(self.device.device_type, "running_config")
        delay = get_command_delay(self.device.device_type, "running_config")
        return self.send_command(cmd, delay_factor=delay)

    def get_cdp_neighbors(self) -> str:
        """Get CDP neighbor details (configurable command)."""
        cmd = get_command(self.device.device_type, "cdp_neighbors")
        delay = get_command_delay(self.device.device_type, "cdp_neighbors")
        return self.send_command(cmd, delay_factor=delay)

    def get_lldp_neighbors(self) -> str:
        """Get LLDP neighbor details (configurable command)."""
        cmd = get_command(self.device.device_type, "lldp_neighbors")
        delay = get_command_delay(self.device.device_type, "lldp_neighbors")
        return self.send_command(cmd, delay_factor=delay)

    def get_version(self) -> str:
        """Get device version info (configurable command)."""
        cmd = get_command(self.device.device_type, "show_version")
        delay = get_command_delay(self.device.device_type, "show_version")
        return self.send_command(cmd, delay_factor=delay)

    def get_inventory(self) -> str:
        """Get device inventory (configurable command)."""
        cmd = get_command(self.device.device_type, "show_inventory")
        delay = get_command_delay(self.device.device_type, "show_inventory")
        return self.send_command(cmd, delay_factor=delay)

    def get_interfaces(self) -> str:
        """Get interface status (configurable command)."""
        cmd = get_command(self.device.device_type, "show_interfaces")
        delay = get_command_delay(self.device.device_type, "show_interfaces")
        return self.send_command(cmd, delay_factor=delay)

    def get_interface_details(self) -> str:
        """Get detailed interface information (configurable command)."""
        cmd = get_command(self.device.device_type, "show_interface_description")
        delay = get_command_delay(self.device.device_type, "show_interface_description")
        return self.send_command(cmd, delay_factor=delay)

    def get_environment(self) -> str:
        """Get environment info (configurable command)."""
        cmd = get_command(self.device.device_type, "show_environment")
        delay = get_command_delay(self.device.device_type, "show_environment")
        return self.send_command(cmd, delay_factor=delay)

    def get_cpu(self) -> str:
        """Get CPU utilization.

        Tries the configured ``show_cpu`` command and falls back to platform
        alternatives (``show system resources`` on NX-OS) until we get useful
        output. Stops as soon as one command returns non-empty data so we
        don't waste time on slow devices.
        """
        for cmd in get_command_with_fallbacks(self.device.device_type, "show_cpu"):
            delay = get_command_delay(self.device.device_type, "show_cpu")
            out = self.send_command(cmd, delay_factor=delay)
            if out and out.strip():
                return out
        return ""

    def get_memory(self) -> str:
        """Get memory usage.

        ``show processes memory`` on IOS prints a per-process table, not the
        overall memory summary our parser needs, so we try a list of platform
        variants and return the first one with content.
        """
        for cmd in get_command_with_fallbacks(self.device.device_type, "show_memory"):
            delay = get_command_delay(self.device.device_type, "show_memory")
            out = self.send_command(cmd, delay_factor=delay)
            if out and out.strip():
                return out
        return ""

    def get_interfaces(self) -> str:
        """Get interface status. Tries fallback commands when the primary
        ``show ip interface brief`` returns no data (e.g. NX-OS)."""
        for cmd in get_command_with_fallbacks(self.device.device_type, "show_interfaces"):
            delay = get_command_delay(self.device.device_type, "show_interfaces")
            out = self.send_command(cmd, delay_factor=delay)
            if out and out.strip():
                return out
        return ""

    def save_config(self) -> bool:
        """Save running config to startup config (configurable command)."""
        if not self.connection:
            return False
        try:
            cmd = get_command(self.device.device_type, "save_config")
            if not cmd or not cmd.strip():
                return False
            delay = get_command_delay(self.device.device_type, "save_config")
            self.connection.send_command(cmd, delay_factor=delay)
            return True
        except Exception as e:
            logger.error(f"Save config error on {self.device.ip_address}: {e}")
            return False


# ---- Parsers ----

def parse_cdp_neighbors(raw_output: str) -> list:
    """
    Parse 'show cdp neighbors detail' output into structured data.

    Returns a list of dicts:
    [{
        "neighbor_name": "...",
        "neighbor_ip": "...",
        "local_interface": "...",
        "neighbor_interface": "...",
        "neighbor_platform": "...",
        "neighbor_capability": "...",
    }]
    """
    neighbors = []
    if not raw_output or "CDP is not enabled" in raw_output:
        return neighbors

    # Split by "------" or "Device ID" sections
    entries = re.split(r"-{3,}", raw_output)

    for entry in entries:
        if not entry.strip():
            continue

        neighbor = {
            "neighbor_name": "",
            "neighbor_ip": "",
            "local_interface": "",
            "neighbor_interface": "",
            "neighbor_platform": "",
            "neighbor_capability": "",
        }

        # Device ID
        m = re.search(r"Device ID:\s*(.+)", entry)
        if m:
            neighbor["neighbor_name"] = m.group(1).strip()

        # IP address
        m = re.search(r"IP(?:v[46])? address:\s*(\S+)", entry)
        if m:
            neighbor["neighbor_ip"] = m.group(1).strip()
        else:
            # Try "IPv4 Address: x.x.x.x"
            m = re.search(r"IPv4 Address:\s*(\S+?)(?:\s|$)", entry)
            if m:
                neighbor["neighbor_ip"] = m.group(1).strip().rstrip(",")

        # Interface (local)
        m = re.search(r"Interface:\s*(\S+),\s*Port ID.*?:\s*(\S+)", entry)
        if m:
            neighbor["local_interface"] = m.group(1).strip()
            neighbor["neighbor_interface"] = m.group(2).strip()

        # Platform
        m = re.search(r"Platform:\s*(.+?),\s*Capabilities", entry)
        if m:
            neighbor["neighbor_platform"] = m.group(1).strip()

        # Capabilities
        m = re.search(r"Capabilities:\s*(.+)", entry)
        if m:
            neighbor["neighbor_capability"] = m.group(1).strip()

        if neighbor["neighbor_name"]:
            neighbors.append(neighbor)

    return neighbors


def parse_lldp_neighbors(raw_output: str) -> list:
    """Parse 'show lldp neighbors detail' output."""
    neighbors = []
    if not raw_output:
        return neighbors

    entries = re.split(r"-{3,}", raw_output)

    for entry in entries:
        if not entry.strip():
            continue

        neighbor = {
            "neighbor_name": "",
            "neighbor_ip": "",
            "local_interface": "",
            "neighbor_interface": "",
            "neighbor_platform": "",
            "neighbor_capability": "",
        }

        m = re.search(r"Chassis id:\s*(.+)", entry)
        if m:
            neighbor["neighbor_name"] = m.group(1).strip()

        m = re.search(r"Port id:\s*(.+)", entry)
        if m:
            neighbor["neighbor_interface"] = m.group(1).strip()

        m = re.search(r"Local Intf:\s*(.+)", entry)
        if m:
            neighbor["local_interface"] = m.group(1).strip()

        m = re.search(r"IPv4 address:\s*(\S+)", entry, re.IGNORECASE)
        if m:
            neighbor["neighbor_ip"] = m.group(1).strip()

        m = re.search(r"System description:\s*(.+)", entry)
        if m:
            neighbor["neighbor_platform"] = m.group(1).strip()

        m = re.search(r"System capabilities:\s*(.+)", entry)
        if m:
            neighbor["neighbor_capability"] = m.group(1).strip()

        if neighbor["neighbor_name"] or neighbor["neighbor_ip"]:
            neighbors.append(neighbor)

    return neighbors


def parse_version(raw_output: str) -> dict:
    """
    Parse 'show version' output.

    Returns:
    {
        "hostname": "...",
        "model": "...",
        "os_type": "...",
        "os_version": "...",
        "serial_number": "...",
        "uptime": "...",
        "uptime_seconds": 0,
    }
    """
    info = {
        "hostname": "",
        "model": "",
        "os_type": "",
        "os_version": "",
        "serial_number": "",
        "uptime": "",
        "uptime_seconds": 0,
    }

    if not raw_output:
        return info

    # Hostname
    m = re.search(r"(\S+)\s+uptime is\s*(.+)", raw_output)
    if m:
        info["hostname"] = m.group(1).strip()
        info["uptime"] = m.group(2).strip()
        info["uptime_seconds"] = _parse_uptime(info["uptime"])

    # Model / Processor
    m = re.search(r"cisco\s+([\w\-]+)\s+.*?(?:processor|CPU)", raw_output, re.IGNORECASE)
    if m:
        info["model"] = m.group(1).strip()

    m = re.search(r"Model number\s*:\s*(\S+)", raw_output)
    if m:
        info["model"] = m.group(1).strip()

    # OS Version
    m = re.search(r"Version\s+([^\s,]+)", raw_output)
    if m:
        info["os_version"] = m.group(1).strip()

    m = re.search(r"Cisco\s+IOS\s+XE\s+Software.*?Version\s+([^\s,]+)", raw_output, re.IGNORECASE)
    if m:
        info["os_type"] = "IOS-XE"
        info["os_version"] = m.group(1).strip()
    elif re.search(r"Cisco\s+IOS\s+Software", raw_output, re.IGNORECASE):
        info["os_type"] = "IOS"
    elif re.search(r"Nexus\s+OS|NX-OS", raw_output, re.IGNORECASE):
        info["os_type"] = "NX-OS"
    elif re.search(r"Cisco\s+Controller|AirOS", raw_output, re.IGNORECASE):
        info["os_type"] = "AireOS"

    # Serial Number
    m = re.search(r"Processor board ID\s+(\S+)", raw_output)
    if m:
        info["serial_number"] = m.group(1).strip()

    m = re.search(r"System Serial Number\s*:\s*(\S+)", raw_output)
    if m:
        info["serial_number"] = m.group(1).strip()

    return info


def parse_inventory(raw_output: str) -> list:
    """
    Parse 'show inventory' output.

    Returns list of:
    [{
        "name": "Chassis",
        "description": "...",
        "pid": "...",
        "vid": "...",
        "serial": "...",
    }]
    """
    items = []
    if not raw_output:
        return items

    entries = re.split(r'NAME:\s*"', raw_output)

    for entry in entries:
        if not entry.strip():
            continue

        item = {"name": "", "description": "", "pid": "", "vid": "", "serial": ""}

        m = re.match(r'([^"]+)"\s*,\s*DESCR:\s*"([^"]*)"', entry)
        if m:
            item["name"] = m.group(1).strip()
            item["description"] = m.group(2).strip()

        m = re.search(r'PID:\s*(\S+)\s*,\s*VID:\s*(\S+)\s*,\s*SN:\s*(\S+)', entry)
        if m:
            item["pid"] = m.group(1).strip()
            item["vid"] = m.group(2).strip()
            item["serial"] = m.group(3).strip()

        if item["name"] or item["pid"]:
            items.append(item)

    return items


def parse_interfaces(raw_output: str) -> list:
    """Parse 'show ip interface brief' output into structured interface data.

    Returns a list of dicts with the interface name, IP address, admin status
    and protocol status. Recognises "administratively down" as a down state.
    """
    interfaces = []
    if not raw_output:
        return interfaces

    for line in raw_output.strip().splitlines():
        # Skip headers and empty lines
        if not line.strip() or "Interface" in line or "Status" in line:
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        status = parts[4].lower()
        protocol = parts[5] if len(parts) > 5 else ""
        # "administratively down" is split across two columns: "administratively" + "down"
        if status == "administratively" and len(parts) > 5:
            status = "administratively down"
            protocol = parts[6] if len(parts) > 6 else "down"

        interfaces.append({
            "name": parts[0],
            "ip_address": parts[1],
            "ok": parts[2],
            "method": parts[3],
            "status": status,
            "protocol": protocol,
        })

    return interfaces


def _parse_uptime(uptime_str: str) -> int:
    """Parse uptime string to seconds."""
    seconds = 0
    m = re.search(r"(\d+)\s*year", uptime_str)
    if m:
        seconds += int(m.group(1)) * 365 * 24 * 3600
    m = re.search(r"(\d+)\s*week", uptime_str)
    if m:
        seconds += int(m.group(1)) * 7 * 24 * 3600
    m = re.search(r"(\d+)\s*day", uptime_str)
    if m:
        seconds += int(m.group(1)) * 24 * 3600
    m = re.search(r"(\d+)\s*hour", uptime_str)
    if m:
        seconds += int(m.group(1)) * 3600
    m = re.search(r"(\d+)\s*minute", uptime_str)
    if m:
        seconds += int(m.group(1)) * 60
    return seconds
