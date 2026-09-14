"""Explicit allocation; read-only discovery never changes ownership."""
from fastapi import HTTPException
from sqlalchemy import text
from app.models import IPAMIPAddress, IPAMPrefix, VMInstance
from app.services.asset_relations import _address, _network, _vm_ips
from app.services.device_ip_link import build_ip_to_device_map


def allocate(db, vm_id, prefix_id, address, release=False):
    # Serialize read/check/write on SQLite, including creation of a missing row.
    if db.get_bind().dialect.name == 'sqlite':
        connection = db.connection().connection.driver_connection
        if not connection.in_transaction:
            db.execute(text('BEGIN IMMEDIATE'))
        else:
            db.execute(text("UPDATE system_settings SET value=value WHERE key='__ip_allocation_lock__'"))
    try:
        vm, prefix = db.get(VMInstance, vm_id), db.get(IPAMPrefix, prefix_id)
        ip = _address(address)
        net = _network(prefix.prefix) if prefix else None
        if not vm or not prefix:
            raise HTTPException(404, '虚拟机或网段不存在')
        if not ip or not net or ip.version != net.version or ip not in net:
            raise HTTPException(400, '地址不在所选网段中')
        rows = [r for r in db.query(IPAMIPAddress).all() if _address(r.address) == ip]
        local = [r for r in rows if r.prefix_id == prefix_id]
        if len(local) > 1:
            raise HTTPException(409, '同一网段存在重复地址记录，请先处理')
        row = local[0] if local else None
        if release:
            if not row or row.assigned_vm_id != vm_id:
                raise HTTPException(409, '占用关系已变化，请刷新后重试')
            row.assigned_vm_id = None
            row.status = '规划'
        else:
            if str(ip) not in _vm_ips(vm):
                raise HTTPException(409, '虚拟机当前已不包含此 IP，请刷新反查结果')
            if ip.version == 4 and net.prefixlen < 31 and ip in (net.network_address, net.broadcast_address):
                raise HTTPException(400, '不能占用网络地址或广播地址')
            if row and row.assigned_vm_id == vm_id:
                db.rollback()
                return {'ok': True, 'id': row.id, 'message': '已由该虚拟机占用'}
            if any(r is not row and (r.assigned_vm_id or r.assigned_device_id or r.status != '规划') for r in rows):
                raise HTTPException(409, '其他规划网段已登记该地址占用，请先确认隔离网络或冲突')
            if row and (row.assigned_vm_id or row.assigned_device_id or row.device_name or row.status != '规划' or row.allocation_type == 'DHCP'):
                raise HTTPException(409, '地址已分配、预留、停用或属于 DHCP，不能覆盖')
            if str(ip) in build_ip_to_device_map(db):
                raise HTTPException(409, '已发现网络设备使用此 IP，不能覆盖')
            if any(v.id != vm_id and str(ip) in _vm_ips(v) for v in db.query(VMInstance).all()):
                raise HTTPException(409, '多台虚拟机具有相同 IP，请先确认并处理冲突')
            if not row:
                row = IPAMIPAddress(prefix_id=prefix_id, address=str(ip), allocation_type='静态')
                db.add(row)
            row.assigned_vm_id = vm_id
            row.status = '使用中'
        db.commit()
        return {'ok': True, 'id': row.id, 'message': '已释放占用' if release else '已登记占用'}
    except Exception:
        db.rollback()
        raise


def guard_vm_delete(db, ids):
    if db.query(IPAMIPAddress).filter(IPAMIPAddress.assigned_vm_id.in_(ids)).first():
        raise HTTPException(409, '虚拟机仍有手动占用的 IP，请先在关联反查中释放占用')
