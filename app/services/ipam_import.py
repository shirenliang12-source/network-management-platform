"""Atomic, explicit IPAM CSV relationships. Never guess between duplicate names."""
import csv
import io
import ipaddress
from fastapi import HTTPException
from app.models import IPAMAggregate, IPAMPrefix, IPAMIPAddress, Device


def network(value):
    return ipaddress.ip_network(str(value).strip(), strict=False)


def canonical(value):
    try:
        return str(network(value))
    except ValueError:
        return str(value)


def unique(rows, label):
    if len(rows) != 1:
        raise ValueError(f'{label}匹配 {len(rows)} 条记录，必须明确且唯一；请先处理重复或补齐引用')
    return rows[0]


def contains(parent, child, strict=False):
    if parent.version != child.version or not child.subnet_of(parent) or (strict and parent == child):
        raise ValueError(f'{child} 不属于 {parent} 的有效子网')


def validate_prefix(db, cidr, aggregate_id, parent_id, own_id=None):
    try:
        net=network(cidr)
        if parent_id:
            parent=db.get(IPAMPrefix,parent_id)
            if not parent:raise ValueError('父网段不存在')
            contains(network(parent.prefix),net,True)
            if aggregate_id is None:aggregate_id=parent.aggregate_id
            if parent.aggregate_id!=aggregate_id:raise ValueError('父子网段必须属于同一聚合')
            seen={own_id} if own_id else set()
            cursor=parent
            while cursor:
                if cursor.id in seen:raise ValueError('父网段链路存在循环')
                seen.add(cursor.id)
                cursor=db.get(IPAMPrefix,cursor.parent_id) if cursor.parent_id else None
        if aggregate_id:
            agg=db.get(IPAMAggregate,aggregate_id)
            if not agg:raise ValueError('聚合不存在')
            contains(network(agg.prefix),net)
        duplicates=[p for p in db.query(IPAMPrefix).filter_by(aggregate_id=aggregate_id).all() if p.id!=own_id and canonical(p.prefix)==str(net)]
        if duplicates:raise ValueError('同一聚合中已存在此网段')
        if own_id:
            for child in db.query(IPAMPrefix).filter_by(parent_id=own_id).all():
                contains(net,network(child.prefix),True)
                if child.aggregate_id!=aggregate_id:raise ValueError('此修改会使子网段聚合不一致，请先调整子网段')
        return str(net),aggregate_id
    except ValueError as exc:
        raise HTTPException(409,str(exc))


def run_import(db, kind, raw):
    from app.routers.ipam import AGG_IMPORT_MAP, PREFIX_IMPORT_MAP, IP_IMPORT_MAP, _map_headers, IPAM_STATUSES, IPAM_ALLOCATION_TYPES
    mappings = {'aggregates': AGG_IMPORT_MAP, 'prefixes': PREFIX_IMPORT_MAP, 'ips': IP_IMPORT_MAP}
    required = {'aggregates': {'name', 'prefix'}, 'prefixes': {'prefix'}, 'ips': {'prefix', 'address'}}[kind]
    raw = raw.lstrip('\ufeff')
    if '\ufffd' in raw:
        raise HTTPException(400, '文件编码无法正确识别，请另存为 UTF-8 CSV 后重新导入')
    if not raw.strip():
        raise HTTPException(400, 'CSV 内容为空')
    csv.field_size_limit(10_000_000)
    try:
        try:
            dialect = csv.Sniffer().sniff(raw[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(raw), dialect=dialect, strict=True)
        header = next(reader)
        fields = _map_headers(header, mappings[kind])
        if not required <= fields.keys():
            raise ValueError('缺少必填列：' + ', '.join(sorted(required-fields.keys())))
        # Reject two headers mapping to the same field rather than picking one.
        mapped = [_map_headers([h], mappings[kind]) for h in header]
        names = [next(iter(m)) for m in mapped if m]
        if len(names) != len(set(names)):
            raise ValueError('存在重复含义的表头，请每个字段只保留一列')
        pending = []
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            if len(row) != len(header):
                raise ValueError(f'第 {reader.line_num} 行列数与表头不一致，请检查引号和分隔符')
            data = {key: row[index].strip() for key,index in fields.items()}
            if any(not data.get(f) for f in required):
                raise ValueError(f'第 {reader.line_num} 行缺少必填字段')
            try:
                net = network(data['prefix'])
            except ValueError:
                raise ValueError(f'第 {reader.line_num} 行 CIDR 无效：{data["prefix"]}')
            data['prefix'] = str(net)
            pending.append((reader.line_num, data, net))
        if len(pending) > 50000:
            raise ValueError('单次最多导入 50000 行，请拆分文件')
    except (ValueError, csv.Error, StopIteration) as exc:
        raise HTTPException(400, f'未导入任何记录：{exc}')

    created, skipped, created_aggs, errors = 0, 0, [], []

    def resolve_aggregate(ref, net):
        if not ref:
            return None
        aggs = db.query(IPAMAggregate).all()
        matches = [a for a in aggs if a.name == ref or canonical(a.prefix) == canonical(ref)]
        if matches:
            agg = unique(matches, f'聚合「{ref}」')
            contains(network(agg.prefix), net)
            return agg.id
        try:
            agg_net = network(ref)
        except ValueError:
            raise ValueError(f'聚合「{ref}」不存在；请使用已有聚合名称或合法 CIDR')
        contains(agg_net, net)
        agg = IPAMAggregate(name=str(agg_net), prefix=str(agg_net), description='由明确的导入聚合列创建')
        db.add(agg); db.flush(); created_aggs.append(str(agg_net))
        return agg.id

    # Process parent prefixes before their children, independent of CSV row order.
    if kind == 'prefixes':
        pending.sort(key=lambda item: (item[2].version, item[2].prefixlen, int(item[2].network_address)))
    try:
        if db.get_bind().dialect.name == 'sqlite' and not db.connection().connection.driver_connection.in_transaction:
            from sqlalchemy import text
            db.execute(text('BEGIN IMMEDIATE'))
        if db.get_bind().dialect.name == 'postgresql':
            from sqlalchemy import text
            db.execute(text('SELECT pg_advisory_xact_lock(190951)'))
        for line, data, net in pending:
            try:
                if data.get('status') and data['status'] not in IPAM_STATUSES:
                    raise ValueError('无效状态：' + data['status'])
                if kind == 'aggregates':
                    existing = [a for a in db.query(IPAMAggregate).all() if canonical(a.prefix) == str(net)]
                    if existing:
                        unique(existing, '聚合 CIDR'); skipped += 1; continue
                    db.add(IPAMAggregate(name=data['name'], prefix=str(net), description=data.get('description',''), date_added=data.get('date_added','')))
                else:
                    agg_id = resolve_aggregate(data.get('aggregate',''), net)
                    matches = [p for p in db.query(IPAMPrefix).all() if canonical(p.prefix) == str(net)]
                    if data.get('aggregate'):
                        matches = [p for p in matches if p.aggregate_id == agg_id]
                    if kind == 'prefixes':
                        parent_id = None
                        if data.get('parent'):
                            parent_net = network(data['parent'])
                            contains(parent_net, net, strict=True)
                            candidates = [p for p in db.query(IPAMPrefix).all() if canonical(p.prefix) == str(parent_net)]
                            if data.get('aggregate'):
                                candidates = [p for p in candidates if p.aggregate_id == agg_id]
                            parent = unique(candidates, '父网段')
                            parent_id = parent.id
                            if not data.get('aggregate'):
                                agg_id = parent.aggregate_id
                        if matches:
                            existing = unique(matches, '网段 CIDR')
                            if data.get('aggregate') and existing.aggregate_id != agg_id or data.get('parent') and existing.parent_id != parent_id:
                                raise ValueError('已有网段的聚合或父网段关系与导入不一致；不覆盖现有关系')
                            skipped += 1; continue
                        values = {key:data.get(key,'') for key in ('role','vlan','company','firewall','zone_interface_name','description')}
                        db.add(IPAMPrefix(prefix=str(net), aggregate_id=agg_id, parent_id=parent_id, status=data.get('status') or '规划',
                                          is_pool=data.get('is_pool','').lower() in ('1','true','yes','是'), **values))
                    else:
                        address = ipaddress.ip_address(data['address'])
                        if address.version != net.version or address not in net:
                            raise ValueError('IP 不在所选网段中')
                        if address.version == 4 and net.prefixlen < 31 and address in (net.network_address,net.broadcast_address):
                            raise ValueError('不能分配网络地址或广播地址')
                        allocation = data.get('allocation_type') or '静态'
                        if allocation not in IPAM_ALLOCATION_TYPES:
                            raise ValueError('无效分配类型')
                        device_id = None
                        if data.get('device'):
                            if allocation == 'DHCP':
                                raise ValueError('DHCP 地址不能同时指定静态关联设备')
                            device = unique([d for d in db.query(Device).all() if d.name == data['device'] or d.ip_address == data['device']], '关联设备')
                            device_id = device.id
                        if matches:
                            prefix = unique(matches, '网段 CIDR（可填写聚合列消除歧义）')
                        else:
                            prefix = IPAMPrefix(prefix=str(net), aggregate_id=agg_id, status='规划', description='由导入创建')
                            db.add(prefix);db.flush()
                        existing = []
                        for row in db.query(IPAMIPAddress).filter_by(prefix_id=prefix.id).all():
                            try:
                                if ipaddress.ip_address(row.address) == address: existing.append(row)
                            except ValueError: pass
                        if existing:
                            row = unique(existing, '已有 IP')
                            if data.get('device') and row.assigned_device_id != device_id:
                                raise ValueError('已有 IP 设备占用与导入不一致，不覆盖')
                            skipped += 1; continue
                        values = {key:data.get(key,'') for key in ('dns_name','description','device_name','device_model','device_ip')}
                        db.add(IPAMIPAddress(prefix_id=prefix.id,address=str(address),status=data.get('status') or ('使用中' if device_id else '规划'),allocation_type=allocation,assigned_device_id=device_id,**values))
                db.flush();created += 1
            except ValueError as exc:
                errors.append(f'第 {line} 行：{exc}')
        if errors:
            db.rollback()
            raise HTTPException(400, '未导入任何记录；请修正以下关系后重试：\n'+'\n'.join(errors[:30]))
        db.commit()
        return {'created':created,'skipped':skipped,'errors':[],'created_aggregates':created_aggs}
    except Exception:
        db.rollback()
        raise


def audit_relations(db):
    issues=[]
    prefixes=db.query(IPAMPrefix).all();by_id={p.id:p for p in prefixes}
    groups={}
    for p in prefixes:
        groups.setdefault((p.aggregate_id,canonical(p.prefix)),[]).append(p.id)
        try:
            net=network(p.prefix)
            if p.aggregate_id:
                agg=db.get(IPAMAggregate,p.aggregate_id)
                if not agg: raise ValueError('聚合不存在')
                contains(network(agg.prefix),net)
            if p.parent_id:
                parent=by_id.get(p.parent_id)
                if not parent: raise ValueError('父网段不存在')
                contains(network(parent.prefix),net,True)
                if parent.aggregate_id!=p.aggregate_id: raise ValueError('父子网段属于不同聚合')
        except ValueError as exc:issues.append(f'网段 #{p.id} {p.prefix}：{exc}')
    for (_,cidr),ids in groups.items():
        if len(ids)>1:issues.append(f'同一聚合存在重复网段 {cidr}：ID {ids}')
    for ip in db.query(IPAMIPAddress).all():
        try:
            p=by_id.get(ip.prefix_id)
            if not p:raise ValueError('所属网段不存在')
            address=ipaddress.ip_address(ip.address);net=network(p.prefix)
            if address.version!=net.version or address not in net:raise ValueError('IP 不在所属网段内')
            if ip.assigned_device_id and ip.assigned_vm_id:raise ValueError('同时关联设备和虚拟机')
        except ValueError as exc:issues.append(f'IP #{ip.id} {ip.address}：{exc}')
    return {'issues':issues,'count':len(issues),'modified':False}
