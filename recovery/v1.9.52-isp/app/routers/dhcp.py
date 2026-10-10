"""DHCP platform integration and IPAM associations."""
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from pydantic import Field, model_validator
from typing import Literal
import ipaddress
import re
from app.database import get_db
from app.models import SystemSetting, IPAMPrefix
from app.api_models import StrictRequest
from app.routers.ip_inventory import DHCPConfigRequest
from app.services import dhcp_integration as service

router = APIRouter(prefix='/api/integrations/dhcp', tags=['integrations'])
ipam_router = APIRouter(prefix='/api/ipam/prefixes', tags=['ipam'])


def allowed(request, *modules, admin=False):
    user = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(401, '请先登录')
    if not user.is_superuser and (admin or not set(modules) <= set(user.get_modules() or [])):
        raise HTTPException(403, '无权执行此操作')


class SourceRequest(DHCPConfigRequest):
    name: str = Field(default='', max_length=100)
    provider: Literal['windows','fortinet','paloalto'] = 'windows'
    api_port: int = Field(default=443, ge=1, le=65535)
    verify_ssl: bool = True
    vdom: str = Field(default='', max_length=79)
    interface: str = Field(default='', max_length=100)
    netmask: str = Field(default='', max_length=15)
    api_token: str | None = Field(default=None, max_length=4096)

    @model_validator(mode='after')
    def validate_provider(self):
        if self.provider != 'windows':
            if not re.fullmatch(r'[A-Za-z0-9_.:/-]+', self.interface):
                raise ValueError('防火墙 DHCP 需填写接口名')
            if self.provider == 'fortinet' and not re.fullmatch(r'[A-Za-z0-9_.-]+', self.vdom):
                raise ValueError('Fortinet 需指定单个 VDOM，多个 VDOM 请分别添加来源')
            if self.provider == 'paloalto':
                if self.vdom:
                    raise ValueError('PA 当前仅支持单虚拟系统，不填写 VDOM')
                net = ipaddress.IPv4Network(f'{self.scope}/{self.netmask}', strict=True)
                self.netmask = str(net.netmask)
            else:
                self.netmask = ''
        else:
            self.vdom = self.interface = self.netmask = ''
            self.api_port = 443
        return self


class BindingRequest(StrictRequest):
    source_id: str | None = None


@router.get('')
def list_sources(request: Request, db: Session = Depends(get_db)):
    allowed(request, 'integrations')
    return service.sources(db)


@router.post('')
def create_source(payload: SourceRequest, request: Request, db: Session = Depends(get_db)):
    allowed(request, admin=True)
    if not payload.server or not payload.scope:
        raise HTTPException(422, '请填写 DHCP 服务器和作用域网络地址')
    return service.save_source(db, payload.model_dump())


@router.put('/{source_id}')
def update_source(source_id: str, payload: SourceRequest, request: Request, db: Session = Depends(get_db)):
    allowed(request, admin=True)
    if not payload.server or not payload.scope:
        raise HTTPException(422, '请填写 DHCP 服务器和作用域网络地址')
    return service.save_source(db, payload.model_dump(), source_id)


@router.post('/{source_id}/sync')
def sync_source(source_id: str, request: Request, db: Session = Depends(get_db)):
    allowed(request, 'integrations')
    try:
        from app.services.dhcp_credentials import public
        return public(service.sync_source(db, source_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete('/{source_id}')
def delete_source(source_id: str, request: Request, db: Session = Depends(get_db)):
    allowed(request, admin=True)
    service.get_source(db, source_id)
    if service.linked_prefixes(db, source_id):
        raise HTTPException(409, '仍有关联的规划网段，请先解除关联')
    if source_id.startswith('inventory-'):
        raise HTTPException(409, '这是兼容的 IP 清单配置，请在 IP 清单中维护或停用，不在此删除')
    db.delete(db.get(SystemSetting, service.source_key(source_id)))
    db.commit()
    return {'ok': True}


@ipam_router.get('/{prefix_id}/dhcp')
def get_prefix_dhcp(prefix_id: int, request: Request, db: Session = Depends(get_db)):
    allowed(request, 'ipam')
    if not db.get(IPAMPrefix, prefix_id):
        raise HTTPException(404, '网段不存在')
    return service.prefix_summary(db, prefix_id)


@ipam_router.put('/{prefix_id}/dhcp')
def set_prefix_dhcp(prefix_id: int, payload: BindingRequest, request: Request, db: Session = Depends(get_db)):
    allowed(request, 'ipam', 'integrations')
    return service.bind_prefix(db, prefix_id, payload.source_id)


@ipam_router.post('/{prefix_id}/dhcp/sync')
def sync_prefix_dhcp(prefix_id: int, request: Request, db: Session = Depends(get_db)):
    allowed(request, 'ipam', 'integrations')
    if not db.get(IPAMPrefix, prefix_id):
        raise HTTPException(404, '网段不存在')
    summary = service.prefix_summary(db, prefix_id)
    if not summary:
        raise HTTPException(409, '请先关联平台集成中的 DHCP 地址池')
    try:
        service.sync_source(db, summary['source_id'])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return service.prefix_summary(db, prefix_id)
