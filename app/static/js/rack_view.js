/* Both rack pages use one physical row for a U label and its occupied slot. */
function rackUHTML(rack, servers) {
    const height = Math.max(1, Number(rack.u_height) || 1);
    const slots = Array.from({length: height + 1}, () => []);
    const invalid = [];
    for (const server of servers || []) {
        const start = Number(server.u_start), size = Number(server.u_size);
        if (!Number.isInteger(start) || !Number.isInteger(size) || start < 1 || size < 1 || start + size - 1 > height) {
            invalid.push(server.name || '未命名设备');
            continue;
        }
        for (let unit = start; unit < start + size; unit++) slots[unit].push(server);
    }
    let rows = '';
    for (let unit = height; unit >= 1; unit--) {
        const entries = slots[unit];
        const conflict = entries.length > 1;
        const color = conflict ? '#dc2626' : entries[0]?.category === '存储' ? '#8b5cf6' : '#3b82f6';
        const names = entries.map(item => item.name || '未命名设备').join(' / ');
        const title = `U${unit}` + (entries.length ? ` · ${names}` : ' · 空闲') + (conflict ? ' · 占用冲突' : '');
        rows += `<div data-rack-u="${unit}" style="display:grid;grid-template-columns:32px minmax(0,1fr);height:18px;gap:4px;align-items:stretch;">
            <span style="font-size:10px;color:#64748b;text-align:right;line-height:18px;">U${unit}</span>
            <div data-occupied="${entries.length > 0}" title="${escapeHtml(title)}" style="box-sizing:border-box;min-width:0;margin:0;border:1px solid ${entries.length ? color : '#e2e8f0'};border-radius:2px;background:${entries.length ? color : '#f1f5f9'};color:#fff;font-size:10px;line-height:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 3px;">${escapeHtml(names)}</div>
        </div>`;
    }
    return `<div style="width:220px;border:1px solid #e6eaf0;border-radius:10px;padding:10px;background:#fff;">
        <div style="text-align:center;font-weight:700;">${escapeHtml(rack.rack_number || '')}</div>
        <div style="text-align:center;color:#888;font-size:12px;">${escapeHtml(rack.name || '')}</div>
        <div style="display:grid;gap:1px;margin-top:6px;">${rows}</div>
        ${invalid.length ? `<div style="color:#dc2626;font-size:12px;">U 位超界或无效：${invalid.map(escapeHtml).join('、')}</div>` : ''}
        <div style="text-align:center;margin-top:6px;font-size:11px;color:#666;">${height}U · ${escapeHtml(rack.site_name || '')} · 底部为 U1</div>
    </div>`;
}
