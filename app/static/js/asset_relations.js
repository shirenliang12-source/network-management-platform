// Follow relationships in either direction without changing inventory records.
const relationEndpoints = {device:'/api/devices', vm:'/api/vms', server:'/api/assets/servers',
    rack:'/api/dc/racks', site:'/api/dc/sites', prefix:'/api/ipam/prefixes', ip:'/api/ipam/ips'};
const relationPages = {device:'/devices', vm:'/vms', server:'/servers', rack:'/datacenter', site:'/datacenter', prefix:'/ipam', ip:'/ipam'};
let relationSequence = 0;
async function showAssetRelations(kind, id) {
    if (!relationEndpoints[kind] || !Number.isInteger(Number(id)) || Number(id) <= 0) return;
    const sequence = ++relationSequence;
    try {
        const data = await API.get(`${relationEndpoints[kind]}/${Number(id)}/relations`);
        if (sequence !== relationSequence) return;
        let overlay = document.getElementById('assetRelationsModal');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'assetRelationsModal';
            document.body.appendChild(overlay);
        }
        overlay.className = 'modal-overlay active';
        overlay.innerHTML = `<div class="modal" style="width:1000px;max-width:95vw"><div class="modal-header"><h3>关联反查：${escapeHtml(data.root.name)}</h3><button class="btn btn-outline" data-close>关闭</button></div><div class="modal-body">${data.notes.map(note=>`<p>${escapeHtml(note)}</p>`).join('')}${data.groups.map(group=>`<h4>${escapeHtml(group.title)}（${group.items.length}）</h4><table class="data-table"><tbody>${group.items.map(item=>`<tr><td>${escapeHtml(item.name)}</td><td style="overflow-wrap:anywhere">${escapeHtml(item.detail)}</td><td><button class="btn btn-sm btn-outline" data-kind="${escapeHtml(item.kind)}" data-id="${Number(item.id)}">继续反查</button> <a class="btn btn-sm btn-outline" href="${relationPages[item.kind]}?relation_kind=${encodeURIComponent(item.kind)}&relation_id=${Number(item.id)}">进入所属模块</a></td></tr>`).join('') || '<tr><td>暂无关联记录</td></tr>'}</tbody></table>`).join('')}</div></div>`;
        overlay.querySelector('[data-close]').onclick = () => { ++relationSequence; overlay.remove(); };
        overlay.querySelectorAll('[data-kind]').forEach(button => {
            button.onclick = () => showAssetRelations(button.dataset.kind, Number(button.dataset.id));
        });
        if ((data.allocations || []).length) {
            const section = document.createElement('div');
            section.className = 'modal-body';
            section.innerHTML = '<h4>手动 IP 占用 / 释放</h4>' + data.allocations.map((a,i) => `<p>${escapeHtml(a.label)} <button class="btn btn-sm btn-outline" data-allocation="${i}">${a.release ? '释放占用' : '手动占用'}</button></p>`).join('');
            overlay.querySelector('.modal').appendChild(section);
            section.querySelectorAll('[data-allocation]').forEach(button => {
                button.onclick = async () => {
                    const a = data.allocations[Number(button.dataset.allocation)];
                    if (!confirm(`${a.release ? '释放' : '登记'}以下 IP 占用？\n${a.label}`)) return;
                    button.disabled = true;
                    try {
                        const result = await API.post('/api/ipam/vm-allocation', {vm_id:a.vm_id, prefix_id:a.prefix_id, address:a.address, release:!!a.release});
                        showToast(result.message, 'success');
                        await showAssetRelations(kind, id);
                        if (typeof loadIps === 'function') loadIps();
                    } catch (e) { showToast(e.message, 'error'); button.disabled = false; }
                };
            });
        }
    } catch (error) { showToast('关联反查失败：' + error.message, 'error'); }
}
window.addEventListener('DOMContentLoaded', () => {
    const query = new URLSearchParams(window.location.search);
    const kind = query.get('relation_kind');
    if (kind && relationPages[kind] === window.location.pathname) showAssetRelations(kind, Number(query.get('relation_id')));
});
