"""DHCP credentials are stored encrypted and are never returned by APIs."""
from fastapi import HTTPException
from app.models import encrypt_password, decrypt_password


def public(config):
    return {**{k:v for k,v in config.items() if k not in {'password','password_enc','api_token','api_token_enc'}},
            'api_token_configured':bool(config.get('api_token_enc')),
            'password_configured':bool(config.get('password_enc'))}


def merge(previous, supplied):
    result = {**previous, **{k:v for k,v in supplied.items() if v is not None and k not in {'password','password_enc','password_configured','api_token','api_token_enc','api_token_configured'}}}
    if result.get('provider','windows') != 'windows':
        token = supplied.get('api_token')
        if token:
            if '\r' in token or '\n' in token:
                raise HTTPException(422, 'API 密钥格式无效')
            result['api_token_enc'] = encrypt_password(token)
        if not result.get('api_token_enc'):
            raise HTTPException(422, '防火墙来源需填写只读 API 密钥')
        if previous and any(result.get(k)!=previous.get(k) for k in ('server','provider','api_port')) and not token:
            raise HTTPException(422, '更换设备、类型或端口时请重新输入 API 密钥')
        return result
    password = supplied.get('password')
    if password:
        result['password_enc'] = encrypt_password(password)
    if result.get('auth_mode','system') == 'manual':
        if not result.get('username') or not result.get('password_enc'):
            raise HTTPException(422, '手工认证需填写账号和密码；建议使用 DOMAIN\\user 或 user@domain')
        if previous.get('password_enc') and any(result.get(k)!=previous.get(k) for k in ('username','server')) and not password:
            raise HTTPException(422, '更换账号或服务器时请重新输入密码')
    return result


def collect_config(config):
    if config.get('provider','windows') != 'windows':
        from app.services.firewall_dhcp import collect
        token=decrypt_password(config.get('api_token_enc',''))
        if not token:
            raise ValueError('DHCP API 密钥无法解密，请重新填写；不会回退到其他账号')
        return collect(config, token)
    from app.services.windows_dhcp import collect
    if config.get('auth_mode','system') != 'manual':
        return collect(config['server'],config['scope'])
    password=decrypt_password(config.get('password_enc',''))
    if not password:
        raise ValueError('保存的 DHCP 密码无法解密，请重新填写；不会回退到服务账号')
    return collect(config['server'],config['scope'],username=config['username'],password=password)
