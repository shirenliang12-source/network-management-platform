"""Read-only Windows DHCP scope statistics using the service account identity."""
import base64
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from app.models import SystemSetting, IPInventory

_lock = threading.Lock()


def read_config(db, item_id):
    row = db.get(SystemSetting, f'dhcp_scope:{item_id}')
    return json.loads(row.value) if row else {'mode': '静态', 'server': '', 'scope': '', 'threshold': 80, 'interval': 60}


def save_config(db, item_id, value):
    from app.services.dhcp_integration import protect_source_change
    protect_source_change(db, f'inventory-{item_id}', read_config(db, item_id), value)
    key = f'dhcp_scope:{item_id}'
    row = db.get(SystemSetting, key)
    if not row:
        row = SystemSetting(key=key)
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)
    db.commit()


def collect(server, scope, username=None, password=None):
    import ipaddress
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', server):
        raise ValueError('DHCP 服务器必须是主机名或 IPv4 地址')
    scope = str(ipaddress.IPv4Address(scope))
    if os.name != 'nt':
        raise ValueError('此采集方式需要 Windows 平台及 DHCP 管理工具')
    script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$stage = 'input'
$session = $null
try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $stage = 'module'
    Import-Module DhcpServer -ErrorAction Stop
    $query = @{ScopeId=[ipaddress]$payload.scope}
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ($payload.username) {
        $stage = 'authentication'
        $secure = ConvertTo-SecureString $payload.password -AsPlainText -Force
        $credential = [pscredential]::new($payload.username, $secure)
        $option = New-CimSessionOption -Protocol Dcom
        $session = New-CimSession -ComputerName $payload.server -Credential $credential -SessionOption $option -OperationTimeoutSec 20
        $query.CimSession = $session
        $identity = [string]$payload.username
    } else {
        $query.ComputerName = [string]$payload.server
    }
    $stage = 'scope'
    $scopeInfo = Get-DhcpServerv4Scope @query
    $stage = 'statistics'
    $stats = Get-DhcpServerv4ScopeStatistics @query
    [pscustomobject]@{scope=[string]$scopeInfo.ScopeId; mask=[string]$scopeInfo.SubnetMask; start=[string]$scopeInfo.StartRange; end=[string]$scopeInfo.EndRange; state=[string]$scopeInfo.State; used=[long]$stats.InUse; free=[long]$stats.Free; reserved=[long]$stats.Reserved; percent=[double]$stats.PercentageInUse; auth_identity=$identity; auth_mode=$(if ($payload.username) {'manual'} else {'system'})} | ConvertTo-Json -Compress
} catch {
    $category = 'unknown'
    $errorText = [string]$_.Exception.Message
    if ($errorText -match '1326|8007052e|logon failure|登录失败|用户名或密码') {$category='logon'}
    elseif ($_.CategoryInfo.Category -in @('PermissionDenied','SecurityError') -or $errorText -match 'access.*denied|拒绝访问|80070005') {$category='permission'}
    elseif ($errorText -match '1722|800706ba|RPC.*unavailable|RPC.*不可用') {$category='rpc'}
    [pscustomobject]@{error_stage=$stage; error_category=$category; error_code=[int]$_.Exception.HResult} | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($session) {Remove-CimSession -CimSession $session -ErrorAction SilentlyContinue}
}
"""
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    executable = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                input=json.dumps({'server':server,'scope':scope,'username':username,'password':password},ensure_ascii=True).encode('ascii'),
                                capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ValueError('DHCP 读取超时；请检查目标 RPC/DCOM 服务及防火墙，Ping 通不代表管理接口可用') from None
    except OSError:
        raise ValueError('无法启动 Windows PowerShell，请检查本机组件和程序执行权限') from None
    if result.returncode:
        try:
            diagnostic=json.loads(result.stdout.decode('utf-8-sig').strip())
        except (ValueError,UnicodeError):
            diagnostic={}
        stage=diagnostic.get('error_stage')
        category=diagnostic.get('error_category')
        if stage=='module':
            raise ValueError('DHCP 管理模块加载失败：请在安装平台的电脑安装/启用 RSAT DHCP 管理工具（不是只在 DHCP 服务器安装）')
        if category=='logon':
            raise ValueError('DHCP 登录认证失败：请检查域、账号、密码；未回退到服务账号')
        if category=='permission':
            stages = {'authentication': '建立远程 CIM/DCOM 会话', 'scope': '读取 DHCP 作用域', 'statistics': '读取 DHCP 地址池统计'}
            phase = stages.get(stage, '远程采集（阶段未知）')
            identity = '手工指定账号（未回退到服务账号）' if username else '平台 Windows 服务运行账号（不是当前网页登录账号）'
            hint = ('请由服务器管理员核对该账号的 DCOM 远程访问、远程启动/激活及 WMI 命名空间远程访问权限'
                    if stage == 'authentication' else
                    '请由服务器管理员核对该账号的 DHCP 只读访问权限及 DHCP WMI 提供程序/命名空间权限；会话创建成功不等于 DHCP 查询已获授权')
            raise ValueError(f'DHCP 拒绝访问：阶段={phase}；使用={identity}。{hint}。Ping 通不能验证这些权限；无需关闭防火墙或禁用 UAC')
        if category=='rpc':
            raise ValueError('DHCP RPC/DCOM 不可用：请检查目标管理接口及防火墙；Ping 通不能确认 RPC 连通')
        labels={'authentication':'创建指定账号的远程 CIM 会话','scope':'读取作用域','statistics':'读取地址池统计','input':'读取本地采集参数'}
        raise ValueError('DHCP '+labels.get(stage,'采集进程')+'失败；请检查作用域、远程 CIM/DCOM 权限及服务状态')
    data = json.loads(result.stdout.decode('utf-8-sig').strip())
    for key in ('used', 'free', 'reserved'):
        if isinstance(data.get(key), bool) or not isinstance(data.get(key), int) or data[key] < 0:
            raise ValueError('DHCP 返回无效计数，保留上次成功数据')
    if data.get('scope') != scope or not isinstance(data.get('percent'), (int, float)) or not 0 <= data['percent'] <= 100:
        raise ValueError('DHCP 返回无效作用域或利用率')
    return data


def sync(db, item_id):
    import ipaddress
    if not _lock.acquire(blocking=False):
        raise ValueError('已有 DHCP 同步正在执行，请稍后重试')
    try:
        item = db.get(IPInventory, item_id)
        if not item:
            raise ValueError('IP 清单记录不存在')
        config = read_config(db, item_id)
        if config['mode'] != 'DHCP':
            raise ValueError('请先选择 DHCP 并保存配置')
        config['last_attempt'] = datetime.now(timezone.utc).isoformat()
        try:
            from app.services.dhcp_credentials import collect_config
            data = collect_config(config)
            db.rollback()
            item = db.get(IPInventory, item_id)
            current = read_config(db, item_id)
            if not item or any(current.get(k) != config.get(k) for k in ('server', 'scope', 'mode', 'threshold', 'interval', 'auth_mode', 'username', 'password_enc')):
                raise RuntimeError('configuration_changed')
            actual = ipaddress.ip_network(f"{data['scope']}/{data['mask']}", strict=False)
            subnet = item.subnet.strip()
            expected = ipaddress.ip_network(subnet if '/' in subnet else f'{subnet}/{item.mask}', strict=False)
            if actual != expected:
                raise ValueError('DHCP 作用域与清单的子网/掩码不匹配，未写入统计')
            data['warning'] = data['percent'] >= config['threshold']
            data['synced_at'] = datetime.now(timezone.utc).isoformat()
            config['snapshot'] = data
            config['error'] = ''
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) == 'configuration_changed':
                raise ValueError('同步期间配置或条目已变化，结果已丢弃，请重新同步') from exc
            config['error'] = str(exc) if isinstance(exc, ValueError) else 'DHCP 同步失败或超时；保留上次成功数据'
            db.rollback()
            current = read_config(db, item_id)
            if db.get(IPInventory, item_id) and all(current.get(k) == config.get(k) for k in ('server', 'scope', 'mode', 'threshold', 'interval', 'auth_mode', 'username', 'password_enc')):
                save_config(db, item_id, config)
            raise ValueError(config['error']) from exc
        save_config(db, item_id, config)
        return config
    finally:
        _lock.release()


def scheduled_sync():
    from app.database import SessionLocal
    with SessionLocal() as db:
        from app.services.dhcp_integration import sync_due
        sync_due(db)
        ids = [r.id for r in db.query(IPInventory).all()]
        for item_id in ids:
            config = read_config(db, item_id)
            if config.get('mode') != 'DHCP' or not config.get('interval'):
                continue
            last = config.get('last_attempt')
            if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < config['interval'] * 60:
                continue
            try:
                sync(db, item_id)
            except ValueError:
                pass  # Error and last successful snapshot remain visible in the UI.
