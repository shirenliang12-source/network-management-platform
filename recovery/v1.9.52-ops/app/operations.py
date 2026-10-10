"""Operations extensions; additive persistence and permission-aware APIs."""
import json
import threading
import re
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import Column, Integer, ForeignKey, String, Text, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Session
from app.database import Base, get_db
from app.models import (Device, DeviceGroup, DCRack, DCSite, ServerAsset, ITAsset,
    TaskLog, SystemSetting, AuditLog, DataLog, IntegrationSyncRun, ScheduleConfig)


class DeviceRack(Base):
    __tablename__ = 'device_rack_locations'
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'), primary_key=True)
    rack_id = Column(Integer, ForeignKey('dc_racks.id'), nullable=False, index=True)
    position = Column(String(100), default='')
    u_start = Column(Integer, nullable=False, default=1)
    u_size = Column(Integer, nullable=False, default=1)


class BackupAlertState(Base):
    __tablename__ = 'backup_alert_states'
    task_id = Column(Integer, primary_key=True)
    item_index = Column(Integer, primary_key=True)
    state = Column(String(20), nullable=False, default='acknowledged')
    operator = Column(String(100), default='')
    updated_at = Column(DateTime, default=datetime.utcnow)


router = APIRouter()


@router.get('/api/devices/{device_id}/relations')
def device_relations(device_id: int,request: Request,db: Session=Depends(get_db)):
    from app.services.asset_relations import relations,request_modules
    return relations(db,'device',device_id,request_modules(request))


def require(request, *modules):
    user = getattr(request.state, 'user', None)
    if user is None:
        raise HTTPException(401, '请先登录')
    if not user.is_superuser and not set(modules) <= set(user.get_modules() or []):
        raise HTTPException(403, '需要模块权限：' + ', '.join(modules))
    return user


class LocationPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rack_id: int | None = Field(default=None, gt=0)
    position: str = Field(default='', max_length=100)
    u_start: int | None = Field(default=None, ge=1, le=60)
    u_size: int | None = Field(default=None, ge=1, le=60)


@router.get('/api/devices/rack-options')
def rack_options(request: Request, db: Session = Depends(get_db)):
    require(request, 'devices', 'dc')
    return [{'id':r.id, 'name':f'{s.name} / {r.rack_number}', 'u_height':r.u_height or 42,
             'site_name':s.name,'rack_number':r.rack_number} for r,s in
            db.query(DCRack,DCSite).join(DCSite,DCRack.site_id==DCSite.id).all()]


def _position_range(position):
    match=re.search(r'U\s*(\d+)(?:\s*[-–—~至]\s*U?\s*(\d+))?',position or '',re.I)
    if not match: return None
    start=int(match.group(1)); end=int(match.group(2) or start)
    return start,max(1,end-start+1)


def _overlap(start,size,other_start,other_size):
    return start <= other_start+other_size-1 and other_start <= start+size-1


def set_device_rack(db,device_id,rack_id,u_start=None,u_size=None,position=''):
    row=db.get(DeviceRack,device_id)
    if rack_id is None:
        if row: db.delete(row)
        return None
    rack=db.get(DCRack,rack_id)
    if not rack: raise HTTPException(404,'机柜不存在')
    parsed=_position_range(position)
    start=u_start or (parsed[0] if parsed else 1)
    size=u_size or (parsed[1] if parsed else 1)
    if start+size-1 > (rack.u_height or 42):
        raise HTTPException(409,f'U位超出机柜范围：U{start}–U{start+size-1}，机柜共{rack.u_height or 42}U')
    conflicts=[]
    for link,device in db.query(DeviceRack,Device).join(Device,Device.id==DeviceRack.device_id).filter(
            DeviceRack.rack_id==rack_id,DeviceRack.device_id!=device_id):
        if _overlap(start,size,link.u_start or 1,link.u_size or 1):
            conflicts.append(f'网络设备 {device.name} U{link.u_start}–U{(link.u_start or 1)+(link.u_size or 1)-1}')
    for server in db.query(ServerAsset).filter_by(rack_id=rack_id):
        if _overlap(start,size,server.u_start or 1,server.u_size or 1):
            conflicts.append(f'服务器/存储 {server.name} U{server.u_start}–U{(server.u_start or 1)+(server.u_size or 1)-1}')
    for asset in db.query(ITAsset).filter_by(rack_id=rack_id):
        parsed_asset=_position_range(asset.rack_position)
        if parsed_asset and _overlap(start,size,*parsed_asset):
            conflicts.append(f'机房设备 {asset.name} U{parsed_asset[0]}')
    if conflicts:
        raise HTTPException(409,'U位已被占用：'+'；'.join(conflicts[:10]))
    if row is None:
        row=DeviceRack(device_id=device_id); db.add(row)
    row.rack_id,row.u_start,row.u_size=rack_id,start,size
    row.position=f'U{start}' if size==1 else f'U{start}–U{start+size-1}'
    return row


@router.put('/api/devices/{device_id}/rack-location')
def set_location(device_id: int, payload: LocationPayload, request: Request, db: Session = Depends(get_db)):
    require(request,'devices','dc')
    if not db.get(Device,device_id):
        raise HTTPException(404,'设备不存在')
    row=db.get(DeviceRack,device_id)
    set_device_rack(db,device_id,payload.rack_id,payload.u_start,payload.u_size,payload.position.strip())
    db.commit()
    row=db.get(DeviceRack,device_id)
    return {'ok':True,'rack_id':row.rack_id if row else None,
            'u_start':row.u_start if row else None,'u_size':row.u_size if row else None}


@router.get('/api/devices/{device_id}/rack-location')
def get_location(device_id: int, request: Request, db: Session=Depends(get_db)):
    require(request,'devices','dc')
    if not db.get(Device,device_id): raise HTTPException(404,'设备不存在')
    row=db.get(DeviceRack,device_id)
    rack=db.get(DCRack,row.rack_id) if row else None
    site=db.get(DCSite,rack.site_id) if rack else None
    return {'rack_id':row.rack_id if row else None,'position':row.position if row else '',
            'u_start':row.u_start if row else None,'u_size':row.u_size if row else None,
            'rack_name':f'{site.name} / {rack.rack_number}' if rack and site else ''}


@router.get('/api/devices/rack-locations')
def list_device_locations(request: Request,db: Session=Depends(get_db)):
    require(request,'devices','dc')
    rows=db.query(DeviceRack,Device,DCRack,DCSite).join(Device,Device.id==DeviceRack.device_id).join(
        DCRack,DCRack.id==DeviceRack.rack_id).join(DCSite,DCSite.id==DCRack.site_id).all()
    return [{'device_id':device.id,'name':device.name,'ip_address':device.ip_address,
             'rack_id':link.rack_id,'rack_name':f'{site.name} / {rack.rack_number}',
             'u_start':link.u_start or 1,'u_size':link.u_size or 1,
             'category':'网络设备'} for link,device,rack,site in rows]


@router.get('/api/backups/filter-options')
def backup_filters(db: Session = Depends(get_db)):
    return {'companies':[r[0] for r in db.query(Device.company).distinct().order_by(Device.company) if r[0]],
            'groups':[{'id':r.id,'name':r.name} for r in db.query(DeviceGroup).order_by(DeviceGroup.name)],
            'statuses':[r[0] for r in db.query(Device.status).distinct() if r[0]]}


class GlobalRetention(BaseModel):
    model_config = ConfigDict(extra='forbid')
    keep: int | None = Field(default=None,ge=0,le=100000)


@router.get('/api/backups/retention/global')
def get_global_retention(db: Session = Depends(get_db)):
    row=db.get(SystemSetting,'backup_retention:global')
    return {'keep':json.loads(row.value)['keep'] if row else None}


@router.put('/api/backups/retention/global')
def set_global_retention(payload: GlobalRetention, request: Request, db: Session = Depends(get_db)):
    require(request,'backups')
    row=db.get(SystemSetting,'backup_retention:global')
    if payload.keep is None:
        if row: db.delete(row)
    else:
        if row is None:
            row=SystemSetting(key='backup_retention:global'); db.add(row)
        row.value=json.dumps({'keep':payload.keep})
    db.commit()
    return {'ok':True,'message':'已保存；设备独立设置优先，下次成功备份后清理，基线额外保留'}


def alert_item(db, task_id, index):
    task=db.get(TaskLog,task_id)
    errors=task.error_details if task and task.task_type=='backup' else []
    if not isinstance(errors,list) or not 0 <= index < len(errors):
        raise HTTPException(404,'告警不存在')
    if task.status in {'running','pending'}: raise HTTPException(409,'任务尚未完成')
    return task,errors[index]


class AlertKey(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id: int=Field(gt=0)
    index: int=Field(ge=0)


class AlertBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    items: list[AlertKey]=Field(min_length=1,max_length=2000)


def alert_keys(payload):
    return list(dict.fromkeys((item.task_id,item.index) for item in payload.items))


@router.post('/api/backups/alerts/batch-acknowledge')
def batch_acknowledge_alerts(payload:AlertBatch,request:Request,db:Session=Depends(get_db)):
    user=require(request,'backups'); keys=alert_keys(payload)
    for task_id,index in keys: alert_item(db,task_id,index)
    for task_id,index in keys:
        row=db.get(BackupAlertState,(task_id,index))
        if row and row.state=='deleted': raise HTTPException(409,'选择中包含已移除告警，请刷新')
        if row is None:
            row=BackupAlertState(task_id=task_id,item_index=index);db.add(row)
        row.state='acknowledged';row.operator=user.username;row.updated_at=datetime.utcnow()
    db.commit()
    return {'ok':True,'count':len(keys),'message':f'已确认 {len(keys)} 条告警'}


@router.post('/api/backups/alerts/batch-delete')
def batch_delete_alerts(payload:AlertBatch,request:Request,db:Session=Depends(get_db)):
    user=require(request,'backups');keys=alert_keys(payload);rows=[]
    for task_id,index in keys:
        alert_item(db,task_id,index)
        row=db.get(BackupAlertState,(task_id,index))
        if row is None or row.state!='acknowledged':
            raise HTTPException(409,'选择中包含未确认告警，请先批量确认；本次未删除')
        rows.append(row)
    for row in rows:
        row.state='deleted';row.operator=user.username;row.updated_at=datetime.utcnow()
    db.commit()
    return {'ok':True,'count':len(rows),'message':f'已从告警列表移除 {len(rows)} 条；原任务和审计保留'}


@router.get('/api/backups/alerts')
def backup_alerts(limit: int = Query(200,ge=1,le=1000), db: Session = Depends(get_db)):
    states={(s.task_id,s.item_index):s.state for s in db.query(BackupAlertState)}
    items=[]
    for task in db.query(TaskLog).filter(TaskLog.task_type=='backup',
            (TaskLog.failed_count>0) | TaskLog.status.in_(['failed','partial'])).order_by(TaskLog.id.desc()).limit(limit):
        errors=task.error_details if isinstance(task.error_details,list) else []
        for index,error in enumerate(errors):
            state=states.get((task.id,index),'open')
            if state=='deleted': continue
            info=error if isinstance(error,dict) else {'error':str(error)}
            items.append({'task_id':task.id,'index':index,'state':state,
                'time':task.started_at,'task_status':task.status,
                'device_id':info.get('device_id'),'device_name':info.get('device_name',info.get('device','')),
                'ip_address':info.get('ip_address',''),'stage':info.get('stage','历史记录未标记阶段'),
                'error':safe_message(info.get('error','未知错误'))})
            if len(items)>=2000: return items
    return items


@router.post('/api/backups/alerts/{task_id}/{index}/acknowledge')
def acknowledge_alert(task_id: int,index: int,request: Request,db: Session=Depends(get_db)):
    user=require(request,'backups')
    alert_item(db,task_id,index)
    row=db.get(BackupAlertState,(task_id,index))
    if row and row.state=='deleted': raise HTTPException(409,'告警已移除')
    if row is None:
        row=BackupAlertState(task_id=task_id,item_index=index); db.add(row)
    row.state='acknowledged'; row.operator=user.username; row.updated_at=datetime.utcnow()
    db.commit()
    return {'ok':True}


@router.delete('/api/backups/alerts/{task_id}/{index}')
def delete_alert(task_id: int,index: int,request: Request,db: Session=Depends(get_db)):
    user=require(request,'backups'); alert_item(db,task_id,index)
    row=db.get(BackupAlertState,(task_id,index))
    if row is None or row.state!='acknowledged': raise HTTPException(409,'请先确认告警')
    row.state='deleted'; row.operator=user.username; row.updated_at=datetime.utcnow()
    db.commit()
    return {'ok':True,'message':'告警已移除，原任务与审计历史保留'}


@router.delete('/api/integrations/sources/{source}')
def delete_source(source: str,request: Request,db: Session=Depends(get_db)):
    require(request,'integrations')
    from app.services.integration_sources import config_key
    from app.services.integration_sync_service import _SYNC_LOCKS
    try: key=config_key(source)
    except ValueError: raise HTTPException(422,'无效来源')
    lock=_SYNC_LOCKS.setdefault(source,threading.Lock())
    if not lock.acquire(blocking=False): raise HTTPException(409,'来源正在同步，请稍后重试')
    try:
        row=db.get(SystemSetting,key)
        if row is None: raise HTTPException(404,'来源不存在')
        if db.query(IntegrationSyncRun).filter_by(source_type=source,status='running').first():
            raise HTTPException(409,'来源存在未结束同步任务，请先核实任务状态')
        job_ids=[]
        for schedule in db.query(ScheduleConfig).filter_by(task_type=source+'_sync'):
            schedule.is_enabled=False
            job_ids.append(f'sched_{schedule.id}')
        marker=db.get(SystemSetting,'removed_'+key)
        if marker is None:
            marker=SystemSetting(key='removed_'+key); db.add(marker)
        marker.value=datetime.utcnow().isoformat()
        db.delete(row); db.commit()
        from app.scheduler.tasks import scheduler
        from apscheduler.jobstores.base import JobLookupError
        for job in scheduler.get_jobs():
            if job.id in job_ids:
                try: scheduler.remove_job(job.id)
                except JobLookupError: pass
        return {'ok':True,'message':'连接已删除，定时同步已禁用；已导入资产和历史保留，不操作远端主机'}
    finally: lock.release()


@router.get('/api/logs/module-summary')
def module_summary(days: int=Query(7,ge=1,le=90),db: Session=Depends(get_db)):
    from app.auth import MODULES, module_for_path
    cutoff=datetime.utcnow()-timedelta(days=days)
    result={key:{'module':key,'label':label,'events':0,'errors':0,'warnings':0,'latest':None,
                 'sources':set()} for key,label,_ in MODULES}
    def add(module,count,errors,warnings,latest,source):
        if module not in result: module='logs'
        row=result[module]; row['events']+=count; row['errors']+=errors; row['warnings']+=warnings
        row['sources'].add(source)
        if latest and (not row['latest'] or latest>row['latest']): row['latest']=latest
    for path,success,count,latest in db.query(AuditLog.path,AuditLog.success,func.count(),func.max(AuditLog.timestamp)).filter(AuditLog.timestamp>=cutoff).group_by(AuditLog.path,AuditLog.success):
        add(module_for_path(path),count,0 if success else count,0,latest,'审计')
    task_map={'backup':'backups','discovery':'devices','info':'devices','topology':'topology'}
    for kind,status,count,latest in db.query(TaskLog.task_type,TaskLog.status,func.count(),func.max(TaskLog.started_at)).filter(TaskLog.started_at>=cutoff).group_by(TaskLog.task_type,TaskLog.status):
        add(task_map.get(kind,'schedule'),count,count if status in {'failed','partial'} else 0,0,latest,'任务')
    for category,level,count,latest in db.query(DataLog.category,DataLog.level,func.count(),func.max(DataLog.timestamp)).filter(DataLog.timestamp>=cutoff).group_by(DataLog.category,DataLog.level):
        module={'ssh':'devices','info':'devices','cpu':'devices','memory':'devices','interface':'devices','backup':'backups','dhcp':'integrations'}.get(category,'logs')
        add(module,count,count if level=='ERROR' else 0,count if level in {'WARN','WARNING'} else 0,latest,'诊断')
    for status,count,latest in db.query(IntegrationSyncRun.status,func.count(),func.max(IntegrationSyncRun.started_at)).filter(IntegrationSyncRun.started_at>=cutoff).group_by(IntegrationSyncRun.status):
        add('integrations',count,count if status in {'failed','partial'} else 0,0,latest,'集成同步')
    for row in result.values():
        row['sources']=sorted(row['sources'])
        row['analysis']='存在失败，请按来源查看明细' if row['errors'] else ('存在警告，请核查' if row['warnings'] else ('未记录异常（不代表实时健康）' if row['events'] else '暂无日志，不能判定健康'))
    return {'days':days,'modules':list(result.values()),'note':'按日志记录计数，同一故障可能同时产生任务、诊断和审计记录；不是独立故障数量。'}


def safe_message(value):
    text=str(value or '')[:8000]
    text=re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----','[私钥已隐藏]',text)
    text=re.sub(r'(?i)((?:password|passwd|secret|token|community|authorization)\s*[:=]\s*)([^\s,;]+)',r'\1[已隐藏]',text)
    text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[已隐藏]@',text)
    return text


@router.get('/api/logs/module-events')
def module_events(module: str,days: int=Query(7,ge=1,le=90),db: Session=Depends(get_db)):
    from app.auth import MODULE_KEYS, module_for_path
    if module not in MODULE_KEYS: raise HTTPException(422,'未知模块')
    cutoff=datetime.utcnow()-timedelta(days=days)
    rows=[]
    def add(time,source,status,message):
        rows.append({'time':time,'source':source,'status':status,'message':safe_message(message)})
    paths=[p for p, in db.query(AuditLog.path).filter(AuditLog.timestamp>=cutoff).distinct()
           if (module_for_path(p) or 'logs')==module]
    for row in db.query(AuditLog).filter(AuditLog.timestamp>=cutoff,AuditLog.path.in_(paths)).order_by(AuditLog.timestamp.desc(),AuditLog.id.desc()).limit(200):
        add(row.timestamp,'审计',str(row.status_code),f'{row.username} · {row.method} {row.path} · {row.action}')
    task_map={'backup':'backups','discovery':'devices','info':'devices','topology':'topology'}
    kinds=[k for k, in db.query(TaskLog.task_type).distinct() if task_map.get(k,'schedule')==module]
    for row in db.query(TaskLog).filter(TaskLog.started_at>=cutoff,TaskLog.task_type.in_(kinds)).order_by(TaskLog.started_at.desc(),TaskLog.id.desc()).limit(200):
        errors=row.error_details if isinstance(row.error_details,list) else []
        detail='\n'.join(str(e) for e in errors[:20])
        add(row.started_at,'任务',row.status,f'任务 #{row.id} · {row.task_type} · 成功 {row.success_count} / 失败 {row.failed_count}\n{row.summary}\n{detail}')
    category_map={'ssh':'devices','info':'devices','cpu':'devices','memory':'devices','interface':'devices','backup':'backups','dhcp':'integrations'}
    categories=[c for c, in db.query(DataLog.category).distinct() if category_map.get(c,'logs')==module]
    for row in db.query(DataLog).filter(DataLog.timestamp>=cutoff,DataLog.category.in_(categories)).order_by(DataLog.timestamp.desc(),DataLog.id.desc()).limit(200):
        add(row.timestamp,'诊断',row.level,f'#{row.id} {row.device_name} {row.device_ip} · {row.category} · {row.action}\n{row.message}\n{row.error}')
    if module=='integrations':
        for row in db.query(IntegrationSyncRun).filter(IntegrationSyncRun.started_at>=cutoff).order_by(IntegrationSyncRun.started_at.desc(),IntegrationSyncRun.id.desc()).limit(200):
            add(row.started_at,'集成同步',row.status,f'同步 #{row.id} · {row.source_type} · {row.mode}\n新增 {row.created_count} / 更新 {row.updated_count} / 不变 {row.unchanged_count} / 缺失 {row.stale_count}\n{row.error_message}')
    return sorted(rows,key=lambda r:r['time'] or datetime.min,reverse=True)
