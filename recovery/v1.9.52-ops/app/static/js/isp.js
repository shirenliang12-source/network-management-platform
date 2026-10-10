(() => {
    'use strict';
    const el = id => document.getElementById('isp-' + id);
    let rows = [], editing = null, generation = 0;
    let groups = {company: [], function: [], region: []};
    let dashboardWidgets = ['line_count', 'by_purpose', 'by_location'];
    const widgetLabels = {
        line_count: '线路数量', by_purpose: '线路用途', by_location: '使用位置',
        by_provider: '运营商', by_company_group: '公司分组',
        by_function_group: '功能分组', by_region_group: '区域分组',
        by_bandwidth: '带宽合计',
    };
    const lines = value => String(value || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    const options = values => values.map(value =>
        '<option value="' + escapeAttr(value) + '">' + escapeHtml(value) + '</option>').join('');

    function setSelect(id, values, firstLabel) {
        const select = el(id), previous = select.value;
        select.innerHTML = '<option value="">' + escapeHtml(firstLabel) + '</option>' + options(values);
        if (values.includes(previous)) select.value = previous;
    }

    async function loadFilterOptions() {
        const result = await API.get('/api/isp/filter-options');
        groups = {company: result.company || [], function: result.function || [], region: result.region || []};
        setSelect('filter-purpose', result.purpose || [], '全部用途');
        setSelect('filter-location', result.location || [], '全部位置');
        setSelect('filter-company', groups.company, '全部公司');
        setSelect('filter-function', groups.function, '全部功能');
        setSelect('filter-region', groups.region, '全部区域');
    }

    function fillEditorGroups(row) {
        for (const kind of ['company', 'function', 'region']) {
            const select = el(kind + '-group');
            const value = row ? row[kind + '_group'] || '' : '';
            const catalog = groups[kind].includes(value) || !value ? groups[kind] : groups[kind].concat([value]);
            select.innerHTML = '<option value="">未分组</option>' + options(catalog);
            select.value = value;
        }
    }

    function bandwidthText(value) {
        const mbps = Number(value || 0);
        if (!mbps) return '-';
        return mbps >= 1000 && mbps % 1000 === 0 ? (mbps / 1000) + ' Gbps' : mbps + ' Mbps';
    }

    function distribution(field) {
        const counts = new Map();
        for (const row of rows) {
            const key = String(row[field] || '未填写');
            counts.set(key, (counts.get(key) || 0) + 1);
        }
        return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    }

    function dashboardCard(title, body, large) {
        return '<div style="border:1px solid #e5e7eb;border-radius:9px;padding:14px;background:#fff;">' +
            '<div style="font-size:13px;color:#64748b;">' + escapeHtml(title) + '</div>' +
            '<div style="' + (large ? 'font-size:30px;font-weight:700;margin-top:8px;' : 'margin-top:8px;') + '">' + body + '</div></div>';
    }

    function renderDashboard() {
        const cards = [];
        for (const widget of dashboardWidgets) {
            if (widget === 'line_count') {
                cards.push(dashboardCard(widgetLabels[widget], String(rows.length), true));
                continue;
            }
            if (widget === 'by_bandwidth') {
                const total = rows.reduce((sum, row) => sum + Number(row.bandwidth_mbps || 0), 0);
                cards.push(dashboardCard(widgetLabels[widget], escapeHtml(bandwidthText(total)), true));
                continue;
            }
            const fields = {
                by_purpose: 'purpose', by_location: 'location', by_provider: 'provider',
                by_company_group: 'company_group', by_function_group: 'function_group',
                by_region_group: 'region_group',
            };
            const values = distribution(fields[widget]);
            const body = values.length
                ? values.slice(0, 8).map(item => '<div style="display:flex;justify-content:space-between;gap:8px;"><span>' +
                    escapeHtml(item[0]) + '</span><strong>' + item[1] + '</strong></div>').join('')
                : '<span style="color:#999;">暂无数据</span>';
            cards.push(dashboardCard(widgetLabels[widget], body, false));
        }
        el('dashboard').innerHTML = cards.join('');
    }

    async function loadDashboardSettings() {
        const result = await API.get('/api/isp/dashboard-settings');
        dashboardWidgets = result.widgets;
        el('dashboard-options').innerHTML = Object.entries(widgetLabels).map(item =>
            '<label style="margin-right:12px;"><input type="checkbox" class="isp-dashboard-widget" value="' +
            escapeAttr(item[0]) + '"' + (dashboardWidgets.includes(item[0]) ? ' checked' : '') + '> ' +
            escapeHtml(item[1]) + '</label>').join('');
    }

    async function load() {
        const current = ++generation;
        const params = new URLSearchParams();
        const filters = [
            ['q', 'search'], ['purpose', 'filter-purpose'], ['location', 'filter-location'],
            ['company_group', 'filter-company'], ['function_group', 'filter-function'],
            ['region_group', 'filter-region'],
        ];
        for (const item of filters) {
            const value = el(item[1]).value.trim();
            if (value) params.set(item[0], value);
        }
        try {
            const result = await API.get('/api/isp?' + params.toString());
            if (current !== generation) return;
            rows = result;
            el('summary').textContent = '当前显示 ' + rows.length + ' 条线路；地址统计按线路分别计算。' +
                (rows.length ? (rows[0].ipam_linked ? ' 已自动合并 IPAM 使用状态。' : ' 未获 IPAM 权限：当前仅为手工统计。') : '');
            el('rows').innerHTML = rows.map(row => '<tr>' +
                [row.provider, row.circuit_number, bandwidthText(row.bandwidth_mbps), row.networks.join('\n'),
                 row.company_group || '-', row.function_group || '-', row.region_group || '-',
                 row.purpose || '-', row.location || '-', row.usable_count, row.used_count,
                 row.reserved_count, row.available_count, row.utilization + '%']
                    .map(value => '<td style="white-space:pre-wrap">' + escapeHtml(value) + '</td>').join('') +
                '<td><button class="btn" data-addresses="' + row.id + '">IP地址明细</button> ' +
                '<button class="btn" data-edit="' + row.id + '">查看 / 编辑</button> ' +
                '<button class="btn btn-danger" data-delete="' + row.id + '">删除</button></td></tr>').join('') ||
                '<tr><td colspan="15" class="empty-state">暂无线路，请新增或导入。</td></tr>';
            renderDashboard();
        } catch (error) {
            el('summary').textContent = '加载失败：' + error.message;
        }
    }

    function edit(row) {
        editing = row ? row.id : null;
        el('form').reset();
        el('error').textContent = '';
        el('editor-title').textContent = row ? '查看 / 编辑线路' : '新增线路';
        el('linked').textContent = row && row.ipam_linked ?
            'IPAM自动关联（只读，修改请到IP地址规划）：\n已用：' + (row.ipam_used_ips.join('、') || '无') +
            '\n预留：' + (row.ipam_reserved_ips.join('、') || '无') :
            '手工补充记录；保存并刷新后按网段重新关联IPAM（需IPAM权限）。';
        const mapping = {
            provider: 'provider', number: 'circuit_number', bandwidth: 'bandwidth_mbps',
            purpose: 'purpose', location: 'location', notes: 'notes',
            networks: 'networks', used: 'used_ips', reserved: 'reserved_ips',
        };
        for (const item of Object.entries(mapping)) {
            const value = row ? row[item[1]] : '';
            el(item[0]).value = Array.isArray(value) ? value.join('\n') : (value ?? '');
        }
        fillEditorGroups(row);
        el('editor').hidden = false;
        el('provider').focus();
    }

    el('form').addEventListener('submit', async event => {
        event.preventDefault();
        const payload = {
            provider: el('provider').value, circuit_number: el('number').value,
            bandwidth_mbps: Number(el('bandwidth').value || 0),
            company_group: el('company-group').value, function_group: el('function-group').value,
            region_group: el('region-group').value, purpose: el('purpose').value,
            location: el('location').value, notes: el('notes').value,
            networks: lines(el('networks').value), used_ips: lines(el('used').value),
            reserved_ips: lines(el('reserved').value),
        };
        el('save').disabled = true;
        el('error').textContent = '';
        try {
            if (editing === null) await API.post('/api/isp', payload);
            else await API.put('/api/isp/' + editing, payload);
            el('editor').hidden = true;
            showToast('线路已保存', 'success');
            await loadFilterOptions();
            await load();
        } catch (error) {
            el('error').textContent = error.message;
        } finally {
            el('save').disabled = false;
        }
    });

    el('rows').addEventListener('click', async event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.dataset.addresses) window.openIspAddresses(Number(button.dataset.addresses));
        if (button.dataset.edit) edit(rows.find(row => row.id === Number(button.dataset.edit)));
        if (button.dataset.delete) {
            const row = rows.find(item => item.id === Number(button.dataset.delete));
            if (!row || !confirm('删除线路“' + row.circuit_number + '”？只删除该线路台账，不删除IPAM数据。')) return;
            button.disabled = true;
            try {
                await API.delete('/api/isp/' + row.id);
                if (editing === row.id) el('editor').hidden = true;
                await loadFilterOptions(); await load();
            } catch (error) {
                showToast(error.message, 'error'); button.disabled = false;
            }
        }
    });

    async function openGroups() {
        groups = await API.get('/api/isp/groups');
        for (const kind of ['company', 'function', 'region']) {
            el(kind + '-groups').value = (groups[kind] || []).join('\n');
        }
        el('groups-error').textContent = '';
        el('groups').hidden = false;
    }

    el('groups-save').addEventListener('click', async () => {
        const payload = {
            company: lines(el('company-groups').value),
            function: lines(el('function-groups').value),
            region: lines(el('region-groups').value),
        };
        try {
            groups = await API.put('/api/isp/groups', payload);
            showToast('ISP分组已保存', 'success');
            el('groups').hidden = true;
            await loadFilterOptions();
        } catch (error) {
            el('groups-error').textContent = error.message;
        }
    });

    el('dashboard-customize').addEventListener('click', () => {
        el('dashboard-settings').hidden = !el('dashboard-settings').hidden;
    });
    el('dashboard-save').addEventListener('click', async () => {
        const selected = Array.from(document.querySelectorAll('.isp-dashboard-widget:checked')).map(item => item.value);
        if (!selected.length) return showToast('至少选择一个仪表盘组件', 'warning');
        const result = await API.put('/api/isp/dashboard-settings', {widgets: selected});
        dashboardWidgets = result.widgets;
        el('dashboard-settings').hidden = true;
        renderDashboard();
        showToast('仪表盘已保存', 'success');
    });

    el('import').addEventListener('click', () => el('import-file').click());
    el('import-file').addEventListener('change', async () => {
        const file = el('import-file').files && el('import-file').files[0];
        if (!file) return;
        const mode = el('import-mode').value;
        if (mode === 'update' && !confirm('覆盖模式会更新同一运营商+线路编号的数据，确认继续？')) {
            el('import-file').value = ''; return;
        }
        const data = new FormData(); data.append('file', file);
        try {
            const response = await fetch('/api/isp/import?mode=' + encodeURIComponent(mode), {method: 'POST', body: data});
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail || result));
            showToast(result.message, 'success');
            await loadFilterOptions(); await load();
        } catch (error) {
            showToast('导入失败：' + error.message, 'error');
        } finally {
            el('import-file').value = '';
        }
    });

    el('add').addEventListener('click', () => edit(null));
    el('cancel').addEventListener('click', () => { el('editor').hidden = true; });
    el('groups-button').addEventListener('click', () => openGroups().catch(error => showToast(error.message, 'error')));
    el('groups-cancel').addEventListener('click', () => { el('groups').hidden = true; });
    el('refresh').addEventListener('click', load);
    el('filter-clear').addEventListener('click', () => {
        for (const id of ['search', 'filter-purpose', 'filter-location', 'filter-company', 'filter-function', 'filter-region']) el(id).value = '';
        load();
    });
    el('search').addEventListener('keydown', event => { if (event.key === 'Enter') load(); });
    for (const id of ['filter-purpose', 'filter-location', 'filter-company', 'filter-function', 'filter-region']) {
        el(id).addEventListener('change', load);
    }
    (async () => {
        try {
            await Promise.all([loadFilterOptions(), loadDashboardSettings()]);
            await load();
        } catch (error) {
            el('summary').textContent = '初始化失败：' + error.message;
        }
    })();
})();
