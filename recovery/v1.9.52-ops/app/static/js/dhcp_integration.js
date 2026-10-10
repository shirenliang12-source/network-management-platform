function dhcpStatistics(data) {
    if (!data || !data.source_id && !data.id) return '<span>未关联 DHCP 地址池</span>';
    const s = data.snapshot;
    return `<div style="color:${data.error || s?.warning ? '#b91c1c' : 'inherit'}">${data.mode !== 'DHCP' ? 'DHCP 已停用 · ' : ''}${data.error ? `⚠ ${escapeHtml(data.error)}（如有统计，为上次成功数据）` : s?.warning ? '⚠ DHCP 地址池预警' : 'DHCP 地址池'}${s ? `<br>已用 <b>${Number(s.used)}</b> · 空闲 <b>${Number(s.free)}</b> · 保留 ${s.reserved==null?'未知':Number(s.reserved)} · 利用率 <b>${Number(s.percent)}%</b><br><small>${escapeHtml(s.start)} – ${escapeHtml(s.end)} · ${escapeHtml(s.version||'')} ${escapeHtml(s.vdom||'')} ${escapeHtml(s.interface||'')} · 上次成功：${escapeHtml(s.synced_at)} · 上次采集使用账号：${escapeHtml(s.auth_identity||'旧版本未记录')}</small>` : '<br>暂无成功同步数据'}</div>`;
}

async function loadDhcpIntegrations() {
    const container = document.getElementById('dhcp-integration-list');
    if (!container) return;
    try {
        const rows = await API.get('/api/integrations/dhcp');
        container.innerHTML = rows.map((r,i)=>`<tr><td>${escapeHtml(r.name || r.scope)}${r.legacy?'<br><small>兼容原 IP 清单配置（同一份数据）</small>':''}</td><td>${escapeHtml(r.server)}<br>${escapeHtml(r.provider||'windows')} · ${escapeHtml(r.vdom||'')} · ${escapeHtml(r.interface||'')}<br>${escapeHtml(r.scope)}</td><td>${r.interval ? `每 ${Number(r.interval)} 分钟` : '仅手动'} · 阈值 ${Number(r.threshold)}%</td><td>${dhcpStatistics(r)}</td><td><button class="btn btn-sm btn-outline" data-dhcp-edit="${i}">配置</button> <button class="btn btn-sm btn-primary" data-dhcp-sync="${i}">立即同步 / 测试</button>${r.legacy?'':` <button class="btn btn-sm btn-danger" data-dhcp-delete="${i}">删除</button>`}</td></tr>`).join('') || '<tr><td colspan="5">暂无地址池，请添加 Windows / PA / Fortinet 来源。</td></tr>';
        container.querySelectorAll('[data-dhcp-edit]').forEach(b=>b.onclick=()=>editDhcpSource(rows[Number(b.dataset.dhcpEdit)]));
        container.querySelectorAll('[data-dhcp-sync]').forEach(b=>b.onclick=async()=>{
            b.disabled=true;b.textContent='同步中…';
            try { await API.post(`/api/integrations/dhcp/${rows[Number(b.dataset.dhcpSync)].id}/sync`,{});showToast('DHCP 同步完成，关联的 IP 规划已共享最新统计','success'); }
            catch(e){showToast(e.message,'error');}finally{loadDhcpIntegrations();}
        });
        container.querySelectorAll('[data-dhcp-delete]').forEach(b=>b.onclick=async()=>{
            const r=rows[Number(b.dataset.dhcpDelete)];
            if(!confirm(`删除平台中的 DHCP 配置 ${r.name || r.scope}？不会修改 DHCP 服务器或防火墙。`))return;
            try{await API.delete(`/api/integrations/dhcp/${r.id}`);loadDhcpIntegrations();}catch(e){showToast(e.message,'error');}
        });
    } catch(e){container.innerHTML=`<tr><td colspan="5">${escapeHtml(e.message)}</td></tr>`;}
}

function editDhcpSource(data={mode:'DHCP',name:'',server:'',scope:'',threshold:80,interval:60}) {
    document.getElementById('dhcp-source-modal')?.remove();
    const overlay=document.createElement('div');overlay.id='dhcp-source-modal';overlay.className='modal-overlay active';
    overlay.innerHTML=`<div class="modal"><div class="modal-header"><h3>DHCP 多来源地址池连接</h3><button class="btn" data-close>关闭</button></div><form class="modal-body"><p>每项配置对应一个 IPv4 地址池。Windows 使用服务账号或指定账号；防火墙使用只读 API 密钥。</p><label>名称<input class="form-control" name="name" maxlength="100" value="${escapeHtml(data.name||'')}"></label><label>服务器<input required class="form-control" name="server" value="${escapeHtml(data.server)}" placeholder="dhcp.example.local"></label><label>作用域网络地址<input required class="form-control" name="scope" value="${escapeHtml(data.scope)}" placeholder="192.168.1.0"></label><label>预警阈值 %<input required class="form-control" type="number" min="1" max="100" name="threshold" value="${Number(data.threshold)}"></label><label>同步间隔（分钟，0 仅手动）<input required class="form-control" type="number" min="0" max="10080" name="interval" value="${Number(data.interval)}"></label><p><label><input type="checkbox" name="enabled" ${data.mode==='DHCP'?'checked':''}>启用 DHCP</label></p><button class="btn btn-primary" type="submit">保存</button></form></div>`;
    document.body.appendChild(overlay);overlay.querySelector('[data-close]').onclick=()=>overlay.remove();
    const auth=document.createElement('div');auth.innerHTML=dhcpAuthFields(data);
    overlay.querySelector('form').insertBefore(auth,overlay.querySelector('form [type=submit]'));
    const provider=document.createElement('div');provider.innerHTML=dhcpProviderFields(data);
    overlay.querySelector('form').prepend(provider);
    configureDhcpProviderForm(overlay.querySelector('form'),auth);
    overlay.querySelector('form').onsubmit=async event=>{
        event.preventDefault();const f=event.target.elements;
        const payload={name:f.namedItem('name').value.trim(),server:f.namedItem('server').value.trim(),scope:f.namedItem('scope').value.trim(),threshold:Number(f.namedItem('threshold').value),interval:Number(f.namedItem('interval').value),mode:f.namedItem('enabled').checked?'DHCP':'静态'};
        Object.assign(payload,dhcpProviderPayload(event.target));
        if(payload.provider==='windows')Object.assign(payload,dhcpAuthPayload(event.target));
        const button=event.target.querySelector('[type=submit]');button.disabled=true;
        try{if(data.id)await API.put(`/api/integrations/dhcp/${data.id}`,payload);else await API.post('/api/integrations/dhcp',payload);overlay.remove();loadDhcpIntegrations();showToast('已保存；请同步成功后在 IP 规划中关联网段','success');}
        catch(e){showToast(e.message,'error');button.disabled=false;}
    };
}

async function showPrefixDhcp(prefixId) {
    try {
        const current=await API.get(`/api/ipam/prefixes/${prefixId}/dhcp`);
        let sources=[],catalogError='';
        try{sources=await API.get('/api/integrations/dhcp');}catch(e){catalogError=e.message;}
        document.getElementById('prefix-dhcp-modal')?.remove();
        const overlay=document.createElement('div');overlay.id='prefix-dhcp-modal';overlay.className='modal-overlay active';
        overlay.innerHTML=`<div class="modal"><div class="modal-header"><h3>规划网段 · DHCP 地址池</h3><button class="btn" data-close>关闭</button></div><div class="modal-body"><p>连接配置统一在 <a href="/integrations#dhcp-integration">平台集成 → DHCP</a> 维护。先同步，再选择作用域与本网段 CIDR 完全一致的地址池。</p>${dhcpStatistics(current)}<hr><select class="form-control" data-source><option value="">不关联 / 解除关联</option>${sources.map(r=>`<option value="${escapeHtml(r.id)}" ${r.id===current.source_id?'selected':''}>${escapeHtml(r.name||r.scope)} · ${escapeHtml(r.server)} · ${escapeHtml(r.scope)}${r.mode!=='DHCP'?'（停用）':''}</option>`).join('')}</select>${catalogError?`<p>${escapeHtml(catalogError)}；维护关联需同时具有平台集成与 IP 规划权限。</p>`:''}<p><button class="btn btn-primary" data-bind ${catalogError?'disabled':''}>保存关联</button> <button class="btn btn-outline" data-sync ${!current.source_id||catalogError?'disabled':''}>同步关联地址池</button></p><p>DHCP 数据是地址池统计，与平台注册占用分开显示，不相加，也不覆盖手动静态 IP / 虚拟机占用。</p></div></div>`;
        document.body.appendChild(overlay);overlay.querySelector('[data-close]').onclick=()=>overlay.remove();
        const refresh=async()=>{await showPrefixDhcp(prefixId);if(typeof loadIps==='function')loadIps();if(typeof loadPrefixes==='function')loadPrefixes();};
        overlay.querySelector('[data-bind]').onclick=async event=>{
            const source_id=overlay.querySelector('[data-source]').value||null;
            if(!confirm(source_id?'关联所选 DHCP 地址池？不会更改现有 IP 分配。':'解除 DHCP 关联？平台集成配置和 IP 分配均保留。'))return;
            event.target.disabled=true;
            try{await API.put(`/api/ipam/prefixes/${prefixId}/dhcp`,{source_id});await refresh();}catch(e){showToast(e.message,'error');event.target.disabled=false;}
        };
        overlay.querySelector('[data-sync]').onclick=async event=>{
            event.target.disabled=true;event.target.textContent='同步中…';
            try{await API.post(`/api/ipam/prefixes/${prefixId}/dhcp/sync`,{});}catch(e){showToast(e.message,'error');}finally{await refresh();}
        };
    } catch(e){showToast(e.message,'error');}
}
window.addEventListener('DOMContentLoaded',loadDhcpIntegrations);
setInterval(()=>{if(!document.hidden&&!document.querySelector('.modal-overlay.active'))loadDhcpIntegrations();},60000);
