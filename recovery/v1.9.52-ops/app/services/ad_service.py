"""Active Directory / LDAP 集成服务（基于 ldap3，纯 Python，适配 PyInstaller 单文件打包）。

提供三类能力：
  1. test_connection  —— 用配置的服务账号绑定 AD，验证连通性并取回域信息；
  2. search_users     —— 按 (Base DN + 过滤) 搜索 AD 用户，标准化为字典列表；
  3. verify_credential—— 用给定「账号 + 密码」直接绑定 AD，校验凭据是否有效
                        （AD 为权威源，本地不保存 AD 用户密码）。
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _server(cfg: Dict[str, Any]):
    from ldap3 import Server, ALL
    host = (cfg.get("host") or "").strip()
    if not host:
        raise ValueError("未配置 AD 服务器地址")
    port = int(cfg.get("port") or 389)
    use_ssl = bool(cfg.get("use_ssl"))
    return Server(host, port=port, use_ssl=use_ssl, get_info=ALL, connect_timeout=10)


def _default_naming(server) -> str:
    try:
        if server.info and getattr(server.info, "other", None):
            vals = server.info.other.get("defaultNamingContext")
            if vals:
                return vals[0]
    except Exception:
        pass
    return ""


def test_connection(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """用配置的服务账号绑定 AD，返回连接状态与基本信息。"""
    from ldap3 import Connection, SIMPLE, SUBTREE
    try:
        server = _server(cfg)
        bind_dn = (cfg.get("bind_dn") or "").strip() or None
        bind_pw = cfg.get("bind_password") or None
        authentication = SIMPLE if bind_dn else None
        conn = Connection(server, user=bind_dn, password=bind_pw,
                           auto_bind=True, authentication=authentication)
        default_naming = _default_naming(server)
        # 试搜一个用户，确认搜索基线可用
        base = (cfg.get("base_dn") or default_naming or "").strip()
        sample_ok = False
        if base:
            try:
                sample_ok = conn.search(base, cfg.get("user_filter") or "(objectClass=user)",
                                         SUBTREE, attributes=["sAMAccountName"], size_limit=1)
            except Exception:
                sample_ok = False
        try:
            conn.unbind()
        except Exception:
            pass
        return {
            "ok": True,
            "message": "AD 连接成功" + ("" if sample_ok else "（但按当前 Base DN / 过滤未匹配到用户，请检查过滤条件）"),
            "server": f"{server.host}:{server.port}",
            "ssl": bool(cfg.get("use_ssl")),
            "default_naming_context": default_naming,
        }
    except Exception as e:  # ldap3 抛 LDAPException 等
        return {"ok": False, "message": f"AD 连接失败: {e}"}


def search_users(cfg: Dict[str, Any], limit: int = 2000) -> List[Dict[str, Any]]:
    """搜索 AD 用户，返回标准化列表（sam / name / mail / dn / disabled）。"""
    from ldap3 import Connection, SIMPLE, SUBTREE
    server = _server(cfg)
    bind_dn = (cfg.get("bind_dn") or "").strip() or None
    bind_pw = cfg.get("bind_password") or None
    authentication = SIMPLE if bind_dn else None
    conn = Connection(server, user=bind_dn, password=bind_pw,
                      auto_bind=True, authentication=authentication)
    try:
        base = (cfg.get("base_dn") or "").strip()
        if not base:
            base = _default_naming(server)
        sam_attr = cfg.get("sam_attr") or "sAMAccountName"
        name_attr = cfg.get("name_attr") or "displayName"
        mail_attr = cfg.get("mail_attr") or "mail"
        attrs = [sam_attr, name_attr, mail_attr, "distinguishedName", "userAccountControl"]
        conn.search(base, cfg.get("user_filter") or "(objectClass=user)", SUBTREE,
                    attributes=attrs, size_limit=limit, time_limit=60)
        users: List[Dict[str, Any]] = []

        for e in conn.entries:
            def val(attr):
                try:
                    a = getattr(e, attr, None)
                    return a.value if a is not None else ""
                except Exception:
                    return ""

            sam = val(sam_attr)
            if not sam:
                continue
            uac = val("userAccountControl")
            try:
                uac_int = int(uac) if str(uac).isdigit() else 0
            except Exception:
                uac_int = 0
            disabled = bool(uac_int & 0x0002)
            users.append({
                "sam": sam,
                "name": val(name_attr) or sam,
                "mail": val(mail_attr),
                "dn": val("distinguishedName"),
                "disabled": disabled,
            })
        return users
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def verify_credential(cfg: Dict[str, Any], username: str, password: str) -> Dict[str, Any]:
    """用给定账号密码直接绑定 AD，校验凭据是否有效。

    username 支持 sAMAccountName、domain\\sam 或 user@domain(UPN) 形式。
    """
    from ldap3 import Connection, SIMPLE
    if not username:
        return {"ok": False, "message": "请输入用户名"}
    try:
        server = _server(cfg)
        conn = Connection(server, user=username, password=password or "",
                           auto_bind=True, authentication=SIMPLE)
        try:
            conn.unbind()
        except Exception:
            pass
        return {"ok": True, "message": "AD 凭据验证通过"}
    except Exception as e:
        return {"ok": False, "message": f"AD 凭据无效: {e}"}
