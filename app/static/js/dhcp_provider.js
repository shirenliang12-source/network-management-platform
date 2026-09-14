function dhcpProviderFields(data) {
    const provider=data.provider||'windows';
    return `<fieldset><legend>来源类型</legend><label>类型<select class="form-control" name="provider" ${data.legacy?'disabled':''}>${[['windows','Windows DHCP'],['fortinet','Fortinet（VDOM）'],['paloalto','Palo Alto（单虚拟系统）']].map(([v,n])=>`<option value="${v}" ${v===provider?'selected':''}>${n}</option>`).join('')}</select></label><div data-firewall-fields><label>HTTPS API 端口<input class="form-control" name="api_port" type="number" min="1" max="65535" value="${Number(data.api_port||443)}"></label><label>VDOM（仅 Fortinet，每个 VDOM 分别添加）<input class="form-control" name="vdom" value="${escapeHtml(data.vdom||'')}" placeholder="root"></label><label>DHCP 接口<input class="form-control" name="interface" value="${escapeHtml(data.interface||'')}" placeholder="port5 或 ethernet1/2"></label><label>子网掩码（仅 PA，手工确认，用于 IPAM 关联）<input class="form-control" name="netmask" value="${escapeHtml(data.netmask||'')}" placeholder="255.255.255.0"></label><label>只读 API 密钥<input class="form-control" name="api_token" type="password" autocomplete="new-password" placeholder="${data.api_token_configured?'已保存，留空保留':'首次必填；不是网页登录密码'}"></label><label><input type="checkbox" name="verify_ssl" ${data.verify_ssl!==false?'checked':''}>验证 HTTPS 证书（建议开启）</label><p>只读取，不修改 DHCP 配置。按设备、VDOM、接口和网段隔离；相同 VDOM 的多个池分别配置。未知格式保留旧统计；当前 Fortinet 含排除范围或保留地址的池暂不计算利用率。</p></div></fieldset>`;
}
function configureDhcpProviderForm(form, auth) {
    const toggle=()=>{
        const windows=form.elements.namedItem('provider').value==='windows';
        form.querySelector('[data-firewall-fields]').hidden=windows;
        auth.hidden=!windows;
    };
    form.elements.namedItem('provider').onchange=toggle;toggle();
}
function dhcpProviderPayload(form) {
    const f=form.elements,provider=f.namedItem('provider').value;
    if(provider==='windows')return {provider};
    return {provider,api_port:Number(f.namedItem('api_port').value),vdom:provider==='fortinet'?f.namedItem('vdom').value.trim():'',interface:f.namedItem('interface').value.trim(),netmask:provider==='paloalto'?f.namedItem('netmask').value.trim():'',api_token:f.namedItem('api_token').value,verify_ssl:f.namedItem('verify_ssl').checked};
}
