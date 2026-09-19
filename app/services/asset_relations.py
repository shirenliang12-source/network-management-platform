"""Read-only cross-inventory navigation. Never silently create or rewrite links."""
import ipaddress
from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from app.models import VMInstance, ServerAsset, DCRack, DCSite, IPAMPrefix, IPAMIPAddress, Device

MODELS = {'device': Device, 'vm': VMInstance, 'server': ServerAsset, 'rack': DCRack,
          'site': DCSite, 'prefix': IPAMPrefix, 'ip': IPAMIPAddress}
MODULES = {'device': 'devices', 'vm': 'vms', 'server': 'servers', 'rack': 'dc', 'site': 'dc', 'prefix': 'ipam', 'ip': 'ipam'}


def request_modules(request):
    user = getattr(request.state, 'user', None)
    if user is None:
        raise HTTPException(401, '请先登录')
    return set(MODULES.values()) if user.is_superuser else set(user.get_modules() or [])


def _address(value):
    try:
        return ipaddress.ip_interface(str(value).strip()).ip
    except ValueError:
        return None


def _network(value):
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None


def _vm_ips(row):
    values = [row.management_ip] + [ip.ip_address for ip in row.additional_ips]
    return list(dict.fromkeys(str(ip) for value in values if (ip := _address(value)) is not None))


def _node(kind, row, detail=''):
    labels = {'prefix': 'prefix', 'ip': 'address', 'rack': 'rack_number'}
    return {'kind': kind, 'id': row.id, 'name': getattr(row, labels.get(kind, 'name')) or str(row.id), 'detail': detail}


def relations(db, kind, row_id, allowed=None):
    allowed = set(MODULES.values()) if allowed is None else allowed
    if MODULES[kind] not in allowed:
        raise HTTPException(403, '无权查看此模块')
    root = db.get(MODELS[kind], row_id)
    if root is None:
        raise HTTPException(404, '记录不存在或已被删除')
    groups, notes, allocations = [], [], []
    def add(title, nodes):
        groups.append({'title': title, 'items': nodes})
    def location(server):
        if 'dc' not in allowed:
            return
        rack = db.get(DCRack, server.rack_id) if server.rack_id else None
        if rack:
            add('所在机柜', [_node('rack', rack, f'U{server.u_start}–U{server.u_start + (server.u_size or 1) - 1}')])
            site = db.get(DCSite, rack.site_id)
            if site:
                add('所属数据中心 / 站点', [_node('site', site, site.company or '')])
    vms = []
    if 'vms' in allowed:
        vms = db.query(VMInstance).options(selectinload(VMInstance.additional_ips)).order_by(VMInstance.name, VMInstance.id).all()
    if kind == 'vm':
        ips = _vm_ips(root)
        notes.append('主 IP 与附加 IP：' + ('、'.join(ips) or '未填写有效 IP'))
        if 'ipam' in allowed:
            prefixes = []
            for prefix in db.query(IPAMPrefix).all():
                net = _network(prefix.prefix)
                matched = [ip for ip in ips if net and _address(ip).version == net.version and _address(ip) in net]
                if matched:
                    prefixes.append((net.prefixlen, _node('prefix', prefix, '命中 IP：' + '、'.join(matched))))
                    allocations.extend({'vm_id': root.id, 'prefix_id': prefix.id, 'address': ip, 'label': f'{root.name} · {ip} → {prefix.prefix}'} for ip in matched)
            add('所属规划网段（最具体的网段优先）', [node for _, node in sorted(prefixes, key=lambda pair: -pair[0])])
            add('已登记的规划 IP', [_node('ip', ip, ip.description or '') for ip in db.query(IPAMIPAddress).all()
                                      if str(_address(ip.address)) in ips])
            notes.append('反查不会自动占用；请在下方手动登记占用。占用前将检查地址冲突。')
        if 'servers' in allowed:
            host = db.get(ServerAsset, root.host_id) if root.host_id else None
            add('已关联的宿主硬件', [_node('server', host, host.category or '')] if host else [])
            if host:
                location(host)
            elif root.host_name:
                candidates = [s for s in db.query(ServerAsset).all() if s.name.strip().casefold() == root.host_name.strip().casefold()]
                add('同名硬件候选（尚未绑定）', [_node('server', s) for s in candidates])
            notes.append('宿主机关系在虚拟机“编辑 → 宿主机”中维护；同名候选不会自动绑定。')
    elif kind in {'prefix', 'ip'} and 'vms' in allowed:
        net = _network(root.prefix) if kind == 'prefix' else None
        exact = _address(root.address) if kind == 'ip' else None
        matches = []
        counts = {}
        for row in vms:
            matched = [ip for ip in _vm_ips(row) if (_address(ip) == exact if kind == 'ip'
                       else net is not None and _address(ip).version == net.version and _address(ip) in net)]
            if matched:
                matches.append(_node('vm', row, '匹配 IP：' + '、'.join(matched)))
                allocations.extend({'vm_id': row.id, 'prefix_id': root.id if kind == 'prefix' else root.prefix_id, 'address': ip, 'label': f'{row.name} · {ip}'} for ip in matched)
                for ip in matched:
                    counts[ip] = counts.get(ip, 0) + 1
        add('IP 匹配的虚拟机', matches)
        if any(count > 1 for count in counts.values()):
            notes.append('存在相同 IP 的多台虚拟机：可能是地址冲突或隔离网络，请人工确认，系统未自动绑定。')
        if kind == 'ip':
            prefix = db.get(IPAMPrefix, root.prefix_id)
            if prefix:
                add('所在规划网段', [_node('prefix', prefix)])
    elif kind == 'server':
        location(root)
        if 'vms' in allowed:
            add('以本硬件为宿主的虚拟机', [_node('vm', row, '、'.join(_vm_ips(row))) for row in vms if row.host_id == root.id])
            add('同名宿主候选（尚未绑定）', [_node('vm', row) for row in vms if row.host_id is None and row.host_name
                                        and row.host_name.strip().casefold() == root.name.strip().casefold()])
        notes.append('所在机柜和 U 位在服务器存储“编辑”中维护；机柜再关联数据中心站点。')
    elif kind in {'rack', 'site'}:
        racks = [root] if kind == 'rack' else db.query(DCRack).filter_by(site_id=root.id).all()
        rack_ids = [rack.id for rack in racks]
        if kind == 'site':
            add('所属机柜', [_node('rack', rack) for rack in racks])
        else:
            site = db.get(DCSite, root.site_id)
            if site:
                add('所属数据中心 / 站点', [_node('site', site)])
        if 'servers' in allowed:
            servers = db.query(ServerAsset).filter(ServerAsset.rack_id.in_(rack_ids)).all()
            add('服务器 / 存储硬件', [_node('server', row, f'{row.category} · U{row.u_start} · {row.management_ip or ""}') for row in servers])
            if 'vms' in allowed:
                server_ids = {row.id for row in servers}
                add('通过宿主硬件关联的虚拟机', [_node('vm', row, '、'.join(_vm_ips(row))) for row in vms if row.host_id in server_ids])
    # Physical inventory participates in reverse lookup too. Match normalized
    # host addresses, including imported CIDR notation, without persisting links.
    def physical_ips(row):
        primary = row.ip_address if isinstance(row, Device) else row.management_ip
        return {ip for value in [primary] + [x.ip_address for x in row.extra_ips]
                if (ip := _address(value)) is not None}
    if kind in {'device', 'server', 'rack', 'site'} and 'ipam' in allowed:
        rows = [root] if kind in {'device', 'server'} else (
            db.query(ServerAsset).filter(ServerAsset.rack_id.in_(rack_ids)).all() if 'servers' in allowed else [])
        addresses = set().union(*(physical_ips(row) for row in rows))
        add('已登记的规划 IP', [_node('ip', row, row.description or '')
            for row in db.query(IPAMIPAddress).all() if _address(row.address) in addresses])
        add('所属规划网段', [_node('prefix', row) for row in db.query(IPAMPrefix).all()
            if (net := _network(row.prefix)) is not None and any(ip.version == net.version and ip in net for ip in addresses)])
    if kind in {'prefix', 'ip'}:
        net = _network(root.prefix) if kind == 'prefix' else None
        exact = _address(root.address) if kind == 'ip' else None
        for target, model, relationship, title in [('device', Device, Device.extra_ips, 'IP 匹配的管理设备'),
                                                  ('server', ServerAsset, ServerAsset.extra_ips, 'IP 匹配的服务器 / 存储')]:
            if MODULES[target] not in allowed:
                continue
            matches = []
            for row in db.query(model).options(selectinload(relationship)).all():
                matched = [ip for ip in physical_ips(row) if (ip == exact if kind == 'ip'
                           else net is not None and ip.version == net.version and ip in net)]
                if matched:
                    matches.append(_node(target, row, '匹配 IP：' + '、'.join(sorted(map(str, matched)))))
                    if target == 'server':
                        location(row)
            add(title, matches)
        notes.append('IP 匹配仅供反查，不自动绑定或占用地址；同 IP 多条记录请核对隔离网络和地址冲突。')
    if allowed != set(MODULES.values()):
        notes.append('仅展示当前账号有权限访问的模块。')
    if {'ipam', 'vms'} <= allowed and kind in {'vm', 'prefix', 'ip'}:
        for row in db.query(IPAMIPAddress).filter(IPAMIPAddress.assigned_vm_id.isnot(None)).all():
            if (kind == 'vm' and row.assigned_vm_id == root.id) or (kind == 'prefix' and row.prefix_id == root.id) or (kind == 'ip' and row.id == root.id):
                allocations = [a for a in allocations if not (a['prefix_id'] == row.prefix_id and a['address'] == row.address)]
                allocations.append({'vm_id': row.assigned_vm_id, 'prefix_id': row.prefix_id, 'address': row.address, 'release': True, 'label': f'{row.address} · 已手动占用（VM #{row.assigned_vm_id}）'})
    return {'root': _node(kind, root), 'groups': groups, 'notes': notes, 'allocations': allocations}
