"""Atomic server inventory CSV with conservative identity matching."""
import csv
import io
import json
from fastapi import HTTPException
from sqlalchemy import text
from app.models import ServerAsset, ServerIP, DCRack, DCSite
from app.schemas import ServerAssetCreate
from app.services.csv_inventory_import import read_rows, normalized, address, stored_address

FIELDS = ['name', 'category', 'brand', 'model', 'serial', 'asset_tag', 'site', 'rack',
          'u_start', 'u_size', 'status', 'management_ip', 'additional_ips', 'os', 'cpu',
          'memory', 'storage_desc', 'owner', 'notes']


def export_csv(db, template=False):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    if not template:
        for server in db.query(ServerAsset).order_by(ServerAsset.id):
            rack = db.get(DCRack, server.rack_id)
            site = db.get(DCSite, rack.site_id) if rack else None
            values = {k: getattr(server, k, '') or '' for k in FIELDS}
            values.update(site=site.name if site else '', rack=rack.rack_number if rack else '',
                          additional_ips=json.dumps([r.ip_address for r in server.extra_ips], ensure_ascii=False))
            # Spreadsheet formula neutralization; import reverses our prefix.
            writer.writerow({k: "'" + str(v) if str(v).startswith(('=', '+', '-', '@', "'")) else v
                             for k, v in values.items()})
    return '\ufeff' + output.getvalue()


def import_csv(db, raw):
    from app.routers.assets import _validate_server, ASSET_STATUSES
    rows = read_rows(raw, 'servers')
    duplicates = []
    created = 0
    try:
        if db.get_bind().dialect.name == 'sqlite' and not db.connection().connection.driver_connection.in_transaction:
            db.execute(text('BEGIN IMMEDIATE'))
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(190951)'))
        identities = {}

        def keys(values, ips):
            return [(k, normalized(values.get(k))) for k in ('name', 'serial', 'asset_tag') if values.get(k)] + [('ip', stored_address(ip)) for ip in ips if ip]

        def index(server):
            for key in keys({k: getattr(server, k) for k in ('name', 'serial', 'asset_tag')},
                            [server.management_ip] + [r.ip_address for r in server.extra_ips]):
                identities.setdefault(key, set()).add(server.id)

        for server in db.query(ServerAsset).all():
            index(server)
        for line, values in rows:
            if set(values) - set(FIELDS):
                raise ValueError(f'第 {line} 行存在未知表头，请下载模板')
            values = {k: v[1:] if v.startswith("'") and len(v) > 1 and v[1] in "=+-@'" else v for k, v in values.items()}
            if not values.get('name'):
                raise ValueError(f'第 {line} 行名称为空')
            racks = db.query(DCRack).join(DCSite).filter(DCSite.name == values.pop('site', ''), DCRack.rack_number == values.pop('rack', '')).all()
            if len(racks) != 1:
                raise ValueError(f'第 {line} 行站点和机柜必须唯一匹配已有记录')
            extras = json.loads(values.pop('additional_ips', '') or '[]')
            if not isinstance(extras, list) or any(not isinstance(v, str) for v in extras):
                raise ValueError(f'第 {line} 行 additional_ips 必须为 JSON 字符串数组')
            values['management_ip'] = address(values.get('management_ip'))
            extras = list(dict.fromkeys(address(v) for v in extras if v.strip()))
            extras = [v for v in extras if v != values['management_ip']]
            payload = ServerAssetCreate(**values, rack_id=racks[0].id, additional_ips=extras)
            if payload.status not in ASSET_STATUSES:
                raise ValueError(f'第 {line} 行状态无效')
            for field, value in payload.model_dump(exclude={'additional_ips'}).items():
                length = getattr(ServerAsset.__table__.c[field].type, 'length', None)
                if length and isinstance(value, str) and len(value) > length:
                    raise ValueError(f'第 {line} 行 {field} 超过 {length} 字符')
            matches = set().union(*(identities.get(k, set()) for k in keys(values, [payload.management_ip] + extras)))
            if matches:
                duplicates.append({'line': line, 'existing_ids': sorted(matches), 'reason': '名称、序列号、资产编号或 IP 重复/冲突，未覆盖'})
                continue
            _validate_server(db, payload.rack_id, payload.u_start, payload.u_size, None)
            server = ServerAsset(**payload.model_dump(exclude={'additional_ips'}))
            db.add(server); db.flush()
            for ip in extras:
                db.add(ServerIP(server_id=server.id, ip_address=ip))
            db.flush(); index(server)
            created += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc) if isinstance(exc, (ValueError, TypeError)) else '数据库写入失败'
        raise HTTPException(400, f'整批未导入：{detail}') from None
    return {'created': created, 'skipped': len(duplicates), 'duplicates': duplicates[:200],
            'details_truncated': len(duplicates) > 200, 'message': f'新增 {created} 条，重复/冲突跳过 {len(duplicates)} 条'}
