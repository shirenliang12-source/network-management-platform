"""Network interface detection service."""
import logging
import socket

logger = logging.getLogger(__name__)
PREFERRED_SOURCE_IP_KEY = "preferred_source_ip"


def get_preferred_source_ip(db) -> str:
    """Return the operator-selected default source IP, if configured."""
    from app.models import SystemSetting

    row = db.get(SystemSetting, PREFERRED_SOURCE_IP_KEY)
    return str(row.value or "").strip() if row else ""


def get_network_interfaces() -> list:
    """
    Detect all network interfaces with IPv4 addresses.
    
    Returns a list of:
    [{
        "name": "Ethernet0",
        "ip_address": "192.168.1.100",
        "is_up": True,
        "is_loopback": False,
    }]
    """
    interfaces = []

    try:
        import psutil
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        for name, addr_list in addrs.items():
            for addr in addr_list:
                if addr.family.name == "AF_INET":
                    ip = addr.address
                    is_up = stats[name].isup if name in stats else False
                    is_loopback = ip.startswith("127.")
                    
                    interfaces.append({
                        "name": name,
                        "ip_address": ip,
                        "is_up": is_up,
                        "is_loopback": is_loopback,
                    })
    except ImportError:
        # Fallback: use socket and ipconfig
        logger.warning("psutil not available, using fallback detection")
        interfaces = _get_interfaces_fallback()

    # Sort: up interfaces first, then by name
    interfaces.sort(key=lambda x: (not x["is_up"], x["name"]))
    return interfaces


def _get_interfaces_fallback() -> list:
    """Fallback interface detection without psutil."""
    import subprocess
    import re
    import platform

    interfaces = []

    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="gbk",
                errors="replace",
            )
            output = result.stdout

            current_adapter = None
            current_disconnected = False

            for line in output.splitlines():
                # Adapter headings are the only non-indented lines ending in
                # a colon. The old parser detected English headings but forgot
                # to assign their name, leaving every NIC dropdown empty.
                adapter_match = re.match(r"^(\S.*?):\s*$", line)
                if adapter_match:
                    heading = adapter_match.group(1).strip()
                    name = re.sub(r"^.*?\badapter\s+", "", heading, flags=re.IGNORECASE)
                    if "适配器" in name:
                        name = name.split("适配器", 1)[1].strip()
                    current_adapter = name or heading
                    current_disconnected = False

                if "Media disconnected" in line or "媒体已断开" in line:
                    current_disconnected = True

                # IPv4 line (handles both English and Chinese Windows)
                ip_match = re.search(r"IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", line, re.IGNORECASE)
                if ip_match and current_adapter:
                    ip = ip_match.group(1)
                    interfaces.append({
                        "name": current_adapter,
                        "ip_address": ip,
                        "is_up": not current_disconnected,
                        "is_loopback": ip.startswith("127."),
                    })
                    current_adapter = None
                    current_disconnected = False
        except Exception as e:
            logger.error(f"Fallback interface detection failed: {e}")
    else:
        # Linux/Mac
        try:
            result = subprocess.run(
                ["ip", "-o", "-4", "addr"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    ifname = parts[1]
                    ip = parts[3].split("/")[0]
                    interfaces.append({
                        "name": ifname,
                        "ip_address": ip,
                        "is_up": True,
                        "is_loopback": ip.startswith("127."),
                    })
        except Exception as e:
            logger.error(f"Linux interface detection failed: {e}")

    return interfaces


def get_default_source_ip() -> str:
    """Get the default outbound IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"
