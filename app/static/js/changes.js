let changeItems = [];

function changeStatusBadge(status) {
    const labels = {
        pending: ['待确认', 'badge-warning'], expected: ['预期变更', 'badge-success'],
        unexpected: ['异常变更', 'badge-danger'], ignored: ['已忽略', 'badge-secondary'],
    };
    const item = labels[status] || [status || '待确认', 'badge-secondary'];
    return `<span class="badge ${item[1]}">${escapeHtml(item[0])}</span>`;
}

function closeChangeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove('active');
}

async function loadConfigurationChanges() {
    const device = document.getElementById('change-device').value;
    const status = document.getElementById('change-status').value;
    const params = new URLSearchParams({limit: '200'});
    if (device) params.set('device_id', device);
    if (status) params.set('status', status);
    try {
        const data = await API.get(`/api/backups/changes?${params}`);
        changeItems = data.items || [];
        const summary = data.summary || {};
        for (const key of ['pending', 'expected', 'unexpected', 'ignored']) {
            document.getElementById(`change-${key}`).textContent = summary[key] || 0;
        }
        document.getElementById('change-drifted').textContent = summary.drifted_devices || 0;

        const select = document.getElementById('change-device');
        if (select.options.length === 1) {
            for (const item of data.devices || []) {
                const option = document.createElement('option');
                option.value = item.id;
                option.textContent = `${item.name} (${item.ip_address})`;
                select.appendChild(option);
            }
            if (device) select.value = device;
        }
        renderConfigurationChanges();
    } catch (error) {
        document.getElementById('change-table').innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(error.message)}</td></tr>`;
        showToast(`加载配置变更失败：${error.message}`, 'error');
    }
}

function renderConfigurationChanges() {
    const body = document.getElementById('change-table');
    if (!changeItems.length) {
        body.innerHTML = '<tr><td colspan="6" class="empty-state">没有符合条件的配置变更</td></tr>';
        return;
    }
    body.innerHTML = changeItems.map(item => {
        const baseline = item.is_baseline
            ? '<span class="badge badge-primary">当前基线</span>'
            : (item.device_drifted ? '<span class="badge badge-danger">偏离基线</span>' : '<span class="badge badge-success">符合基线</span>');
        const reviewer = item.reviewed_by
            ? `${escapeHtml(item.reviewed_by)}<br><small>${formatDateTime(item.reviewed_at)}</small>` : '-';
        const note = item.review_note ? `<div title="${escapeAttr(item.review_note)}" style="max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(item.review_note)}</div>` : '';
        return `<tr>
            <td><strong>${escapeHtml(item.device_name)}</strong><br><code>${escapeHtml(item.device_ip)}</code></td>
            <td>${formatDateTime(item.backup_time)}${item.is_latest ? '<br><span class="badge badge-info">最新</span>' : ''}</td>
            <td>${changeStatusBadge(item.review_status)}</td><td>${baseline}</td>
            <td>${reviewer}${note}</td>
            <td style="white-space:nowrap;">
                <button class="btn btn-sm btn-outline" data-action="diff" data-id="${item.id}">差异</button>
                <button class="btn btn-sm btn-primary" data-action="review" data-id="${item.id}">处理</button>
                <button class="btn btn-sm btn-outline" data-action="baseline" data-id="${item.id}">设为基线</button>
            </td></tr>`;
    }).join('');
}

async function showChangeDiff(id) {
    const item = changeItems.find(row => row.id === id);
    if (!item || !item.previous_backup_id) {
        showToast('该记录没有可比较的上一版本', 'warning');
        return;
    }
    document.getElementById('change-diff-modal').classList.add('active');
    document.getElementById('change-diff-title').textContent = `配置差异 - ${item.device_name}`;
    const content = document.getElementById('change-diff-content');
    content.textContent = '加载中...';
    try {
        const result = await API.get(`/api/backups/diff/${item.previous_backup_id}/${item.id}`);
        content.textContent = result.diff || '两个版本没有文本差异';
    } catch (error) {
        content.textContent = `加载失败：${error.message}`;
    }
}

function openChangeReview(id) {
    const item = changeItems.find(row => row.id === id);
    if (!item) return;
    document.getElementById('change-review-id').value = id;
    document.getElementById('change-review-status').value = item.review_status || 'expected';
    document.getElementById('change-review-note').value = item.review_note || '';
    document.getElementById('change-review-modal').classList.add('active');
}

async function saveChangeReview() {
    const id = Number(document.getElementById('change-review-id').value);
    const status = document.getElementById('change-review-status').value;
    const note = document.getElementById('change-review-note').value;
    try {
        await API.post(`/api/backups/${id}/review`, {status, note});
        closeChangeModal('change-review-modal');
        showToast('变更处理结果已保存', 'success');
        await loadConfigurationChanges();
    } catch (error) {
        showToast(`保存失败：${error.message}`, 'error');
    }
}

async function setChangeBaseline(id) {
    if (!confirm('设为基线后，该设备将以此版本判断当前配置是否漂移。确定继续吗？')) return;
    try {
        await API.post(`/api/backups/${id}/baseline`);
        showToast('配置基线已更新', 'success');
        await loadConfigurationChanges();
    } catch (error) {
        showToast(`设置基线失败：${error.message}`, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('change-filter').addEventListener('click', loadConfigurationChanges);
    document.getElementById('change-refresh').addEventListener('click', loadConfigurationChanges);
    document.getElementById('change-review-save').addEventListener('click', saveChangeReview);
    document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => closeChangeModal(button.dataset.close)));
    document.getElementById('change-table').addEventListener('click', event => {
        const button = event.target.closest('button[data-action]');
        if (!button) return;
        const id = Number(button.dataset.id);
        if (button.dataset.action === 'diff') showChangeDiff(id);
        if (button.dataset.action === 'review') openChangeReview(id);
        if (button.dataset.action === 'baseline') setChangeBaseline(id);
    });
    loadConfigurationChanges();
});
