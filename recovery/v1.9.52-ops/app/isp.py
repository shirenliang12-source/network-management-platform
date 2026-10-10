"""ISP circuit inventory. No scans, IPAM writes or host-range enumeration."""
import hashlib
import ipaddress
import json
import re
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.models import IPAMIPAddress, SystemSetting


class ISPLine(Base):
    __tablename__ = 'isp_lines'
    id = Column(Integer, primary_key=True)
    identity_key = Column(String(64), nullable=False, unique=True)
    provider = Column(String(120), nullable=False)
    circuit_number = Column(String(120), nullable=False)
    networks_json = Column(Text, nullable=False)
    used_json = Column(Text, nullable=False)
    reserved_json = Column(Text, nullable=False)
    bandwidth_mbps = Column(Integer, nullable=False, default=0)
    company_group = Column(String(120), nullable=False, default='')
    function_group = Column(String(120), nullable=False, default='')
    region_group = Column(String(120), nullable=False, default='')
    purpose = Column(String(500), nullable=False, default='')
    location = Column(String(500), nullable=False, default='')
    notes = Column(Text, nullable=False, default='')


class ISPGroup(Base):
    __tablename__ = 'isp_groups'
    __table_args__ = (UniqueConstraint('kind', 'name', name='uq_isp_group_kind_name'),)
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False, index=True)
    name = Column(String(120), nullable=False)


class ISPAddress(Base):
    __tablename__ = 'isp_addresses'
    __table_args__ = (UniqueConstraint('line_id','address',name='uq_isp_line_address'),)
    id = Column(Integer, primary_key=True)
    line_id = Column(Integer,ForeignKey('isp_lines.id',ondelete='CASCADE'),nullable=False,index=True)
    address = Column(String(45),nullable=False)
    status = Column(String(20),nullable=False,default='可用')
    purpose = Column(String(500),default='')
    ports = Column(Text,default='')
    owner = Column(String(200),default='')
    notes = Column(Text,default='')


class AddressPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: str
    purpose: str = Field(default='',max_length=500)
    ports: str = Field(default='',max_length=3000)
    owner: str = Field(default='',max_length=200)
    notes: str = Field(default='',max_length=5000)

    @field_validator('status')
    @classmethod
    def valid_status(cls,value):
        if value not in {'可用','使用中','预留','停用'}: raise ValueError('无效地址状态')
        return value

    @field_validator('ports')
    @classmethod
    def valid_ports(cls,value):
        tokens=[s.strip().upper() for s in re.split(r'[,，\n]',value) if s.strip()]
        for token in tokens:
            if token=='ICMP': continue
            match=re.fullmatch(r'(TCP|UDP)/(\d{1,5})(?:-(\d{1,5}))?',token)
            if not match or not 1<=int(match[2])<=int(match[3] or match[2])<=65535:
                raise ValueError('端口格式：TCP/443, UDP/53, TCP/8000-8010, ICMP')
        return ', '.join(dict.fromkeys(tokens))


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
    bandwidth_mbps: int = Field(default=0, ge=0, le=100000000)
    company_group: str = Field(default='', max_length=120)
    function_group: str = Field(default='', max_length=120)
    region_group: str = Field(default='', max_length=120)
    purpose: str = Field(default='', max_length=500)
    location: str = Field(default='', max_length=500)
    notes: str = Field(default='', max_length=5000)

    @field_validator('provider', 'circuit_number', 'company_group', 'function_group',
                     'region_group', 'purpose', 'location', 'notes', mode='before')
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
                bandwidth_mbps=row.bandwidth_mbps or 0,
                company_group=row.company_group or '', function_group=row.function_group or '',
                region_group=row.region_group or '', purpose=row.purpose or '',
                location=row.location, notes=row.notes, usable_count=str(total),
                ipam_linked=snapshot is not None,
                ipam_used_ips=sorted(auto_used), ipam_reserved_ips=sorted(auto_reserved),
                used_count=len(effective_used), reserved_count=len(effective_reserved),
                available_count=str(total-len(effective_used)-len(effective_reserved)),
                utilization=round(len(effective_used)*100/total, 2))


router = APIRouter(prefix='/api/isp', tags=['ISP线路'])


@router.get('')
def list_lines(request: Request, q: str = Query('', max_length=200),
               purpose: str = Query('', max_length=500), location: str = Query('', max_length=500),
               company_group: str = Query('', max_length=120),
               function_group: str = Query('', max_length=120),
               region_group: str = Query('', max_length=120),
               db: Session = Depends(get_db)):
    query = db.query(ISPLine)
    if q.strip():
        query = query.filter(ISPLine.provider.contains(q.strip(), autoescape=True) |
                             ISPLine.circuit_number.contains(q.strip(), autoescape=True) |
                             ISPLine.location.contains(q.strip(), autoescape=True) |
                             ISPLine.purpose.contains(q.strip(), autoescape=True))
    for value, column in ((purpose, ISPLine.purpose), (location, ISPLine.location),
                          (company_group, ISPLine.company_group),
                          (function_group, ISPLine.function_group),
                          (region_group, ISPLine.region_group)):
        if value.strip():
            query = query.filter(column == value.strip())
    snapshot = ipam_snapshot(db, request)
    return [representation(row, snapshot) for row in query.order_by(ISPLine.id.desc()).all()]


GROUP_FIELDS = {
    'company': 'company_group',
    'function': 'function_group',
    'region': 'region_group',
}


def identity_key(provider, circuit_number):
    return hashlib.sha256(json.dumps(
        [provider.casefold(), circuit_number.casefold()], ensure_ascii=False).encode()).hexdigest()


def ensure_group(db, kind, name):
    if not name:
        return ''
    existing = next((row for row in db.query(ISPGroup).filter(ISPGroup.kind == kind)
                     if row.name.casefold() == name.casefold()), None)
    if existing is None:
        db.add(ISPGroup(kind=kind, name=name))
        return name
    return existing.name


def validate_detail_bounds(row, payload, db):
    if row.id:
        networks=[ipaddress.ip_network(n) for n in payload.networks]
        for detail in db.query(ISPAddress).filter_by(line_id=row.id):
            address=ipaddress.ip_address(detail.address)
            if not any(address.version==net.version and bounds(net)[0]<=int(address)<=bounds(net)[1] for net in networks):
                raise HTTPException(409,'网段修改将使已维护IP越界，请先处理该地址记录')
            detail.status=('使用中' if detail.address in payload.used_ips else
                           ('停用' if detail.status=='停用' else '预留') if detail.address in payload.reserved_ips else '可用')


def apply_line(row, payload, db):
    validate_detail_bounds(row, payload, db)
    row.provider, row.circuit_number = payload.provider, payload.circuit_number
    row.identity_key = identity_key(payload.provider, payload.circuit_number)
    row.networks_json = json.dumps(payload.networks)
    row.used_json, row.reserved_json = json.dumps(payload.used_ips), json.dumps(payload.reserved_ips)
    row.bandwidth_mbps = payload.bandwidth_mbps
    row.company_group = ensure_group(db, 'company', payload.company_group)
    row.function_group = ensure_group(db, 'function', payload.function_group)
    row.region_group = ensure_group(db, 'region', payload.region_group)
    row.purpose = payload.purpose
    row.location, row.notes = payload.location, payload.notes
    db.add(row)


def save_line(row, payload, db, request):
    apply_line(row, payload, db)
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


class GroupCatalogPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company: list[str] = Field(default_factory=list, max_length=500)
    function: list[str] = Field(default_factory=list, max_length=500)
    region: list[str] = Field(default_factory=list, max_length=500)

    @field_validator('company', 'function', 'region')
    @classmethod
    def clean_names(cls, values):
        result, seen = [], set()
        for value in values:
            name = str(value).strip()
            if not name:
                continue
            if len(name) > 120:
                raise ValueError('分组名称不能超过120个字符')
            key = name.casefold()
            if key not in seen:
                result.append(name); seen.add(key)
        return result


def group_catalog(db):
    result = {kind: [] for kind in GROUP_FIELDS}
    for row in db.query(ISPGroup).order_by(ISPGroup.kind, ISPGroup.name):
        if row.kind in result:
            result[row.kind].append(row.name)
    return result


@router.get('/groups')
def get_groups(db: Session = Depends(get_db)):
    return group_catalog(db)


@router.get('/filter-options')
def filter_options(db: Session = Depends(get_db)):
    def values(column):
        return sorted({str(value or '').strip() for (value,) in db.query(column).distinct().all()
                       if str(value or '').strip()})
    return {
        'purpose': values(ISPLine.purpose),
        'location': values(ISPLine.location),
        **group_catalog(db),
    }


@router.put('/groups')
def save_groups(payload: GroupCatalogPayload, request: Request, db: Session = Depends(get_db)):
    requested = {kind: getattr(payload, kind) for kind in GROUP_FIELDS}
    existing = {kind: db.query(ISPGroup).filter_by(kind=kind).all() for kind in GROUP_FIELDS}
    for kind, field in GROUP_FIELDS.items():
        wanted = {name.casefold(): name for name in requested[kind]}
        used = {str(value or '').strip().casefold() for (value,) in
                db.query(getattr(ISPLine, field)).distinct().all() if str(value or '').strip()}
        removed_used = used - set(wanted)
        if removed_used:
            names = [row.name for row in existing[kind] if row.name.casefold() in removed_used]
            raise HTTPException(409, '不能删除仍被线路使用的分组：' + '、'.join(names or sorted(removed_used)))
        current = {row.name.casefold(): row for row in existing[kind]}
        for key, name in wanted.items():
            if key in current:
                if current[key].name != name:
                    db.query(ISPLine).filter(getattr(ISPLine, field) == current[key].name).update(
                        {field: name}, synchronize_session=False)
                current[key].name = name
            else:
                db.add(ISPGroup(kind=kind, name=name))
        for key, row in current.items():
            if key not in wanted:
                db.delete(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '分组名称重复，请刷新后重试')
    request.state.audit_action = 'isp.groups.update'
    return group_catalog(db)


DASHBOARD_WIDGETS = {
    'line_count', 'by_purpose', 'by_location', 'by_provider',
    'by_company_group', 'by_function_group', 'by_region_group', 'by_bandwidth',
}
DEFAULT_DASHBOARD_WIDGETS = ['line_count', 'by_purpose', 'by_location']


class DashboardPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    widgets: list[str] = Field(min_length=1, max_length=8)

    @field_validator('widgets')
    @classmethod
    def valid_widgets(cls, values):
        values = list(dict.fromkeys(values))
        if any(value not in DASHBOARD_WIDGETS for value in values):
            raise ValueError('包含不支持的仪表盘组件')
        return values


@router.get('/dashboard-settings')
def get_dashboard_settings(db: Session = Depends(get_db)):
    row = db.get(SystemSetting, 'isp_dashboard_widgets')
    try:
        widgets = json.loads(row.value) if row else DEFAULT_DASHBOARD_WIDGETS
        widgets = [value for value in widgets if value in DASHBOARD_WIDGETS]
    except (TypeError, ValueError, json.JSONDecodeError):
        widgets = DEFAULT_DASHBOARD_WIDGETS
    return {'widgets': widgets or DEFAULT_DASHBOARD_WIDGETS}


@router.put('/dashboard-settings')
def set_dashboard_settings(payload: DashboardPayload, request: Request, db: Session = Depends(get_db)):
    row = db.get(SystemSetting, 'isp_dashboard_widgets')
    if row is None:
        row = SystemSetting(key='isp_dashboard_widgets'); db.add(row)
    row.value = json.dumps(payload.widgets, ensure_ascii=False)
    db.commit()
    request.state.audit_action = 'isp.dashboard.update'
    return {'widgets': payload.widgets}


CSV_COLUMNS = [
    ('provider', '运营商'), ('circuit_number', '线路编号'), ('bandwidth_mbps', '带宽Mbps'),
    ('networks', '网段CIDR'), ('used_ips', '已使用IP'), ('reserved_ips', '预留IP'),
    ('company_group', '公司分组'), ('function_group', '功能分组'), ('region_group', '区域分组'),
    ('purpose', '用途'), ('location', '使用位置'), ('notes', '备注'),
]


def csv_safe(value):
    text = str(value or '')
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


def csv_restore(value):
    value = str(value or '').strip()
    return value[1:] if len(value) > 1 and value[0] == "'" and value[1] in '=+-@' else value


@router.get('/export')
def export_lines(db: Session = Depends(get_db)):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=[label for _, label in CSV_COLUMNS])
    writer.writeheader()
    for row in db.query(ISPLine).order_by(ISPLine.id):
        data = representation(row)
        writer.writerow({
            label: csv_safe('|'.join(data[key]) if key in {'networks', 'used_ips', 'reserved_ips'} else data.get(key, ''))
            for key, label in CSV_COLUMNS
        })
    body = ('\ufeff' + output.getvalue()).encode('utf-8')
    return Response(body, media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="isp-lines.csv"'})


def split_csv_list(value):
    text = csv_restore(value)
    return [item.strip() for item in re.split(r'[|,，;；\r\n]+', text) if item.strip()]


def parse_bandwidth(value):
    text = csv_restore(value).upper().replace(' ', '')
    if not text:
        return 0
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(G|GBPS|M|MBPS)?', text)
    if not match:
        raise ValueError('带宽请填写Mbps数字，或使用100Mbps/1Gbps格式')
    number = float(match.group(1)) * (1000 if (match.group(2) or '').startswith('G') else 1)
    if number != int(number):
        raise ValueError('换算后的带宽Mbps必须为整数')
    return int(number)


def csv_value(row, key, label):
    return csv_restore(row.get(label, row.get(key, '')))


@router.post('/import')
async def import_lines(request: Request, file: UploadFile = File(...),
                       mode: str = Query('skip', pattern='^(skip|update)$'),
                       db: Session = Depends(get_db)):
    content = await file.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(413, 'CSV文件不能超过2MB')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = content.decode('gb18030')
        except UnicodeDecodeError:
            raise HTTPException(422, 'CSV编码无法识别，请使用UTF-8或GB18030')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    if not reader.fieldnames:
        raise HTTPException(422, 'CSV缺少表头')
    parsed, errors, duplicate_rows, seen = [], [], 0, set()
    for number, source in enumerate(reader, start=2):
        if number > 5001:
            errors.append({'row': number, 'error': '一次最多导入5000条线路'}); break
        if not any(str(value or '').strip() for value in source.values()):
            continue
        try:
            payload = LinePayload(
                provider=csv_value(source, 'provider', '运营商'),
                circuit_number=csv_value(source, 'circuit_number', '线路编号'),
                bandwidth_mbps=parse_bandwidth(source.get('带宽Mbps', source.get('bandwidth_mbps', ''))),
                networks=split_csv_list(source.get('网段CIDR', source.get('networks', ''))),
                used_ips=split_csv_list(source.get('已使用IP', source.get('used_ips', ''))),
                reserved_ips=split_csv_list(source.get('预留IP', source.get('reserved_ips', ''))),
                company_group=csv_value(source, 'company_group', '公司分组'),
                function_group=csv_value(source, 'function_group', '功能分组'),
                region_group=csv_value(source, 'region_group', '区域分组'),
                purpose=csv_value(source, 'purpose', '用途'),
                location=csv_value(source, 'location', '使用位置'),
                notes=csv_value(source, 'notes', '备注'),
            )
            key = identity_key(payload.provider, payload.circuit_number)
            if key in seen:
                duplicate_rows += 1
                continue
            seen.add(key); parsed.append((number, key, payload))
        except (ValidationError, ValueError) as exc:
            errors.append({'row': number, 'error': str(exc)})
    if errors:
        raise HTTPException(422, {'message': 'CSV校验失败，本次未导入', 'errors': errors[:100]})
    existing = {row.identity_key: row for row in
                db.query(ISPLine).filter(ISPLine.identity_key.in_([key for _, key, _ in parsed])).all()}
    created = updated = skipped = 0
    try:
        for _number, key, payload in parsed:
            row = existing.get(key)
            if row is not None and mode == 'skip':
                skipped += 1
                continue
            if row is None:
                row = ISPLine(); created += 1
            else:
                updated += 1
            apply_line(row, payload, db)
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except IntegrityError:
        db.rollback(); raise HTTPException(409, '导入期间检测到重复线路，本次未导入')
    request.state.audit_action = 'isp.import'
    request.state.audit_detail = {'created': created, 'updated': updated, 'skipped': skipped,
                                  'duplicate_rows': duplicate_rows, 'mode': mode}
    return {'ok': True, 'created': created, 'updated': updated, 'skipped': skipped,
            'duplicate_rows': duplicate_rows,
            'message': f'导入完成：新增 {created}，更新 {updated}，跳过已存在 {skipped}，文件内重复 {duplicate_rows}'}


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
    db.query(ISPAddress).filter_by(line_id=line_id).delete()
    db.delete(row)
    db.commit()
    return {'ok': True}


def address_row(line,address,details,summary):
    detail=details.get(address)
    manual_used=set(json.loads(line.used_json)); manual_reserved=set(json.loads(line.reserved_json))
    state='使用中' if address in manual_used else ('预留' if address in manual_reserved else '可用')
    if detail: state=detail.status
    auto='使用中' if address in summary['ipam_used_ips'] else ('预分配' if address in summary['ipam_reserved_ips'] else '')
    effective='使用中' if auto=='使用中' or state=='使用中' else ('预留' if auto=='预分配' or state in {'预留','停用'} else '可用')
    return {'address':address,'status':state,'effective_status':effective,'ipam_status':auto,
            **{key:getattr(detail,key,'') or '' for key in ('purpose','ports','owner','notes')}}


@router.get('/{line_id}/addresses')
def list_addresses(line_id:int,request:Request,offset:str=Query('0',pattern=r'^\d{1,40}$'),
                   limit:int=Query(100,ge=1,le=200),address:str=Query('',max_length=45),db:Session=Depends(get_db)):
    line=db.get(ISPLine,line_id)
    if not line: raise HTTPException(404,'线路不存在')
    summary=representation(line,ipam_snapshot(db,request))
    nets=[ipaddress.ip_network(n) for n in summary['networks']]
    chosen=[]
    if address:
        try: ip=ipaddress.ip_address(address.strip())
        except ValueError: raise HTTPException(422,'请输入完整IP地址')
        if any(ip.version==n.version and bounds(n)[0]<=int(ip)<=bounds(n)[1] for n in nets): chosen=[str(ip)]
    else:
        skip=int(offset)
        for net in nets:
            first,last=bounds(net); size=last-first+1
            if skip>=size: skip-=size; continue
            count=min(limit-len(chosen),size-skip)
            cls=ipaddress.IPv4Address if net.version==4 else ipaddress.IPv6Address
            chosen.extend(str(cls(first+skip+i)) for i in range(count))
            skip=0
            if len(chosen)>=limit: break
    details={r.address:r for r in db.query(ISPAddress).filter(ISPAddress.line_id==line_id,ISPAddress.address.in_(chosen))}
    return {'items':[address_row(line,ip,details,summary) for ip in chosen],
            'total':summary['usable_count'],'offset':offset,'ipam_linked':summary['ipam_linked'],
            'next_offset':str(int(offset)+len(chosen)) if not address and int(offset)+len(chosen)<int(summary['usable_count']) else None}


@router.put('/{line_id}/addresses/{address}')
def edit_address(line_id:int,address:str,payload:AddressPayload,request:Request,db:Session=Depends(get_db)):
    line=db.get(ISPLine,line_id)
    if not line: raise HTTPException(404,'线路不存在')
    try: ip=ipaddress.ip_address(address)
    except ValueError: raise HTTPException(422,'无效IP')
    nets=[ipaddress.ip_network(n) for n in json.loads(line.networks_json)]
    if not any(ip.version==n.version and bounds(n)[0]<=int(ip)<=bounds(n)[1] for n in nets):
        raise HTTPException(422,'IP不在可用范围')
    address=str(ip)
    detail=db.query(ISPAddress).filter_by(line_id=line_id,address=address).first()
    if detail is None:
        detail=ISPAddress(line_id=line_id,address=address); db.add(detail)
    for key,value in payload.model_dump().items(): setattr(detail,key,value)
    used=set(json.loads(line.used_json)); reserved=set(json.loads(line.reserved_json))
    used.discard(address); reserved.discard(address)
    if payload.status=='使用中': used.add(address)
    elif payload.status in {'预留','停用'}: reserved.add(address)
    line.used_json=json.dumps(sorted(used)); line.reserved_json=json.dumps(sorted(reserved))
    try: db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409,'地址记录已变化，请刷新重试')
    return {'ok':True,'message':'已保存本线路地址台账；IPAM占用保持不变'}
