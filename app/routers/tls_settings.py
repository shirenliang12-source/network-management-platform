import ipaddress
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.config import DATA_DIR, settings, TLS_EXTERNAL_CONFIGURED
from app.routers.system import _require_superuser
from app.services.tls_config import configured_paths, import_certificate

router = APIRouter(prefix='/api/settings/https', tags=['settings'])


class CertificateImport(BaseModel):
    certificate_pem: str = Field(min_length=1, max_length=65536)
    private_key_pem: str = Field(min_length=1, max_length=32768)
    password: str | None = Field(default=None, max_length=1024)


@router.get('')
def status(request: Request):
    _require_superuser(request)
    paths = configured_paths(DATA_DIR)
    metadata = {}
    if paths:
        saved = json.loads((DATA_DIR / 'tls/active.json').read_text(encoding='utf-8'))
        metadata = {k: saved.get(k) for k in ('subject', 'expires', 'sha256')}
    return {'configured': bool(paths), 'https_active': bool(settings.SSL_CERTFILE),
            'restart_required': bool(paths and paths['SSL_CERTFILE'] != settings.SSL_CERTFILE), **metadata}


@router.post('/certificate')
def upload_certificate(payload: CertificateImport, request: Request):
    _require_superuser(request)
    try:
        local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        local = False
    # Never accept private key material over a remote plaintext connection.
    if request.url.scheme != 'https' and not local:
        raise HTTPException(400, '请在服务器本机通过回环地址访问，或使用已启用的 HTTPS 导入证书')
    if TLS_EXTERNAL_CONFIGURED:
        raise HTTPException(409, '当前使用外部配置的证书，请先由管理员处理环境变量或 .env，避免配置冲突')
    try:
        metadata = import_certificate(DATA_DIR, payload.certificate_pem, payload.private_key_pem, payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, '证书未激活，请检查服务账号的目录权限和证书链；原配置保留') from exc
    request.state.audit_resource_type = 'https_certificate'
    return {'ok': True, 'restart_required': True, **metadata}
