// Inventory-only workflows. No runtime credentials are exposed here.
const inventoryTypeLabels = {};
async function loadDeviceTypeFilter() {
    try {
        const data = await API.get('/api/devices/catalog/types');
        const el = document.getElementById('typeFilter');
        for (const item of data.device_types) {
            inventoryTypeLabels[item.key] = item.label;
            el.add(new Option(item.label, item.key));
        }
        loadDevices();
    } catch (e) { showToast('类型加载失败：' + e.message, 'error'); }
}

async function populateDeviceCatalogs(prefix, type, company) {
    try {
        const [types, companies] = await Promise.all([API.get('/api/devices/catalog/types'), API.get('/api/devices/companies')]);
        const replace = (id, entries, current, emptyLabel) => {
            const old = document.getElementById(id);
            if (!old) return;
            const select = document.createElement('select');
            select.id = id;
            select.className = 'form-control';
            if (emptyLabel) select.add(new Option(emptyLabel, ''));
            for (const [value, label] of entries) select.add(new Option(label, value));
            if (current && !Array.from(select.options).some(o => o.value === current)) select.add(new Option(current + '（现有值）', current));
            select.value = current || '';
            old.replaceWith(select);
        };
        replace(prefix + '-type', types.device_types.map(t => [t.key, t.label]), type);
        const typeSelect = document.getElementById(prefix + '-type');
        if (typeSelect) {
            const hint = document.createElement('small');
            hint.className = 'text-muted';
            hint.style.display = 'block';
            typeSelect.insertAdjacentElement('afterend', hint);
            const updateHint = () => { hint.textContent = types.device_types.find(t => t.key === typeSelect.value)?.note || '请维护该类型的连接驱动与命令。'; };
            typeSelect.addEventListener('change', updateHint);
            updateHint();
        }
        replace(prefix + '-company', companies.filter(c => c.company).map(c => [c.company, c.company]), company, '未指定公司');
    } catch (e) { showToast('下拉选项加载失败：' + e.message, 'error'); }
}

async function manageCompanies() {
    try {
        const companies = await API.get('/api/devices/companies');
        document.getElementById('modalContainer').innerHTML = `<div class="modal-overlay active"><div class="modal"><div class="modal-header"><h3>维护公司下拉框</h3></div><div class="modal-body"><p>每行一个公司名称。被设备使用的公司不能移除；现有设备配置不会改变。</p><textarea class="form-control" id="companyNames" rows="12"></textarea></div><div class="modal-footer"><button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button><button class="btn btn-primary" onclick="saveCompanyCatalog()">保存</button></div></div></div>`;
        document.getElementById('companyNames').value = companies.map(c => c.company).filter(Boolean).join('\n');
    } catch (e) { showToast(e.message, 'error'); }
}

async function saveCompanyCatalog() {
    try {
        await API.put('/api/devices/companies', {names: document.getElementById('companyNames').value.split('\n')});
        document.getElementById('modalContainer').innerHTML = '';
        await loadCompanies();
        showToast('公司下拉框已保存', 'success');
    } catch (e) { showToast(e.message, 'error'); }
}

function resetDeviceFilters() {
    for (const id of ['searchInput', 'companyFilter', 'groupFilter', 'statusFilter', 'typeFilter']) document.getElementById(id).value = '';
    loadDevices();
}

async function deleteSelectedDevices() {
    const ids = getSelectedIds();
    if (!ids.length) return showToast('请先选择设备', 'warning');
    if (!confirm(`删除选中的 ${ids.length} 台设备及其备份、采集和邻居记录？\n只影响本平台记录，不会操作真实设备。此操作不可撤销，请先做好数据备份。`)) return;
    try {
        const result = await API.post('/api/devices/batch-delete', {ids});
        showToast(`已删除 ${result.deleted} 台设备`, 'success');
        await loadDevices();
        await loadCompanies();
    } catch (e) { showToast(e.message, 'error'); }
}

async function showDeviceNeighbors(id, refresh = false) {
    try {
        if (refresh) {
            showToast('正在读取该设备的 CDP / LLDP 邻居…', 'info');
            const result = await API.post(`/api/devices/${id}/neighbors/discover`);
            if (!result.success) throw new Error(result.error || '邻居发现失败');
        }
        const rows = await API.get(`/api/devices/${id}/neighbors`);
        document.getElementById('modalContainer').innerHTML = `<div class="modal-overlay active"><div class="modal" style="width:1000px;max-width:95vw"><div class="modal-header"><h3>选择邻居加入设备管理</h3></div><div class="modal-body"><p>仅扫描本台设备，不递归、不自动添加。电话及未知设备默认只建档，不参与自动 SSH 任务。</p><button class="btn btn-primary" onclick="showDeviceNeighbors(${id},true)">刷新 CDP / LLDP</button><label><input type="checkbox" onchange="document.querySelectorAll('.neighbor-choice:not(:disabled)').forEach(c=>c.checked=this.checked)"> 全选可添加邻居</label><table class="data-table"><thead><tr><th>选择</th><th>名称 / IP</th><th>平台</th><th>自动分组</th><th>协议</th></tr></thead><tbody>${rows.map(n => `<tr><td><input class="neighbor-choice" type="checkbox" value="${n.id}" ${n.managed || !n.ip ? 'disabled' : ''}>${n.managed_device_id ? `<a href="/devices/${n.managed_device_id}">进入设备继续发现</a>` : !n.ip ? '缺少IP' : ''}</td><td>${escapeHtml(n.name || '')}<br>${escapeHtml(n.ip || '')}</td><td>${escapeHtml(n.platform || '')}</td><td>${escapeHtml(n.category)}</td><td>${escapeHtml(n.protocol)}</td></tr>`).join('') || '<tr><td colspan="5">暂无邻居，请点击刷新</td></tr>'}</tbody></table></div><div class="modal-footer"><button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">关闭</button><button class="btn btn-primary" onclick="addChosenNeighbors(${id})">添加选中邻居</button></div></div></div>`;
    } catch (e) { showToast(e.message, 'error'); }
}

async function addChosenNeighbors(id) {
    const ids = Array.from(document.querySelectorAll('.neighbor-choice:checked:not(:disabled)')).map(c => Number(c.value));
    if (!ids.length) return showToast('请选择需要添加的邻居', 'warning');
    try {
        const result = await API.post(`/api/devices/${id}/neighbors/add`, {ids});
        showToast(`新增 ${result.added_count} 台，跳过 ${result.skipped_count} 台${result.errors.length ? '；' + result.errors.join('；') : ''}`, result.errors.length ? 'warning' : 'success');
        await showDeviceNeighbors(id);
        if (document.getElementById('groupFilter')) {
            document.getElementById('groupFilter').innerHTML = '<option value="">全部分组</option>';
            await loadGroups();
            await loadDevices();
        }
        if (typeof loadNeighbors === 'function') await loadNeighbors();
    } catch (e) { showToast(e.message, 'error'); }
}
