let integrationInventory = null;
let activeIntegrationSource = '';

function integrationStatus(source, message, isError = false) {
    const element = document.getElementById(`${source}-status`);
    element.textContent = message;
    element.style.color = isError ? '#c0392b' : '#287b43';
}

function collectIntegrationConfig(source) {
    if (source === 'zabbix') {
        return {
            url: document.getElementById('zabbix-url').value.trim(),
            username: document.getElementById('zabbix-username').value.trim(),
            password: document.getElementById('zabbix-password').value,
            verify_ssl: document.getElementById('zabbix-verify').checked,
            timeout: Number(document.getElementById('zabbix-timeout').value || 20),
            source_ip: document.getElementById('zabbix-source-ip').value,
        };
    }
    return {
        host: document.getElementById('vcenter-host').value.trim(),
        port: Number(document.getElementById('vcenter-port').value || 443),
        username: document.getElementById('vcenter-username').value.trim(),
        password: document.getElementById('vcenter-password').value,
        verify_ssl: document.getElementById('vcenter-verify').checked,
        timeout: Number(document.getElementById('vcenter-timeout').value || 30),
    };
}

async function loadIntegrationConfig() {
    try {
        const configs = await API.get('/api/integrations/config');
        for (const source of ['zabbix', 'vcenter']) {
            const config = configs[source] || {};
            for (const key of Object.keys(config)) {
                const suffix = key === 'verify_ssl' ? 'verify' : key.replaceAll('_', '-');
                const element = document.getElementById(`${source}-${suffix}`);
                if (!element || key === 'password' || key === 'password_configured') continue;
                if (element.type === 'checkbox') element.checked = Boolean(config[key]);
                else {
                    if (element.tagName === 'SELECT' && config[key] &&
                        !Array.from(element.options).some(option => option.value === config[key])) {
                        element.add(new Option(`${config[key]}（已保存，当前未检测到）`, config[key]));
                    }
                    element.value = config[key] ?? '';
                }
            }
            if (config.password_configured) {
                document.getElementById(`${source}-password`).placeholder = '密码已保存；留空保持不变';
                integrationStatus(source, '已保存连接配置');
            }
        }
    } catch (error) {
        showToast(`加载集成配置失败：${error.message}`, 'error');
    }
}

async function loadIntegrationNICs() {
    const select = document.getElementById('zabbix-source-ip');
    try {
        const data = await API.get('/api/integrations/network-interfaces');
        select.innerHTML = '<option value="">自动选择（系统路由）</option>' +
            (data.interfaces || [])
                .filter(nic => !nic.is_loopback)
                .map(nic => `<option value="${escapeAttr(nic.ip_address)}">${escapeHtml(nic.name)} — ${escapeHtml(nic.ip_address)}${nic.is_up ? '' : '（未连接）'}</option>`)
                .join('');
    } catch (error) {
        select.innerHTML = '<option value="">自动选择（网卡列表加载失败）</option>';
        showToast(`加载出口网卡失败：${error.message}`, 'error');
    }
}

async function saveIntegrationConfig(source, quiet = false) {
    const config = collectIntegrationConfig(source);
    const result = await API.put(`/api/integrations/${source}/config`, config);
    document.getElementById(`${source}-password`).value = '';
    if (result.password_configured) document.getElementById(`${source}-password`).placeholder = '密码已保存；留空保持不变';
    integrationStatus(source, '配置保存成功');
    if (!quiet) showToast(`${source} 配置已保存`, 'success');
}

async function runIntegrationAction(source, action, button) {
    button.disabled = true;
    const oldText = button.textContent;
    try {
        if (action === 'save') {
            await saveIntegrationConfig(source);
        } else if (action === 'test') {
            await saveIntegrationConfig(source, true);
            integrationStatus(source, '正在测试连接…');
            const result = await API.post(`/api/integrations/${source}/test`);
            integrationStatus(source, result.message || '连接成功');
            showToast(result.message || '连接成功', 'success');
        } else if (action === 'discover' || action === 'preview') {
            await saveIntegrationConfig(source, true);
            integrationStatus(source, action === 'preview' ? '正在计算完整差异…' : '正在获取虚拟机与存储清单…');
            const result = await API.post(`/api/integrations/${source}/${action}`);
            activeIntegrationSource = source;
            integrationInventory = result;
            renderIntegrationInventory(result);
            integrationStatus(source, `获取完成：${result.summary.vms} 台虚拟机，${result.summary.storage} 个存储`);
        } else if (action === 'sync') {
            await saveIntegrationConfig(source, true);
            if (!confirm(`全量同步 ${source}？新增和变更会写入，源端已消失的资产只标记失联，不会删除。`)) return;
            integrationStatus(source, '正在执行全量同步…');
            const result = await API.post(`/api/integrations/${source}/sync`, {
                external_ids: null, update_existing: true, mark_missing: true,
            });
            integrationStatus(source, result.message);
            showToast(result.message, 'success');
            await loadSyncHistory();
            const preview = await API.post(`/api/integrations/${source}/preview`);
            activeIntegrationSource = source;
            integrationInventory = preview;
            renderIntegrationInventory(preview);
        }
    } catch (error) {
        integrationStatus(source, error.message, true);
        showToast(`${source} 操作失败：${error.message}`, 'error');
    } finally {
        button.disabled = false;
        button.textContent = oldText;
    }
}

function renderIntegrationInventory(result) {
    document.getElementById('inventory-panel').style.display = '';
    document.getElementById('inventory-title').textContent = `${result.source === 'vcenter' ? 'VMware vCenter' : 'Zabbix'} 发现结果`;
    const diff = result.diff_summary || {};
    document.getElementById('inventory-summary').textContent = `版本 ${result.version || '-'} · 获取时间 ${formatDateTime(result.fetched_at)} · 虚拟机/主机 ${result.summary.vms} · 存储 ${result.summary.storage} · 新增 ${diff.new || 0} · 变更 ${diff.update || 0} · 未变化 ${diff.unchanged || 0} · 失联 ${diff.stale || 0}${result.truncated ? ' · ⚠ 清单超过 2000 条，本次不会标记失联' : ''}`;
    const vmBody = document.getElementById('integration-vm-body');
    if (!result.vms.length) {
        vmBody.innerHTML = '<tr><td colspan="8" class="empty-state">没有发现可导入的虚拟机或主机</td></tr>';
    } else {
        vmBody.innerHTML = result.vms.map((vm) => {
            if (vm.error) return `<tr><td></td><td>${escapeHtml(vm.name)}</td><td colspan="6" style="color:#c0392b;">读取失败：${escapeHtml(vm.error)}</td></tr>`;
            const checked = vm.change_type === 'unchanged' ? '' : 'checked';
            const disks = (vm.disks || []).map((disk) => `${disk.name}: ${disk.size}`).join('；');
            const changeLabels = (vm.changed_fields || []).map((field) => field.label).join('、');
            const protectedLabels = (vm.protected_fields || []).join('、');
            const changeBadge = vm.change_type === 'new'
                ? '<span class="badge badge-success">新增</span>'
                : (vm.change_type === 'update'
                    ? `<span class="badge badge-warning">变更</span><br><small>${escapeHtml(changeLabels)}</small>`
                    : '<span class="badge badge-secondary">无变化</span>');
            return `<tr>
                <td><input class="integration-vm-check" type="checkbox" value="${escapeAttr(vm.external_id)}" ${checked}></td>
                <td><strong>${escapeHtml(vm.name)}</strong><br><small>${escapeHtml(vm.external_id)}</small></td>
                <td>${escapeHtml(vm.status || '-')}</td>
                <td>${escapeHtml(vm.os_version || vm.os_type || '-')}<br><code>${escapeHtml(vm.management_ip || '-')}</code></td>
                <td>${escapeHtml(vm.host_name || '-')}</td>
                <td>${escapeHtml(vm.cpu || '-')} / ${escapeHtml(vm.memory || '-')}</td>
                <td>${escapeHtml(vm.disk_size || '-')}<br><small>${escapeHtml(vm.storage_lun || disks || '-')}</small></td>
                <td>${changeBadge}${protectedLabels ? `<br><small style="color:#287b43;">已保护：${escapeHtml(protectedLabels)}</small>` : ''}${vm.imported ? `<br><small>本地 #${vm.local_id}</small>` : ''}</td>
            </tr>`;
        }).join('');
    }
    const storageBody = document.getElementById('integration-storage-body');
    storageBody.innerHTML = result.storage.length ? result.storage.map((storage) => `<tr>
        <td><strong>${escapeHtml(storage.name)}</strong></td><td>${escapeHtml(storage.type || '-')}</td>
        <td>${escapeHtml(storage.capacity || '-')}</td><td>${escapeHtml(storage.used_space || '-')}</td>
        <td>${escapeHtml(storage.free_space || '-')}</td><td>${Number(storage.vm_count || 0)}</td>
        <td>${storage.accessible ? '<span class="badge badge-success">可访问</span>' : '<span class="badge badge-danger">不可访问</span>'} ${storage.change_type ? `<span class="badge badge-info">${escapeHtml(storage.change_type)}</span>` : ''}</td>
    </tr>`).join('') : '<tr><td colspan="7" class="empty-state">当前数据源未返回独立存储清单</td></tr>';
    const missing = result.missing_vms || [];
    const missingStorage = result.missing_storage || [];
    document.getElementById('missing-panel').style.display = (missing.length || missingStorage.length) ? '' : 'none';
    document.getElementById('missing-vm-list').innerHTML = missing.map((vm) =>
        `<span class="badge badge-warning" style="margin:2px;">VM：${escapeHtml(vm.name)} · ${escapeHtml(vm.external_id)}</span>`
    ).join('');
    document.getElementById('missing-storage-list').innerHTML = missingStorage.map((row) =>
        `<span class="badge badge-warning" style="margin:2px;">存储：${escapeHtml(row.name)} · ${escapeHtml(row.external_id)}</span>`
    ).join('');
    const selectable = Array.from(document.querySelectorAll('.integration-vm-check'));
    document.getElementById('select-all-vms').checked = selectable.length > 0 && selectable.every((item) => item.checked);
    document.getElementById('inventory-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function importSelectedInventory() {
    const selected = Array.from(document.querySelectorAll('.integration-vm-check:checked')).map((element) => element.value);
    if (!selected.length) {
        showToast('请至少选择一条虚拟机记录', 'warning');
        return;
    }
    const button = document.getElementById('import-selected');
    button.disabled = true;
    try {
        const result = await API.post(`/api/integrations/${activeIntegrationSource}/import`, {
            external_ids: selected,
            update_existing: document.getElementById('update-existing').checked,
        });
        showToast(result.message, 'success');
        for (const vm of integrationInventory.vms) {
            if (selected.includes(String(vm.external_id))) {
                vm.imported = true;
                vm.local_id = result.imported_ids[String(vm.external_id)] || vm.local_id;
            }
        }
        renderIntegrationInventory(integrationInventory);
        loadSyncHistory();
    } catch (error) {
        showToast(`导入失败：${error.message}`, 'error');
    } finally {
        button.disabled = false;
    }
}

document.querySelectorAll('[data-action][data-source]').forEach((button) => {
    button.addEventListener('click', () => runIntegrationAction(button.dataset.source, button.dataset.action, button));
});
document.getElementById('select-all-vms').addEventListener('change', (event) => {
    document.querySelectorAll('.integration-vm-check').forEach((checkbox) => { checkbox.checked = event.target.checked; });
});
document.getElementById('import-selected').addEventListener('click', importSelectedInventory);
async function initializeIntegrations() {
    await loadIntegrationNICs();
    await loadIntegrationConfig();
}
initializeIntegrations();
loadSyncHistory();

async function loadSyncHistory() {
    const body = document.getElementById('sync-history-body');
    try {
        const rows = await API.get('/api/integrations/sync-runs?limit=30');
        body.innerHTML = rows.length ? rows.map((row) => `<tr>
            <td>${formatDateTime(row.started_at)}</td><td>${escapeHtml(row.source_type)}</td>
            <td>${row.mode === 'scheduled' ? '定时' : '手动'}</td>
            <td>${row.status === 'success' ? '<span class="badge badge-success">成功</span>' : `<span class="badge badge-danger">${escapeHtml(row.status)}</span>`}</td>
            <td>VM ${row.discovered_vms} / 存储 ${row.discovered_storage}</td>
            <td>新增 ${row.created} · 更新 ${row.updated} · 失联 ${row.stale}</td>
            <td>${row.attempt}/${row.max_attempts}</td><td><small>${escapeHtml(row.error_message || '-')}</small></td>
        </tr>`).join('') : '<tr><td colspan="8" class="empty-state">暂无同步历史</td></tr>';
    } catch (error) {
        body.innerHTML = `<tr><td colspan="8" class="empty-state">加载失败：${escapeHtml(error.message)}</td></tr>`;
    }
}
