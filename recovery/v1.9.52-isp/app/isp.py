"""ISP circuit inventory. No scans, IPAM writes or host-range enumeration."""
import hashlib
import ipaddress
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.models import IPAMIPAddress


class ISPLine(Base):
    __tablename__ = 'isp_lines'
    id = Column(Integer, primary_key=True)
    identity_key = Column(String(64), nullable=False, unique=True)
    provider = Column(String(120), nullable=False)
    circuit_number = Column(String(120), nullable=False)
    networks_json = Column(Text, nullable=False)
    used_json = Column(Text, nullable=False)
    reserved_json = Column(Text, nullable=False)
    location = Column(String(500), nullable=False, default='')
    notes = Column(Text, nullable=False, default='')


def bounds(network):
    start, end = int(network.network_address), int(network.broadcast_address)
    if network.version == 4 and network.prefixlen < 31:
        return start + 1, end - 1
    if network.version == 6 and network.prefixlen < 127:
        return start + 1, end
    return start, end


class LinePayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str = Field(min_length=1, max_length=120)
    circuit_number: str = Field(min_length=1, max_length=120)
    networks: list[str] = Field(min_length=1, max_length=128)
    used_ips: list[str] = Field(default_factory=list, max_length=5000)
    reserved_ips: list[str] = Field(default_factory=list, max_length=5000)
    location: str = Field(default='', max_length=500)
    notes: str = Field(default='', max_length=5000)

    @field_validator('provider', 'circuit_number', 'location', 'notes', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def validate_addresses(self):
        try:
            nets = list(dict.fromkeys(ipaddress.ip_network(value.strip(), strict=True) for value in self.networks))
            for index, net in enumerate(nets):
                if any(net.version == other.version and net.overlaps(other) for other in nets[index+1:]):
                    raise ValueError('同一线路网段不能重叠')
            self.networks = [str(net) for net in nets]
            for field in ('used_ips', 'reserved_ips'):
                addresses = list(dict.fromkeys(ipaddress.ip_address(value.strip()) for value in getattr(self, field)))
                for address in addresses:
                    if not any(address.version == net.version and bounds(net)[0] <= int(address) <= bounds(net)[1] for net in nets):
                        raise ValueError('使用或预留IP必须位于线路的可分配地址范围内')
                setattr(self, field, [str(address) for address in addresses])
            if set(self.used_ips) & set(self.reserved_ips):
                raise ValueError('同一IP不能同时标记已用和预留')
        except ValueError as exc:
            raise ValueError('网段/IP填写错误：' + str(exc)) from exc
        return self


def ipam_snapshot(db, request):
    user = getattr(request.state, 'user', None)
    if not user or not (user.is_superuser or 'ipam' in (user.get_modules() or [])):
        return None
    entries = []
    for record in db.query(IPAMIPAddress.address, IPAMIPAddress.status).filter(
            IPAMIPAddress.status.in_(['使用中', '预分配'])).all():
        try:
            address = ipaddress.ip_interface(record.address.strip()).ip
        except (ValueError, AttributeError):
            continue
        entries.append((address, record.status))
    return entries


def representation(row, snapshot=None):
    nets = json.loads(row.networks_json)
    used, reserved = json.loads(row.used_json), json.loads(row.reserved_json)
    networks = [ipaddress.ip_network(n) for n in nets]
    auto_used, auto_reserved = set(), set()
    for address, status in snapshot or []:
        if any(address.version == net.version and bounds(net)[0] <= int(address) <= bounds(net)[1] for net in networks):
            (auto_used if status == '使用中' else auto_reserved).add(str(address))
    auto_reserved -= auto_used
    effective_used = set(used) | auto_used
    effective_reserved = (set(reserved) | auto_reserved) - effective_used
    total = sum(bounds(ipaddress.ip_network(n))[1] - bounds(ipaddress.ip_network(n))[0] + 1 for n in nets)
    # Decimal strings avoid JavaScript precision loss for IPv6 counts.
    return dict(id=row.id, provider=row.provider, circuit_number=row.circuit_number,
                networks=nets, used_ips=used, reserved_ips=reserved,
                location=row.location, notes=row.notes, usable_count=str(total),
                ipam_linked=snapshot is not None,
                ipam_used_ips=sorted(auto_used), ipam_reserved_ips=sorted(auto_reserved),
                used_count=len(effective_used), reserved_count=len(effective_reserved),
                available_count=str(total-len(effective_used)-len(effective_reserved)),
                utilization=round(len(effective_used)*100/total, 2))


router = APIRouter(prefix='/api/isp', tags=['ISP线路'])


@router.get('')
def list_lines(request: Request, q: str = Query('', max_length=200), db: Session = Depends(get_db)):
    query = db.query(ISPLine)
    if q.strip():
        query = query.filter(ISPLine.provider.contains(q.strip(), autoescape=True) |
                             ISPLine.circuit_number.contains(q.strip(), autoescape=True) |
                             ISPLine.location.contains(q.strip(), autoescape=True))
    snapshot = ipam_snapshot(db, request)
    return [representation(row, snapshot) for row in query.order_by(ISPLine.id.desc()).all()]


def save_line(row, payload, db, request):
    row.provider, row.circuit_number = payload.provider, payload.circuit_number
    row.identity_key = hashlib.sha256(json.dumps(
        [payload.provider.casefold(), payload.circuit_number.casefold()], ensure_ascii=False).encode()).hexdigest()
    row.networks_json = json.dumps(payload.networks)
    row.used_json, row.reserved_json = json.dumps(payload.used_ips), json.dumps(payload.reserved_ips)
    row.location, row.notes = payload.location, payload.notes
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '同一运营商下的线路编号已存在')
    db.refresh(row)
    return representation(row, ipam_snapshot(db, request))


@router.post('', status_code=201)
def create_line(payload: LinePayload, request: Request, db: Session = Depends(get_db)):
    return save_line(ISPLine(), payload, db, request)


@router.put('/{line_id}')
def update_line(line_id: int, payload: LinePayload, request: Request, db: Session = Depends(get_db)):
    row = db.get(ISPLine, line_id)
    if row is None:
        raise HTTPException(404, '线路不存在')
    return save_line(row, payload, db, request)


@router.delete('/{line_id}')
def delete_line(line_id: int, db: Session = Depends(get_db)):
    row = db.get(ISPLine, line_id)
    if row is None:
        raise HTTPException(404, '线路不存在')
    db.delete(row)
    db.commit()
    return {'ok': True}
