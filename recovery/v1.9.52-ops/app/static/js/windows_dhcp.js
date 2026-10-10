async function showWindowsDhcp(id) {
    try {
        const data = await API.get(`/api/ip-inventory/${id}/dhcp`);
        document.getElementById('dhcpModal')?.remove();
        const overlay = document.createElement('div');
        overlay.id = 'dhcpModal'; overlay.className = 'modal-overlay active';
        const s = data.snapshot;
        overlay.innerHTML = `<div class="modal"><div class="modal-header"><h3>Windows DHCP 地址池</h3><button data-close class="btn">关闭</button></div><div class="modal-body">
        <p>只读同步 IPv4 作用域，不修改 DHCP 服务器配置。安装电脑需有 RSAT DHCP 模块，平台服务运行账号需具有 DHCP 读取权限。清单的子网/掩码必须与作用域相同。</p>
        <label>分配模式 <select id="dhcpMode"><option value="静态">静态</option><option value="DHCP" ${data.mode==='DHCP'?'selected':''}>DHCP</option></select></label>
        <p><label>DHCP 服务器 <input id="dhcpServer" value="${escapeHtml(data.server)}" placeholder="dhcp.example.local"></label></p>
        <p><label>作用域网络地址 <input id="dhcpScope" value="${escapeHtml(data.scope)}" placeholder="192.168.1.0"></label></p>
        <p><label>利用率预警阈值 % <input id="dhcpThreshold" type="number" min="1" max="100" value="${Number(data.threshold)}"></label></p>
        <p><label>定期同步间隔（分钟，0 为关闭） <input id="dhcpInterval" type="number" min="0" max="10080" value="${Number(data.interval)}"></label></p>
        <button class="btn btn-primary" data-save>保存配置</button> <button class="btn btn-outline" data-sync>立即同步已保存配置</button>
        ${data.error ? `<p style="color:#b91c1c">同步失败：${escapeHtml(data.error)}；以下若有数据，为上次成功快照。</p>` : ''}
        ${s ? `<hr><p style="color:${s.warning?'#b91c1c':'inherit'}">${s.warning?'⚠ 地址池利用率达到预警阈值':'地址池统计'}：已用 ${Number(s.used)} / 空闲 ${Number(s.free)} / 保留 ${Number(s.reserved)} · 利用率 ${Number(s.percent)}%</p><p>地址池 ${escapeHtml(s.start)} – ${escapeHtml(s.end)} · 状态 ${escapeHtml(s.state)}<br>上次成功同步 ${escapeHtml(s.synced_at)}</p><p>此处是 DHCP 地址池利用率，不包含池外手工静态地址；保留数量不与已用数量重复相加。</p>` : '<p>暂无成功同步数据。</p>'}
        </div></div>`;
        document.body.appendChild(overlay);
        const auth=document.createElement('div');auth.innerHTML=dhcpAuthFields(data);
        overlay.querySelector('.modal-body').insertBefore(auth,overlay.querySelector('[data-save]'));
        overlay.querySelector('[data-close]').onclick = () => overlay.remove();
        overlay.querySelector('[data-save]').onclick = async () => {
            try {
                await API.put(`/api/ip-inventory/${id}/dhcp`, {...dhcpAuthPayload(overlay),mode:document.getElementById('dhcpMode').value, server:document.getElementById('dhcpServer').value.trim(), scope:document.getElementById('dhcpScope').value.trim(), threshold:Number(document.getElementById('dhcpThreshold').value), interval:Number(document.getElementById('dhcpInterval').value)});
                showToast('DHCP 配置已保存', 'success'); showWindowsDhcp(id);
            } catch(e) { showToast(e.message, 'error'); }
        };
        overlay.querySelector('[data-sync]').onclick = async event => {
            event.target.disabled = true; event.target.textContent = '同步中…';
            try { await API.post(`/api/ip-inventory/${id}/dhcp/sync`, {}); showToast('DHCP 同步完成', 'success'); }
            catch(e) { showToast(e.message, 'error'); }
            finally { showWindowsDhcp(id); }
        };
    } catch(e) { showToast(e.message, 'error'); }
}

// Keep threshold warnings visible while the inventory page is open.
setInterval(() => {
    if (!document.hidden && !document.querySelector('.modal-overlay.active') && typeof loadItems === 'function') loadItems();
}, 60000);
