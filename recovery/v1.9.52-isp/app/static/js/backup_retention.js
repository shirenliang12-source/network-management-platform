async function showBackupRetention() {
    try {
        const rows = await API.get('/api/backups/retention/devices');
        document.getElementById('retentionModal')?.remove();
        const modal = document.createElement('div');
        modal.id = 'retentionModal'; modal.className = 'modal-overlay active';
        modal.innerHTML = `<div class="modal"><div class="modal-header"><h3>每台设备备份保留数量</h3><button data-close class="btn">关闭</button></div><div class="modal-body"><p>留空：继承全局天数；0：全部保留；5：最近 5 次。基线额外保留。保存不会立即删除，下次成功备份后清理。</p>${rows.map(r=>`<p>${escapeHtml(r.name)}（现有 ${r.count} 次） <input type="number" min="0" max="100000" data-keep="${r.id}" value="${r.keep ?? ''}" placeholder="继承全局"><button class="btn btn-outline" data-save="${r.id}">保存</button></p>`).join('')}</div></div>`;
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
