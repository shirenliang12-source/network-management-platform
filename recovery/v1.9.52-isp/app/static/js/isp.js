(() => {
    'use strict';
    const el = id => document.getElementById('isp-' + id);
    let rows = [], editing = null, generation = 0;
    const lines = value => value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    async function load() {
        const current = ++generation;
        try {
            const result = await API.get('/api/isp?q=' + encodeURIComponent(el('search').value));
            if (current !== generation) return;
            rows = result;
            el('summary').textContent = '当前显示 ' + rows.length + ' 条线路；地址统计按线路分别计算。' +
                (rows.length ? (rows[0].ipam_linked ? ' 已自动合并IPAM使用状态。' : ' 未获IPAM权限：当前仅为手工统计。') : '');
            el('rows').innerHTML = rows.map(row => '<tr>' +
                [row.provider, row.circuit_number, row.networks.join('\n'), row.usable_count,
                 row.used_count, row.reserved_count, row.available_count, row.utilization + '%', row.location]
                    .map(value => '<td style="white-space:pre-wrap">' + escapeHtml(value) + '</td>').join('') +
                '<td><button class="btn" data-edit="' + row.id + '">查看 / 编辑</button> ' +
                '<button class="btn" data-delete="' + row.id + '">删除</button></td></tr>').join('') ||
                '<tr><td colspan="10">暂无线路，请新增。</td></tr>';
        } catch (error) { el('summary').textContent = '加载失败：' + error.message; }
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
        const mapping = {provider:'provider', number:'circuit_number', location:'location', notes:'notes',
                         networks:'networks', used:'used_ips', reserved:'reserved_ips'};
        for (const [field, key] of Object.entries(mapping)) {
            const value = row ? row[key] : '';
            el(field).value = Array.isArray(value) ? value.join('\n') : value;
        }
        el('editor').hidden = false;
        el('provider').focus();
    }
    el('form').addEventListener('submit', async event => {
        event.preventDefault();
        const payload = {provider:el('provider').value, circuit_number:el('number').value,
            networks:lines(el('networks').value), used_ips:lines(el('used').value),
            reserved_ips:lines(el('reserved').value), location:el('location').value, notes:el('notes').value};
        el('save').disabled = true;
        el('error').textContent = '';
        try {
            if (editing === null) await API.post('/api/isp', payload);
            else await API.put('/api/isp/' + editing, payload);
            el('editor').hidden = true;
            showToast('线路已保存', 'success');
            await load();
        } catch (error) { el('error').textContent = error.message; }
        finally { el('save').disabled = false; }
    });
    el('rows').addEventListener('click', async event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.dataset.edit) edit(rows.find(row => row.id === Number(button.dataset.edit)));
        if (button.dataset.delete) {
            const row = rows.find(row => row.id === Number(button.dataset.delete));
            if (!row || !confirm('删除线路“' + row.circuit_number + '”？只删除该线路台账，不删除IPAM数据。')) return;
            button.disabled = true;
            try {
                await API.delete('/api/isp/' + row.id);
                if (editing === row.id) el('editor').hidden = true;
                await load();
            } catch (error) { showToast(error.message, 'error'); button.disabled = false; }
        }
    });
    el('add').addEventListener('click', () => edit(null));
    el('cancel').addEventListener('click', () => { el('editor').hidden = true; });
    el('refresh').addEventListener('click', load);
    el('search').addEventListener('keydown', event => { if (event.key === 'Enter') load(); });
    load();
})();
