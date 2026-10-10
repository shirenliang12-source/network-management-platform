"""Topology building service using neighbor discovery data."""
import math
import logging
import networkx as nx
from datetime import datetime
from sqlalchemy.orm import Session

from app.models import Device, Neighbor, DeviceInfo, DeviceGroup

logger = logging.getLogger(__name__)

# Lightweight device-type label map (avoids pulling frontend code into backend)
_DEVICE_TYPE_LABELS = {
    "cisco_ios": "Cisco IOS",
    "cisco_ios_xe": "Cisco IOS-XE",
    "cisco_nxos": "Cisco NX-OS",
    "cisco_wlc": "Cisco WLC",
    "cisco_ap": "Cisco AP",
    "unknown": "未知",
}


def _xml_escape(value: str) -> str:
    """Escape a string for safe inclusion in XML text/attribute values."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "&#10;")
    )


def _normalize_hostname(name: str) -> str:
    """Normalize a CDP/LLDP hostname for matching against managed devices.

    CDP/LLDP frequently report names as FQDNs ("sw1.corp.local") or with an
    appended serial ("sw1(FDO1234ABC)"). Both forms must match the managed
    device simply named "SW1".
    """
    if not name:
        return ""
    n = str(name).strip().lower()
    if "(" in n:
        n = n.split("(", 1)[0]
    if "." in n:
        n = n.split(".", 1)[0]
    return n.strip()


def build_topology(db: Session) -> dict:
    """
    Build network topology from neighbor discovery data.

    Returns:
    {
        "nodes": [...],
        "edges": [...],
        "total_nodes": int,
        "total_edges": int,
    }
    """
    # Get all devices
    devices = db.query(Device).filter(Device.is_active == True).all()

    # Get all neighbors
    neighbors = db.query(Neighbor).all()

    # ------------------------------------------------------------------
    # Resolution indexes.
    #
    # A neighbor row may have been written BEFORE the corresponding device
    # was added to inventory (this is exactly what auto-add discovery does),
    # leaving neighbor_device_id NULL. If we trusted that FK alone we would
    # render a phantom "discovered" node next to the real managed device,
    # inflating the node count so it no longer matches the device count.
    # So we also resolve by IP address and by normalized hostname.
    # ------------------------------------------------------------------
    devices_by_id = {d.id: d for d in devices}
    devices_by_ip = {}
    devices_by_name = {}
    for d in devices:
        if d.ip_address:
            devices_by_ip.setdefault(d.ip_address.strip(), d)
        key = _normalize_hostname(d.name)
        if key:
            devices_by_name.setdefault(key, d)

    def _resolve_managed(nbr):
        """Return the managed Device this neighbor refers to, or None."""
        if nbr.neighbor_device_id and nbr.neighbor_device_id in devices_by_id:
            return devices_by_id[nbr.neighbor_device_id]
        if nbr.neighbor_ip:
            match = devices_by_ip.get(nbr.neighbor_ip.strip())
            if match:
                return match
        return devices_by_name.get(_normalize_hostname(nbr.neighbor_name))

    # Build graph
    G = nx.Graph()

    # Track all node IDs (managed + discovered)
    node_map = {}  # node_id -> node_data
    edge_set = set()  # Track unique edges to avoid duplicates

    # Add managed devices as nodes
    for device in devices:
        node_id = f"dev_{device.id}"
        node_data = {
            "id": node_id,
            "label": device.name,
            "ip": device.ip_address,
            "device_type": device.device_type,
            "status": device.status,
            "group": "",
            "is_managed": True,
            "serial_number": "",
            "os_version": "",
            "model": "",
        }

        # Get group name
        if device.group:
            node_data["group"] = device.group.name

        # Get latest device info
        latest_info = (
            db.query(DeviceInfo)
            .filter(DeviceInfo.device_id == device.id)
            .order_by(DeviceInfo.collected_at.desc())
            .first()
        )
        if latest_info:
            node_data["serial_number"] = latest_info.serial_number
            node_data["os_version"] = latest_info.os_version
            node_data["model"] = latest_info.model

        G.add_node(node_id, **node_data)
        node_map[node_id] = node_data

    # Add neighbor relationships as edges and discover unmanaged nodes
    unmanaged_ids = {}  # dedupe key -> node_id, so one physical box = one node
    backfilled = 0

    for neighbor in neighbors:
        source_id = f"dev_{neighbor.device_id}"
        source_device = devices_by_id.get(neighbor.device_id)
        if not source_device:
            continue

        # Determine target node
        target_device = _resolve_managed(neighbor)
        if target_device is not None:
            # Linked to a managed device (already added as a node above)
            target_id = f"dev_{target_device.id}"
            # Self-heal the FK so future queries are cheap and consistent.
            if neighbor.neighbor_device_id != target_device.id:
                neighbor.neighbor_device_id = target_device.id
                backfilled += 1
        else:
            # Unmanaged/discovered neighbor - create a virtual node.
            # Dedupe on IP (or hostname) so the same box seen from several
            # switches collapses into a single node instead of one per link.
            dedupe_key = (
                neighbor.neighbor_ip.strip()
                if neighbor.neighbor_ip
                else _normalize_hostname(neighbor.neighbor_name)
            ) or f"__nbr_{neighbor.id}"

            if dedupe_key in unmanaged_ids:
                target_id = unmanaged_ids[dedupe_key]
            else:
                target_id = f"nbr_{neighbor.id}"
                unmanaged_ids[dedupe_key] = target_id
                target_label = (
                    neighbor.neighbor_name
                    or neighbor.neighbor_ip
                    or f"Unknown-{neighbor.id}"
                )
                target_data = {
                    "id": target_id,
                    "label": target_label,
                    "ip": neighbor.neighbor_ip,
                    "device_type": _guess_device_type(neighbor.neighbor_platform),
                    "status": "discovered",
                    "group": "Discovered",
                    "is_managed": False,
                    "serial_number": "",
                    "os_version": "",
                    "model": neighbor.neighbor_platform,
                }
                G.add_node(target_id, **target_data)
                node_map[target_id] = target_data

        # A device reporting itself (e.g. via a loopback) is not a link
        if target_id == source_id:
            continue

        # Create edge (avoid duplicates)
        edge_key = tuple(sorted([source_id, target_id]))
        if edge_key not in edge_set:
            edge_data = {
                "source": source_id,
                "target": target_id,
                "label": f"{neighbor.local_interface} <-> {neighbor.neighbor_interface}",
                "source_interface": neighbor.local_interface,
                "target_interface": neighbor.neighbor_interface,
                "protocol": neighbor.protocol,
            }
            G.add_edge(source_id, target_id, **edge_data)
            edge_set.add(edge_key)

    # Persist any neighbor->device links we resolved, so the data self-heals.
    if backfilled:
        try:
            db.commit()
            logger.info("Topology: backfilled %d neighbor->device links", backfilled)
        except Exception as exc:  # pragma: no cover - non-fatal
            db.rollback()
            logger.warning("Topology: failed to backfill neighbor links: %s", exc)

    # Build response
    nodes = []
    for node_id in G.nodes():
        data = G.nodes[node_id]
        nodes.append({
            "id": data.get("id", node_id),
            "label": data.get("label", node_id),
            "ip": data.get("ip", ""),
            "device_type": data.get("device_type", ""),
            "status": data.get("status", "unknown"),
            "group": data.get("group", ""),
            "is_managed": data.get("is_managed", False),
            "serial_number": data.get("serial_number", ""),
            "os_version": data.get("os_version", ""),
            "model": data.get("model", ""),
        })

    edges = []
    for u, v in G.edges():
        edge_data = G.edges[u, v]
        edges.append({
            "source": edge_data.get("source", u),
            "target": edge_data.get("target", v),
            "label": edge_data.get("label", ""),
            "source_interface": edge_data.get("source_interface", ""),
            "target_interface": edge_data.get("target_interface", ""),
            "protocol": edge_data.get("protocol", "cdp"),
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


def _guess_device_type(platform: str) -> str:
    """Guess device type from CDP/LLDP platform string."""
    if not platform:
        return "unknown"
    platform_lower = platform.lower()

    if "wlc" in platform_lower or "aireos" in platform_lower or "controller" in platform_lower:
        return "cisco_wlc"
    if "air-" in platform_lower or "ap" in platform_lower:
        return "cisco_ap"
    if "nexus" in platform_lower:
        return "cisco_nxos"
    if "catalyst" in platform_lower or "ws-" in platform_lower:
        return "cisco_ios"
    return "cisco_ios"


def get_topology_stats(db: Session) -> dict:
    """Get topology statistics.

    The numbers are internally consistent:
      total_nodes = managed_devices + discovered_unmanaged_neighbors
      total_edges = number of physical CDP/LLDP links
    """
    topology = build_topology(db)
    managed = sum(1 for n in topology["nodes"] if n.get("is_managed"))
    unmanaged = len(topology["nodes"]) - managed

    # Count by device type (managed devices only)
    type_counts = {}
    devices = db.query(Device).filter(Device.is_active == True).all()
    for d in devices:
        type_counts[d.device_type] = type_counts.get(d.device_type, 0) + 1

    return {
        "total_managed_devices": managed,
        "total_unmanaged_discovered": unmanaged,
        "total_nodes": topology["total_nodes"],
        "total_edges": topology["total_edges"],
        "device_type_distribution": type_counts,
    }


def export_topology_graphml(db: Session, filepath: str) -> bool:
    """Export topology as GraphML file."""
    try:
        topology = build_topology(db)
        G = nx.Graph()

        for node in topology["nodes"]:
            G.add_node(node["id"], **{k: str(v) for k, v in node.items()})

        for edge in topology["edges"]:
            G.add_edge(edge["source"], edge["target"],
                       **{k: str(v) for k, v in edge.items() if k not in ["source", "target"]})

        nx.write_graphml(G, filepath)
        return True
    except Exception as e:
        logger.error(f"Failed to export topology: {e}")
        return False


def export_topology_drawio(db: Session, filepath: str) -> bool:
    """Export topology as a draw.io / diagrams.net (.drawio) mxGraph XML file.

    The generated file can be opened directly in draw.io (https://app.diagrams.net)
    or imported into an existing draw.io diagram. Nodes are laid out on a grid and
    colored by status; edges are labeled with the connecting interfaces/protocol.
    """
    try:
        import os
        topology = build_topology(db)
        nodes = topology["nodes"]
        edges = topology["edges"]

        n = len(nodes)
        cols = int(math.ceil(math.sqrt(n))) if n > 0 else 1
        rows = int(math.ceil(n / cols)) if cols else 1
        col_w, row_h = 230, 100
        margin = 40

        pos = {}
        for i, node in enumerate(nodes):
            r = i // cols
            c = i % cols
            pos[node["id"]] = (margin + c * col_w, margin + r * row_h)

        status_color = {
            "online": "#28a745",
            "offline": "#dc3545",
            "unknown": "#1a73e8",
            "discovered": "#9c27b0",
        }

        parts = []
        parts.append('<?xml version="1.0" encoding="UTF-8"?>')
        parts.append(
            '<mxGraphModel dx="1000" dy="700" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="1200" pageHeight="1700" math="0" shadow="0">'
        )
        parts.append('  <root>')
        parts.append('    <mxCell id="0"></mxCell>')
        parts.append('    <mxCell id="1" parent="0"></mxCell>')

        for node in nodes:
            x, y = pos[node["id"]]
            color = status_color.get(node["status"], "#1a73e8")
            type_label = _DEVICE_TYPE_LABELS.get(node["device_type"], node["device_type"])
            label = node["label"]
            if node["ip"]:
                label += f"\n{node['ip']}"
            if node.get("model"):
                label += f"\n{node['model']}"
            if not node.get("is_managed"):
                label += "\n(未管理)"
            parts.append(
                f'    <mxCell id="{_xml_escape(node["id"])}" value="{_xml_escape(label)}" '
                f'style="rounded=1;whiteSpace=wrap;html=1;fillColor={color};fontColor=#ffffff;'
                f'fontSize=12;strokeColor=#333333;" vertex="1" parent="1">'
            )
            parts.append(
                f'      <mxGeometry x="{x}" y="{y}" width="200" height="64" as="geometry"></mxGeometry>'
            )
            parts.append('    </mxCell>')

        for i, edge in enumerate(edges):
            label = edge.get("label", "") or ""
            if edge.get("protocol"):
                label = (label + f"\n[{edge['protocol'].upper()}]").strip()
            parts.append(
                f'    <mxCell id="edge_{i}" value="{_xml_escape(label)}" '
                f'style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;fontSize=10;'
                f'endArrow=none;strokeColor=#888888;" edge="1" parent="1" '
                f'source="{_xml_escape(edge["source"])}" target="{_xml_escape(edge["target"])}">'
            )
            parts.append('      <mxGeometry relative="1" as="geometry"></mxGeometry>')
            parts.append('    </mxCell>')

        parts.append('  </root>')
        parts.append('</mxGraphModel>')

        # dirname() is empty for a bare filename - nothing to create in that case
        out_dir = os.path.dirname(filepath)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        return True
    except Exception as e:
        logger.error(f"Failed to export draw.io topology: {e}")
        return False
