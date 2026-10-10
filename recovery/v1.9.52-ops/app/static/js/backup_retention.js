async function showBackupRetention() {
    try {
        const rows = await API.get('/api/backups/retention/devices');
        rows.forEach(row => { row.keep = row.override; });
        document.getElementById('retentionModal')?.remove();
        const modal = document.createElement('div');
        modal.id = 'retentionModal'; modal.className = 'modal-overlay active';
        modal.innerHTML = `<div class="modal"><div class="modal-header"><h3>每台设备备份保留数量</h3><button data-close class="btn">关闭</button></div><div class="modal-body"><p>留空：继承全局次数（未设置次数时使用原天数策略）；0：全部保留；5：最近 5 次。基线额外保留。保存不会立即删除，下次成功备份后清理。</p>${rows.map(r=>`<p>${escapeHtml(r.name)}（现有 ${r.count} 次） <input type="number" min="0" max="100000" data-keep="${r.id}" value="${r.keep ?? ''}" placeholder="继承全局"><button class="btn btn-outline" data-save="${r.id}">保存</button></p>`).join('')}</div></div>`;
        document.body.appendChild(modal);
        modal.querySelector('[data-close]').onclick = () => modal.remove();
        modal.querySelectorAll('[data-save]').forEach(button => { button.onclick = async () => {
            const value = modal.querySelector(`[data-keep="${button.dataset.save}"]`).value;
            const keep = value === '' ? null : Number(value);
            if (keep !== null && (!Number.isInteger(keep) || keep < 0 || keep > 100000)) return showToast('请输入有效保留次数', 'error');
            if (!confirm('保存保留策略？未来成功备份后，超出策略的非基线历史将被删除。')) return;
            try { const r = await API.put(`/api/backups/retention/devices/${button.dataset.save}`, {keep}); showToast(r.message, 'success'); }
            catch(e) { showToast(e.message, 'error'); }
        }; });
    } catch(e) { showToast(e.message, 'error'); }
}

async function loadBackupRetentionTable() {
    const body = document.getElementById('retentionDeviceTable');
    if (!body) return;
    try {
        const [globalPolicy, rows] = await Promise.all([
            API.get('/api/backups/retention/global'),
            API.get('/api/backups/retention/devices'),
        ]);
        document.getElementById('global-retention-keep').value = globalPolicy.keep ?? '';
        body.innerHTML = rows.length ? rows.map(row => `
            <tr>
                <td>${escapeHtml(row.name)}</td>
                <td>${Number(row.count || 0)} 次</td>
                <td>${row.keep === null || row.keep === undefined ? '原天数策略' : (row.keep === 0 ? '全部保留' : '最近 ' + row.keep + ' 次')}</td>
                <td><input class="form-control" type="number" min="0" max="100000" id="retention-device-${row.id}" value="${row.override ?? ''}" placeholder="继承全局"></td>
                <td><button class="btn btn-sm btn-primary" onclick="saveDeviceBackupRetention(${row.id})">保存</button></td>
            </tr>`).join('') : '<tr><td colspan="5" class="empty-state">暂无设备</td></tr>';
    } catch (error) {
        body.innerHTML = '<tr><td colspan="5" class="empty-state">加载保留策略失败：' + escapeHtml(error.message) + '</td></tr>';
    }
}

function backupRetentionValue(element) {
    const raw = element.value.trim();
    if (raw === '') return null;
    const keep = Number(raw);
    if (!Number.isInteger(keep) || keep < 0 || keep > 100000) {
        throw new Error('请输入 0–100000，或留空继承策略');
    }
    return keep;
}

async function saveGlobalBackupRetention() {
    try {
        const keep = backupRetentionValue(document.getElementById('global-retention-keep'));
        if (!confirm('保存全局备份保留数量？超出策略的非基线记录会在下一次成功备份后清理。')) return;
        const result = await API.put('/api/backups/retention/global', {keep});
        showToast(result.message, 'success');
        await loadBackupRetentionTable();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveDeviceBackupRetention(deviceId) {
    try {
        const keep = backupRetentionValue(document.getElementById(`retention-device-${deviceId}`));
        if (!confirm('保存该设备的独立保留数量？')) return;
        const result = await API.put(`/api/backups/retention/devices/${deviceId}`, {keep});
        showToast(result.message, 'success');
        await loadBackupRetentionTable();
    } catch (error) {
        showToast(error.message, 'error');
    }
}
