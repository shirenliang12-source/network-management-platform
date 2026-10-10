function dhcpAuthFields(config) {
    return `<fieldset><legend>读取账号</legend><label>认证方式<select class="form-control" data-dhcp-auth><option value="system">平台 Windows 服务运行账号</option><option value="manual" ${config.auth_mode==='manual'?'selected':''}>指定账号（远程 CIM/DCOM）</option></select></label><label>指定账号<input class="form-control" data-dhcp-user autocomplete="off" placeholder="DOMAIN\\user 或 user@domain" value="${escapeHtml(config.username||'')}"></label><label>密码<input class="form-control" data-dhcp-password type="password" autocomplete="new-password" placeholder="${config.password_configured?'已保存，留空保留；更换账号/服务器需重填':'首次使用指定账号必填'}"></label><small>加密保存；不更改 Windows 服务账号；认证失败不会回退。需有 DHCP 读取权限及远程 CIM/DCOM 权限。Ping 通不代表 RPC/DCOM 可用。</small></fieldset>`;
}
function dhcpAuthPayload(root) {
    return {auth_mode:root.querySelector('[data-dhcp-auth]').value,username:root.querySelector('[data-dhcp-user]').value.trim(),password:root.querySelector('[data-dhcp-password]').value};
}
