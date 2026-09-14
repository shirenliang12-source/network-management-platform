"""Read-only firewall DHCP APIs. Unknown/partial formats fail closed.

Fortinet: GET CMDB DHCP configuration + monitor leases in one explicit VDOM.
PAN-OS: XML API action=show (running DHCP config) + operational lease query.
No configuration writes, key generation, redirects, or raw response logging.
"""
import ipaddress
import json
import re
from xml.etree import ElementTree as ET
import httpx


def fail():
    raise ValueError('DHCP 返回格式不兼容或数据不完整；已保留上次成功快照，请核对接口、地址池和 API 读取权限')


def ranges(values, net):
    result=[]
    for lo,hi in values:
        lo,hi=ipaddress.IPv4Address(lo),ipaddress.IPv4Address(hi)
        if lo not in net or hi not in net or lo > hi:
            fail()
        result.append((int(lo),int(hi)))
    result.sort()
    if not result or any(result[i][0] <= result[i-1][1] for i in range(1,len(result))):
        fail()
    return result


def snapshot(config, net, pools, used, total, reserved=None, version=''):
    if type(used) is not int or type(total) is not int or total<=0 or not 0<=used<=total:
        fail()
    return {'scope':str(net.network_address),'mask':str(net.netmask),
            'start':str(ipaddress.IPv4Address(pools[0][0])), 'end':str(ipaddress.IPv4Address(pools[-1][1])),
            'ranges':[{'start':str(ipaddress.IPv4Address(a)),'end':str(ipaddress.IPv4Address(b))} for a,b in pools],
            'used':used,'free':total-used,'reserved':reserved,'percent':round(used/total*100,2),
            'state':'Active','version':version,'provider':config['provider'],
            'vdom':config.get('vdom',''),'interface':config['interface'],'auth_identity':'配置的只读 API 密钥'}


def fortinet(config, query):
    def get(path):
        doc=json.loads(query(path,{'vdom':config['vdom']}))
        if not isinstance(doc,dict) or doc.get('status')!='success' or doc.get('vdom')!=config['vdom'] or doc.get('limit_reached'):
            fail()
        if not isinstance(doc.get('results'),list):fail()
        return doc
    cfg=get('/api/v2/cmdb/system.dhcp/server')
    candidates=[r for r in cfg['results'] if r.get('interface')==config['interface']]
    # Never guess between multiple pools on the same interface.
    if len(candidates)!=1:fail()
    pool=candidates[0]
    if pool.get('status','enable')!='enable' or pool.get('server-type','regular')!='regular' or pool.get('ip-mode','range')!='range':fail()
    net=ipaddress.IPv4Network(f"{config['scope']}/{pool['netmask']}",strict=True)
    bounds=ranges([(r['start-ip'],r['end-ip']) for r in pool['ip-range']],net)
    # Exclusions and reservations can affect availability semantics: do not
    # claim free capacity until a supported calculation can be verified.
    if pool.get('exclude-range') or pool.get('reserved-address'):
        raise ValueError('此 Fortinet 池含排除范围或保留地址，当前适配器不计算其可用量；保留旧快照，需补充该池统计格式适配')
    total=sum(b-a+1 for a,b in bounds)
    leases=get('/api/v2/monitor/system/dhcp')
    seen=set()
    for lease in leases['results']:
        if not isinstance(lease,dict) or 'interface' not in lease:fail()
        if lease['interface']!=config['interface']:continue
        if lease.get('type')!='ipv4' or lease.get('status') not in ('leased','expired'):fail()
        if lease['status']=='expired':continue
        ip=int(ipaddress.IPv4Address(lease['ip']))
        if not any(a<=ip<=b for a,b in bounds):fail()
        seen.add(ip)
    return snapshot(config,net,bounds,len(seen),total,0,str(cfg.get('version','')))


def xml_response(raw):
    if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():fail()
    root=ET.fromstring(raw)
    if root.tag!='response' or root.get('status')!='success':
        raise ValueError('PA API 拒绝读取或命令不受支持，请核对密钥的配置读取与运行命令权限')
    result=root.find('result')
    if result is None:fail()
    return result


def paloalto(config,query):
    version=xml_response(query('/api/',{'type':'op','cmd':'<show><system><info></info></system></show>'})).findtext('.//sw-version') or ''
    # Fixed XPath cannot contain operator-supplied expressions.
    cfg=xml_response(query('/api/',{'type':'config','action':'show','xpath':'/config/devices/entry/network/dhcp'}))
    candidates=[r for r in cfg.findall('.//interface/entry') if r.get('name')==config['interface']]
    if len(candidates)!=1:fail()
    server=candidates[0].find('server')
    if server is None or server.findtext('mode')=='disabled':fail()
    # Explicit operator supplied netmask is visible in the source form; bounds
    # must fit it and the device-reported total must agree with configured pools.
    net=ipaddress.IPv4Network(f"{config['scope']}/{config['netmask']}",strict=True)
    values=[]
    for member in server.findall('ip-pool/member'):
        parts=(member.text or '').strip().split('-')
        if len(parts)==1:parts*=2
        if len(parts)!=2:fail()
        values.append(parts)
    bounds=ranges(values,net)
    cmd=ET.Element('show');dhcp=ET.SubElement(cmd,'dhcp');server_cmd=ET.SubElement(dhcp,'server');lease=ET.SubElement(server_cmd,'lease');ET.SubElement(lease,'interface').text=config['interface']
    result=xml_response(query('/api/',{'type':'op','cmd':ET.tostring(cmd,encoding='unicode')}))
    output=''.join(result.itertext())
    interfaces=re.findall(r'interface:\s*"([^"]+)"',output)
    counts=re.findall(r'Allocated IPs:\s*(\d+),\s*Total number of IPs in pool:\s*(\d+)',output)
    if interfaces!=[config['interface']] or len(counts)!=1:fail()
    used,total=map(int,counts[0])
    if total!=sum(b-a+1 for a,b in bounds):fail()
    return snapshot(config,net,bounds,used,total,None,version)


def collect(config,token):
    if config.get('provider') not in ('fortinet','paloalto'):fail()
    # Revalidate persisted host too; prohibit URL credentials/path/query injection.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',config['server']):fail()
    port=int(config.get('api_port',443))
    if not 1<=port<=65535:fail()
    with httpx.Client(trust_env=False,verify=config.get('verify_ssl',True),timeout=httpx.Timeout(30,connect=10),follow_redirects=False) as session:
        session.headers.update({'Authorization':'Bearer '+token} if config['provider']=='fortinet' else {'X-PAN-KEY':token})
        def query(path,params):
            with session.stream('GET',f"https://{config['server']}:{port}{path}",params=params) as response:
                if response.status_code in (401,403):
                    raise ValueError('防火墙 API 拒绝访问：请检查只读 API 密钥、允许的管理来源地址和所选 VDOM 权限')
                if response.status_code!=200:
                    raise ValueError(f'防火墙 API 返回 HTTP {response.status_code}，未接受重定向或其他地址')
                parts=[];size=0
                for part in response.iter_bytes(65536):
                    size+=len(part)
                    if size>10_000_000:raise ValueError('DHCP 响应超过 10MB，未写入不完整统计')
                    parts.append(part)
                return b''.join(parts).decode('utf-8-sig')
        try:
            return fortinet(config,query) if config['provider']=='fortinet' else paloalto(config,query)
        except httpx.HTTPError:
            raise ValueError('防火墙 API 连接失败或超时，请检查 HTTPS 证书信任、管理接口与端口；不会自动降低证书验证') from None
        except (KeyError,TypeError,ET.ParseError,UnicodeError,json.JSONDecodeError):
            fail()
