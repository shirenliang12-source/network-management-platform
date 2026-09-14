"""Shared DHCP sources and explicit IPAM bindings; legacy inventory stays intact."""
import ipaddress
import json
import re
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from app.models import SystemSetting, IPInventory, IPAMPrefix
from app.services import windows_dhcp
from app.services.dhcp_credentials import public, merge, collect_config


def source_key(source_id):
    if re.fullmatch(r'pool-[a-f0-9]{32}', source_id):
        return 'dhcp_pool:' + source_id[5:]
    if re.fullmatch(r'inventory-[1-9][0-9]*', source_id):
        return 'dhcp_scope:' + source_id[10:]
    raise HTTPException(404, 'DHCP 地址池不存在')


def get_source(db, source_id):
    row = db.get(SystemSetting, source_key(source_id))
    if not row or (source_id.startswith('inventory-') and not db.get(IPInventory, int(source_id[10:]))):
        raise HTTPException(404, 'DHCP 地址池不存在')
    return json.loads(row.value)


def sources(db):
    result = []
    inventory_ids = {r.id for r in db.query(IPInventory).all()}
    for row in db.query(SystemSetting).filter((SystemSetting.key.startswith('dhcp_pool:')) | (SystemSetting.key.startswith('dhcp_scope:'))).all():
        if row.key.startswith('dhcp_pool:'):
            source_id = 'pool-' + row.key.split(':')[1]
        else:
            if not row.key.split(':')[1].isdigit() or int(row.key.split(':')[1]) not in inventory_ids:
                continue
            source_id = 'inventory-' + row.key.split(':')[1]
        data = json.loads(row.value)
        if not data.get('server') or not data.get('scope'):
            continue
        result.append({'id': source_id, **public(data), 'legacy': source_id.startswith('inventory-')})
    return result


def linked_prefixes(db, source_id):
    return [row for row in db.query(SystemSetting).filter(SystemSetting.key.startswith('ipam_dhcp:')).all()
            if json.loads(row.value).get('source_id') == source_id]


def identity(config):
    return (config.get('provider','windows'), config.get('server','').strip().casefold(),
            config.get('api_port',443),config.get('vdom',''),config.get('interface',''),config.get('scope',''),config.get('netmask',''))


def protect_source_change(db, source_id, previous, updated):
    if identity(previous) != identity(updated) and linked_prefixes(db, source_id):
        raise HTTPException(409, '地址池已关联 IP 规划；请先解除网段关联，再更换服务器或作用域')


def save_source(db, data, source_id=None):
    if any(s['id'] != source_id and identity(s)[:-1] == identity(data)[:-1] for s in sources(db)):
        raise HTTPException(409, '该服务器和作用域已存在，请维护已有配置，避免重复同步')
    if source_id is None:
        source_id = 'pool-' + uuid.uuid4().hex
        previous = {}
    else:
        previous = get_source(db, source_id)
        if source_id.startswith('inventory-') and data.get('provider','windows') != 'windows':
            raise HTTPException(409, '原 IP 清单 Windows 来源不能改为防火墙，请新增独立来源')
        protect_source_change(db, source_id, previous, data)
    credentials = merge(previous, data)
    updated = {k:v for k,v in credentials.items() if k not in {'snapshot','last_attempt','error'}}
    if identity(previous) == identity(data):
        updated = {**previous, **updated}
        if updated.get('snapshot'):
            updated['snapshot']['warning'] = updated['snapshot']['percent'] >= updated['threshold']
    row = db.get(SystemSetting, source_key(source_id))
    if not row:
        row = SystemSetting(key=source_key(source_id))
        db.add(row)
    row.value = json.dumps(updated, ensure_ascii=False)
    db.commit()
    return {'id': source_id, **public(updated)}


def sync_source(db, source_id):
    config = get_source(db, source_id)
    if source_id.startswith('inventory-'):
        return windows_dhcp.sync(db, int(source_id[10:]))
    if config.get('mode') != 'DHCP':
        raise ValueError('地址池已停用，请先启用 DHCP')
    if not windows_dhcp._lock.acquire(blocking=False):
        raise ValueError('已有 DHCP 同步执行中，请稍后重试')
    try:
        before = dict(config)
        original_value = db.get(SystemSetting, source_key(source_id)).value
        config['last_attempt'] = datetime.now(timezone.utc).isoformat()
        try:
            snapshot = collect_config(config)
            network = ipaddress.ip_network(f"{snapshot['scope']}/{snapshot['mask']}", strict=False)
            if str(network.network_address) != config['scope']:
                raise ValueError('服务器返回的作用域网络地址不匹配')
            start, end = ipaddress.ip_address(snapshot['start']), ipaddress.ip_address(snapshot['end'])
            if start not in network or end not in network or start > end:
                raise ValueError('服务器返回的地址池范围无效')
            snapshot.update(warning=snapshot['percent'] >= config['threshold'], synced_at=datetime.now(timezone.utc).isoformat())
            config.update(snapshot=snapshot, error='')
        except Exception as exc:
            config['error'] = str(exc) if isinstance(exc, ValueError) else 'DHCP 同步失败或超时，保留上次成功快照'
        db.rollback()
        current = get_source(db, source_id)
        if identity(current) != identity(before) or any(current.get(k) != before.get(k) for k in ('mode', 'threshold', 'interval', 'auth_mode', 'username', 'password_enc','api_token_enc','verify_ssl')):
            raise ValueError('同步期间配置已变化，请重新同步')
        # Optimistic compare-and-swap also protects edits arriving after re-read.
        changed = db.query(SystemSetting).filter(SystemSetting.key == source_key(source_id), SystemSetting.value == original_value).update(
            {'value': json.dumps(config, ensure_ascii=False)}, synchronize_session=False)
        if changed != 1:
            db.rollback()
            raise ValueError('同步期间配置已变化，结果已丢弃')
        db.commit()
        if config.get('error'):
            raise ValueError(config['error'])
        return config
    finally:
        windows_dhcp._lock.release()


def bind_prefix(db, prefix_id, source_id):
    prefix = db.get(IPAMPrefix, prefix_id)
    if not prefix:
        raise HTTPException(404, '规划网段不存在')
    row = db.get(SystemSetting, f'ipam_dhcp:{prefix_id}')
    if source_id is None:
        if row:
            db.delete(row)
        db.commit()
        return {}
    config = get_source(db, source_id)
    snapshot = config.get('snapshot')
    if config.get('mode') != 'DHCP' or not snapshot or config.get('error'):
        raise HTTPException(409, '请先在平台集成中启用地址池并成功同步，再关联规划网段')
    actual = ipaddress.ip_network(f"{snapshot['scope']}/{snapshot['mask']}", strict=False)
    if actual != ipaddress.ip_network(prefix.prefix, strict=False):
        raise HTTPException(409, '地址池的作用域 CIDR 必须与规划网段完全一致')
    if not row:
        row = SystemSetting(key=f'ipam_dhcp:{prefix_id}')
        db.add(row)
    row.value = json.dumps({'source_id': source_id, 'prefix': prefix.prefix})
    db.commit()
    return prefix_summary(db, prefix_id)


def prefix_summary(db, prefix_id):
    link = db.get(SystemSetting, f'ipam_dhcp:{prefix_id}')
    if not link:
        return {}
    source_id = json.loads(link.value)['source_id']
    try:
        config = get_source(db, source_id)
    except HTTPException:
        return {'source_id': source_id, 'error': '关联地址池已不存在，请重新关联'}
    prefix = db.get(IPAMPrefix, prefix_id)
    snapshot = config.get('snapshot')
    result = {'source_id': source_id, 'name': config.get('name') or config.get('scope'),
              'mode': config.get('mode'), 'threshold': config.get('threshold', 80),
              'error': config.get('error', ''), 'snapshot': snapshot}
    if snapshot and prefix:
        actual = ipaddress.ip_network(f"{snapshot['scope']}/{snapshot['mask']}", strict=False)
        if actual != ipaddress.ip_network(prefix.prefix, strict=False):
            result.update(error='DHCP 作用域与规划网段不再匹配，请重新关联', snapshot=None)
    return result


def sync_due(db):
    for source in sources(db):
        if source['legacy'] or source.get('mode') != 'DHCP' or not source.get('interval'):
            continue
        last = source.get('last_attempt')
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < source['interval'] * 60:
            continue
        try:
            sync_source(db, source['id'])
        except (ValueError, HTTPException):
            pass  # Display errors alongside the last successful snapshot.
