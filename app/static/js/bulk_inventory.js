// Reuse authorized per-record APIs. Display per-record outcomes; never imply atomicity.
const bulkModules = {
    '/devices': [['设备', '/api/devices', {company:'公司', function:'用途', model:'型号'}]],
    '/vms': [['虚拟机', '/api/vms', {function:'用途', notes:'备注'}]],
    '/servers': [['服务器 / 存储', '/api/assets/servers', {owner:'负责人', notes:'备注'}]],
    '/assets': [['IT 资产', '/api/assets/it', {location_detail:'位置', notes:'备注'}]],
    '/ip-inventory': [['IP 地址清单', '/api/ip-inventory', {company:'公司', vlan:'VLAN', remarks:'备注'}]],
    '/ipam': [['聚合', '/api/ipam/aggregates', {description:'描述'}], ['网段', '/api/ipam/prefixes', {company:'公司', role:'用途', description:'描述'}], ['IP 地址', '/api/ipam/ips', {description:'描述', dns_name:'DNS 名称'}]],
    '/datacenter': [['数据中心', '/api/dc/sites', {company:'公司', region:'区域', description:'描述'}], ['机柜', '/api/dc/racks', {role:'用途', description:'描述'}]],
    '/accounts': [['账号记录', '/api/accounts', {category:'分类', notes:'备注'}]],
    '/settings': [['SSH 凭据', '/api/credentials/profiles', {description:'描述'}]],
    '/schedule': [['定时任务', '/api/schedule', {description:'描述'}]],
};

async function showBulkInventory() {
    const specs = bulkModules[location.pathname];
    if (!specs) return;
    document.getElementById('bulkInventoryModal')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'bulkInventoryModal'; overlay.className = 'modal-overlay active';
    overlay.innerHTML = `<div class="modal" style="width:1000px;max-width:95vw"><div class="modal-header"><h3>批量维护</h3><button class="btn" data-close>关闭</button></div><div class="modal-body"><p>本窗口独立筛选；全选仅选择下方当前筛选结果。操作逐条执行，失败项保留并显示原因，已成功项不会回滚。删除不可撤销，请先备份。</p><select data-module>${specs.map((s,i)=>`<option value="${i}">${escapeHtml(s[0])}</option>`).join('')}</select> <input data-search placeholder="筛选名称 / IP / 公司"><button class="btn btn-outline" data-all>全选筛选结果</button> <button class="btn btn-outline" data-none>清空选择</button><p><select data-field></select> <input data-value placeholder="新值（空白表示清空）"><button class="btn btn-primary" data-edit>编辑选中</button> <button class="btn btn-danger" data-delete>删除选中</button> <span data-count></span></p><div data-status role="status"></div><div style="max-height:50vh;overflow:auto"><table class="data-table"><tbody data-rows></tbody></table></div></div></div>`;
    document.body.appendChild(overlay);
    const q = s => overlay.querySelector(s);
    let records = [], selected = new Set(), running = false, changed = false, sequence = 0;
    const spec = () => specs[Number(q('[data-module]').value)];
    const visible = () => { const term = q('[data-search]').value.toLowerCase(); return records.filter(r => [r.name,r.address,r.prefix,r.ip_address,r.management_ip,r.subnet,r.company,r.description,r.rack_number].some(v=>String(v||'').toLowerCase().includes(term))); };
    function render() {
        q('[data-rows]').innerHTML = visible().map(r=>`<tr><td><input type="checkbox" data-id="${r.id}" ${selected.has(r.id)?'checked':''} ${running?'disabled':''}></td><td>#${r.id}</td><td>${escapeHtml(r.name||r.address||r.prefix||r.subnet||r.rack_number||r.description||'未命名')}</td><td>${escapeHtml(r.ip_address||r.management_ip||r.company||'')}</td></tr>`).join('');
        q('[data-count]').textContent = `选中 ${selected.size} / 筛选 ${visible().length} / 已加载 ${records.length}`;
        q('[data-rows]').querySelectorAll('[data-id]').forEach(c=>c.onchange=()=>{const id=Number(c.dataset.id); c.checked?selected.add(id):selected.delete(id); render();});
    }
    async function load() {
        const current = ++sequence;
        selected.clear(); records=[]; render();
        q('[data-field]').innerHTML = Object.entries(spec()[2]).map(([k,v])=>`<option value="${k}">${escapeHtml(v)}</option>`).join('');
        try { const data = await API.get(spec()[1]); if (current!==sequence) return; if (!Array.isArray(data)) throw new Error('列表返回格式不正确'); records=data; render(); }
        catch(e) { q('[data-status]').textContent=e.message; }
    }
    async function execute(remove) {
        if (running || !selected.size) return;
        const ids = [...selected], endpoint=spec()[1], field=q('[data-field]').value, value=q('[data-value]').value;
        if (!remove && !Object.hasOwn(spec()[2],field)) return;
        if (!confirm(`${remove?'永久删除':'修改'}选中的 ${ids.length} 条 ${spec()[0]}？${remove?'':'\n'+spec()[2][field]+' → '+(value||'（清空）')}`)) return;
        running=true;
        overlay.querySelectorAll('button,input,select').forEach(e=>e.disabled=true);
        let success=0; const errors=[];
        for (const id of ids) {
            try { if(remove) await API.delete(`${endpoint}/${id}`); else await API.put(`${endpoint}/${id}`, {[field]:value}); success++; changed=true; selected.delete(id); if(remove) records=records.filter(r=>r.id!==id); }
            catch(e) { errors.push(`#${id}: ${e.message}`); }
            q('[data-status]').textContent=`已处理 ${success+errors.length}/${ids.length}`;
        }
        running=false; overlay.querySelectorAll('button,input,select').forEach(e=>e.disabled=false); render();
        q('[data-status]').textContent=`成功 ${success}，失败 ${errors.length}。${errors.join('；')}`;
    }
    q('[data-close]').onclick=()=>{if(!running){++sequence;overlay.remove();if(changed)location.reload();}};
    q('[data-module]').onchange=load; q('[data-search]').oninput=()=>{selected.clear();render();};
    q('[data-all]').onclick=()=>{visible().forEach(r=>selected.add(r.id));render();};
    q('[data-none]').onclick=()=>{selected.clear();render();};
    q('[data-edit]').onclick=()=>execute(false); q('[data-delete]').onclick=()=>execute(true);
    await load();
}
window.addEventListener('DOMContentLoaded',()=>{
    if(!bulkModules[location.pathname])return;
    const header=document.querySelector('.page-header');
    if(header){const button=document.createElement('button');button.className='btn btn-outline';button.textContent='多选 / 全选批量维护';button.onclick=showBulkInventory;header.appendChild(button);}
});
