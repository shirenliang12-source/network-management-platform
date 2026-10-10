"""Conservative, atomic CSV imports. Identity collisions never silently overwrite."""
import csv
import io
import ipaddress
import re
import unicodedata
from fastapi import HTTPException
from sqlalchemy import text
from app.models import Device, DeviceGroup, VMInstance, ServerAsset, IPInventory


def normalized(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip().casefold()


def address(value):
    value = str(value or '').strip()
    return str(ipaddress.ip_address(value)) if value else ''


def stored_address(value):
    try:
        return address(value)
    except ValueError:
        return normalized(value)


def network_key(data):
    subnet = str(data.get('subnet') or '').strip()
    mask = str(data.get('mask') or '').strip()
    try:
        network = str(ipaddress.ip_network(subnet if '/' in subnet else f'{subnet}/{mask}', strict=False))
    except ValueError:
        network = normalized(subnet) + '|' + normalized(mask)
    # Company/firewall/interface/VLAN distinguish overlapping private address spaces.
    return tuple(normalized(data.get(k)) for k in ('company','firewall','zone_interface_name','vlan')) + (network,)


def read_rows(raw, kind):
    if len(raw) > 10_000_000 or not raw.strip() or '\ufffd' in raw:
        raise HTTPException(400, 'CSV 为空、超过 10MB 或编码无效，请使用 UTF-8 CSV')
    csv.field_size_limit(10_000_000)
    try:
        reader = csv.reader(io.StringIO(raw.lstrip('\ufeff')), strict=True)
        header = next(reader)
        if kind == 'ip_inventory':
            from app.routers.ip_inventory import _IMPORT_HEADER_MAP
            fields = [_IMPORT_HEADER_MAP.get(h.strip().lower()) or _IMPORT_HEADER_MAP.get(h.lower().replace(' ','')) for h in header]
            required = {'subnet'}
        else:
            fields = [h.strip().lower() for h in header]
            required = {'name','ip_address'} if kind == 'devices' else {'name'}
        mapped = [f for f in fields if f]
        if not required <= set(mapped) or len(mapped) != len(set(mapped)):
            raise ValueError('缺少必填表头或存在重复含义的表头')
        rows = []
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            if len(row) != len(header):
                raise ValueError(f'第 {reader.line_num} 行列数不一致，请检查 CSV 引号')
            rows.append((reader.line_num, {k:v.strip() for k,v in zip(fields,row) if k}))
        if len(rows) > 50000:
            raise ValueError('单次最多导入 50000 行')
        return rows
    except (ValueError, csv.Error, StopIteration) as exc:
        raise HTTPException(400, f'未导入任何数据：{exc}') from None


def run_import(db, kind, raw):
    rows = read_rows(raw, kind)
    created, duplicates, errors = [], [], []
    model = {'devices':Device, 'vms':VMInstance, 'ip_inventory':IPInventory}[kind]
    try:
        # SQLite has no uniqueness constraint for several legacy identities.
        # Reserve the writer before reading them, including simultaneous uploads.
        if db.get_bind().dialect.name == 'sqlite' and not db.connection().connection.driver_connection.in_transaction:
            db.execute(text('BEGIN IMMEDIATE'))
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(190951)'))
        existing = db.query(model).all()
        identities = {}

        def keys_for(row):
            if kind == 'ip_inventory':
                return [('network',network_key({k:getattr(row,k) for k in ('subnet','mask','company','firewall','zone_interface_name','vlan')}))]
            ip_field = 'ip_address' if kind == 'devices' else 'management_ip'
            extra_rows = row.extra_ips if kind == 'devices' else row.additional_ips
            return [('name',normalized(row.name))] + [('ip',stored_address(value)) for value in
                    [getattr(row,ip_field)] + [p.ip_address for p in extra_rows] if value]

        def index(row):
            for key in keys_for(row):
                identities.setdefault(key, []).append(row)

        for row in existing:
            index(row)
        max_order = max((r.sort_order or 0 for r in existing), default=0) if kind == 'ip_inventory' else 0
        for line, data in rows:
            try:
                if kind == 'ip_inventory':
                    if not data.get('subnet'):
                        raise ValueError('缺少子网，无法建立去重标识')
                    key = network_key(data)
                    matches = identities.get(('network',key), [])
                else:
                    if not data.get('name'):
                        raise ValueError('名称不能为空')
                    ip_field = 'ip_address' if kind == 'devices' else 'management_ip'
                    ip = address(data.get(ip_field))
                    if kind == 'devices' and not ip:
                        raise ValueError('设备管理 IP 不能为空')
                    extras = [address(data[k]) for k in sorted(data, key=lambda k:int(k[2:]) if re.fullmatch(r'ip\d+',k) else 0)
                              if re.fullmatch(r'ip\d+',k) and int(k[2:]) >= 2 and data[k]]
                    incoming_ips = set([ip] + extras) - {''}
                    keys = [('name',normalized(data['name']))] + [('ip',value) for value in incoming_ips]
                    matches = list({r.id:r for key in keys for r in identities.get(key,[])}.values())
                if matches:
                    duplicates.append({'line':line, 'existing_ids':[r.id for r in matches],
                                       'reason':'标识重复或冲突，已跳过；如需修改请核对现有记录后编辑'})
                    continue
                if kind == 'ip_inventory':
                    max_order += 1
                    row = IPInventory(**{k:data.get(k,'') for k in ('firewall','subnet','company','ip_segment','mask','vlan','zone_interface_name','remarks')},sort_order=max_order,network_type='有线')
                elif kind == 'devices':
                    group_id = None
                    if data.get('group_name'):
                        groups = [g for g in db.query(DeviceGroup).all() if normalized(g.name)==normalized(data['group_name'])]
                        if len(groups)>1:
                            raise ValueError('分组名称存在多条匹配，请先确认分组')
                        group = groups[0] if groups else DeviceGroup(name=data['group_name'],description='')
                        db.add(group); db.flush(); group_id=group.id
                    port = int(data.get('port') or 22)
                    if not 1 <= port <= 65535:
                        raise ValueError('SSH 端口须为 1–65535')
                    row = Device(name=data['name'],ip_address=ip,group_id=group_id,port=port,
                                 device_type=data.get('device_type') or 'cisco_ios', username=data.get('username') or 'admin',
                                 **{k:data.get(k,'') for k in ('company','model','function')},is_active=True)
                    row.set_password(data.get('password','')); row.set_enable_password(data.get('enable_password',''))
                else:
                    from app.routers.vms import _valid, _dump_disks, _parse_disks_csv, _parse_disks, VM_OS_TYPES, VM_STATUSES
                    hosts = [h for h in db.query(ServerAsset).all() if normalized(h.name)==normalized(data.get('host_name'))] if data.get('host_name') else []
                    if len(hosts)>1:
                        raise ValueError('宿主机名称存在多条匹配，不能自动关联')
                    disks = _dump_disks(_parse_disks_csv(data.get('disks','')))
                    count = len(_parse_disks(disks)) if disks else int(data.get('disk_count') or 1)
                    if count < 0:
                        raise ValueError('磁盘数量不能为负数')
                    row = VMInstance(name=data['name'],management_ip=ip,host_id=hosts[0].id if hosts else None,
                                     os_type=_valid(data.get('os_type'),VM_OS_TYPES,'Linux'), status=_valid(data.get('status'),VM_STATUSES,'运行中'),
                                     disks=disks,disk_count=count,**{k:data.get(k,'') for k in ('function','os_version','host_name','cpu','memory','disk_size','storage_lun','notes')})
                db.add(row); db.flush()
                if kind == 'devices':
                    from app.routers.devices import _set_device_ips
                    _set_device_ips(db,row,extras)
                elif kind == 'vms':
                    from app.routers.vms import _set_vm_ips
                    _set_vm_ips(db,row,extras)
                db.flush()
                if kind == 'devices':
                    db.expire(row, ['extra_ips'])
                elif kind == 'vms':
                    db.expire(row, ['additional_ips'])
                created.append(row); index(row)
            except (ValueError, TypeError):
                # Do not expose raw values (CSV may include credentials).
                errors.append(f'第 {line} 行字段无效或关联不唯一，请核对 IP、端口、数量及宿主机/分组')
                break
        if errors:
            db.rollback()
            raise HTTPException(400, '本次全部回滚，未导入任何数据：' + '；'.join(errors))
        if kind == 'devices':
            from app.services.device_ip_link import sync_device_auto_links
            for row in created:
                sync_device_auto_links(db, row, commit=False)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(409, '导入失败，已全部回滚；请检查数据约束或稍后重试') from None
    return {'created':len(created),'imported':len(created),'skipped':len(duplicates),'errors':[],
            'duplicates':duplicates[:200], 'details_truncated':len(duplicates)>200,
            'message':f'新增 {len(created)} 条，重复或冲突跳过 {len(duplicates)} 条；未覆盖已有数据'}
