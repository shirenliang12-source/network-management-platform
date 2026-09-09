async function loadSchedules() {
    try {
        const schedules = await API.get('/api/schedule');
        const body = document.getElementById('scheduleTable');
        if (schedules && schedules.length > 0) {
            body.innerHTML = schedules.map(s => `
                <tr>
                    <td>${escapeHtml(taskTypeLabel(s.task_type))}</td>
                    <td><code>${escapeHtml(s.cron_expression)}</code></td>
                    <td>${escapeHtml(s.description || '-')}</td>
                    <td>${s.is_enabled ? '<span class="badge badge-success">启用</span>' : '<span class="badge badge-secondary">禁用</span>'}</td>
                    <td>${formatRelative(s.last_run)}</td>
                    <td>
                        <button class="btn btn-sm btn-outline" onclick="toggleSchedule(${s.id})">${s.is_enabled ? '禁用' : '启用'}</button>
                        <button class="btn btn-sm btn-outline" onclick="editSchedule(${s.id})">编辑</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteSchedule(${s.id})">删除</button>
                    </td>
                </tr>
            `).join('');
        } else {
            body.innerHTML = '<tr><td colspan="6" class="empty-state">暂无定时任务</td></tr>';
        }
    } catch (e) {
        showToast('加载定时任务失败: ' + e.message, 'error');
    }
}

function formatBytes(value) {
    const n = Number(value || 0);
    if (n < 1024) return `${n} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let size = n / 1024;
    let unit = units[0];
    for (let i = 1; i < units.length && size >= 1024; i++) {
        size /= 1024;
        unit = units[i];
    }
    return `${size.toFixed(size >= 10 ? 1 : 2)} ${unit}`;
}

async function loadSystemHealth(full = false) {
    const summary = document.getElementById('system-health-summary');
    if (!summary) return;
    summary.textContent = full ? '正在执行完整数据库检查...' : '正在检查...';
    try {
        const h = await API.get(`/api/system/health${full ? '?full=true' : ''}`);
        const relationCount = (h.foreign_key_violations || []).length;
        summary.innerHTML = h.ok && h.relations_ok
            ? '<span class="badge badge-success">健康</span> 数据库完整，外键约束已启用且关系正常。'
            : (h.ok
                ? `<span class="badge badge-warning">需维护</span> 数据库可用，但检测到 ${relationCount} 条旧关联异常。`
                : `<span class="badge badge-danger">异常</span> ${escapeHtml(h.integrity || '数据库检查失败')}`);
        document.getElementById('health-schema').textContent = h.schema_revision || '-';
        document.getElementById('health-data-dir').textContent = h.data_dir || '-';
        document.getElementById('health-storage').textContent = `${formatBytes(h.database_size)} / ${formatBytes(h.disk_free)}`;
        const u = h.upgrade || {};
        document.getElementById('health-upgrade').textContent = u.status
            ? `${u.status === 'success' ? '成功' : (u.status === 'failed' ? '失败' : '进行中')} · ${u.version || '-'} · ${formatDateTime(u.completed_at || u.started_at)}`
            : '暂无升级记录';
        document.getElementById('health-backup').textContent = u.backup_path || '新安装或暂无快照';
    } catch (e) {
        summary.innerHTML = `<span class="badge badge-danger">检查失败</span> ${escapeHtml(e.message)}`;
    }
}

async function loadGroups() {
    try {
        const groups = await API.get('/api/devices/groups');
        const body = document.getElementById('groupTable');
        if (groups && groups.length > 0) {
            body.innerHTML = groups.map(g => `
                <tr>
                    <td><strong>${escapeHtml(g.name)}</strong></td>
                    <td>${escapeHtml(g.description || '-')}</td>
                    <td>${g.device_count}</td>
                    <td>
                        <button class="btn btn-sm btn-danger" onclick="deleteGroup(${g.id})">删除</button>
                    </td>
                </tr>
            `).join('');
        } else {
            body.innerHTML = '<tr><td colspan="4" class="empty-state">暂无分组</td></tr>';
        }
    } catch (e) {
        showToast('加载分组失败: ' + e.message, 'error');
    }
}

function taskTypeLabel(type) {
    const map = { 'backup': '配置备份', 'discovery': '邻居发现', 'info': '信息采集' };
    return map[type] || String(type || '');
}

// ---- Credential Profiles ----
function deviceTypeText(t) {
    const map = {
        'cisco_ios': 'Cisco IOS',
        'cisco_ios_xe': 'Cisco IOS-XE',
        'cisco_nxos': 'Cisco NX-OS',
        'cisco_wlc': 'Cisco WLC',
        'cisco_ap': 'Cisco AP',
    };
    return map[t] || String(t || '');
}

async function loadProfiles() {
    try {
        const profiles = await API.get('/api/credentials/profiles');
        const body = document.getElementById('profileTable');
        if (profiles && profiles.length > 0) {
            body.innerHTML = profiles.map(p => `
                <tr>
                    <td><strong>${escapeHtml(p.name)}</strong></td>
                    <td>${escapeHtml(deviceTypeText(p.device_type))}</td>
                    <td>${escapeHtml(p.username)}</td>
                    <td>${p.port}</td>
                    <td>${p.source_ip ? `<code style="font-size:11px;">${escapeHtml(p.source_ip)}</code>` : '<span style="color:#999;">自动</span>'}</td>
                    <td>${p.has_password ? '<span class="badge badge-success">已设置</span>' : '<span class="badge badge-secondary">未设置</span>'}</td>
                    <td style="font-size:12px;color:#666;">${escapeHtml(p.description || '-')}</td>
                    <td>
                        <button class="btn btn-sm btn-outline" onclick="editProfile(${p.id})">✏️</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteProfile(${p.id})">🗑️</button>
                    </td>
                </tr>
            `).join('');
        } else {
            body.innerHTML = '<tr><td colspan="8" class="empty-state">暂无凭据配置，点击右上角添加</td></tr>';
        }
    } catch (e) {
        showToast('加载凭据配置失败: ' + e.message, 'error');
    }
}

function showAddProfileModal() {
    const nicOptions = '<option value="">自动（系统默认路由）</option>';
    const modal = `
        <div class="modal-overlay active" onclick="if(event.target===this)this.classList.remove('active')">
            <div class="modal">
                <div class="modal-header"><h3>添加凭据配置</h3><button class="btn btn-sm btn-outline" onclick="this.closest('.modal-overlay').remove()">✕</button></div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>名称 *</label>
                        <input type="text" class="form-control" id="profile-name" placeholder="核心交换机凭据">
                    </div>
                    <div class="form-group">
                        <label>设备类型 *</label>
                        <select class="form-control" id="profile-device-type">
                            <option value="cisco_ios">Cisco IOS</option>
                            <option value="cisco_ios_xe">Cisco IOS-XE</option>
                            <option value="cisco_nxos">Cisco NX-OS</option>
                            <option value="cisco_wlc">Cisco WLC</option>
                            <option value="cisco_ap">Cisco AP</option>
                        </select>
                        <div style="font-size:11px;color:#999;margin-top:2px;">发现设备时按此类型匹配凭据</div>
                    </div>
                    <div class="form-group">
                        <label>用户名</label>
                        <input type="text" class="form-control" id="profile-username" value="admin">
                    </div>
                    <div class="form-group">
                        <label>密码</label>
                        <input type="password" class="form-control" id="profile-password">
                    </div>
                    <div class="form-group">
                        <label>Enable密码</label>
                        <input type="password" class="form-control" id="profile-enable-password">
                    </div>
                    <div class="form-group">
                        <label>SSH端口</label>
                        <input type="number" class="form-control" id="profile-port" value="22">
                    </div>
                    <div class="form-group">
                        <label>出口网卡 (源IP)</label>
                        <select class="form-control" id="profile-source-ip">
                            ${nicOptions}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>说明</label>
                        <input type="text" class="form-control" id="profile-desc" placeholder="核心层IOS-XE交换机专用账号">
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button>
                    <button class="btn btn-primary" onclick="submitAddProfile()">添加</button>
                </div>
            </div>
        </div>
    `;
    document.getElementById('modalContainer').innerHTML = modal;
    loadNICsForProfile('profile-source-ip');
}

async function loadNICsForProfile(selectId, currentValue) {
    try {
        const data = await API.get('/api/nics');
        const select = document.getElementById(selectId);
        if (!select) return;
        data.interfaces.forEach(nic => {
            if (nic.is_up && !nic.is_loopback) {
                const opt = document.createElement('option');
                opt.value = nic.ip_address;
                opt.textContent = `${nic.name} (${nic.ip_address})`;
                if ((currentValue || data.selected_ip) === nic.ip_address) opt.selected = true;
                select.appendChild(opt);
            }
        });
    } catch (e) { console.error('Failed to load NICs:', e); }
}

async function submitAddProfile() {
    const data = {
        name: document.getElementById('profile-name').value,
        device_type: document.getElementById('profile-device-type').value,
        username: document.getElementById('profile-username').value,
        password: document.getElementById('profile-password').value,
        enable_password: document.getElementById('profile-enable-password').value,
        port: parseInt(document.getElementById('profile-port').value),
        source_ip: document.getElementById('profile-source-ip').value,
        description: document.getElementById('profile-desc').value,
    };
    if (!data.name) { showToast('请输入名称', 'warning'); return; }
    try {
        await API.post('/api/credentials/profiles', data);
        showToast('凭据配置添加成功', 'success');
        document.querySelector('.modal-overlay').remove();
        loadProfiles();
    } catch (e) {
        showToast('添加失败: ' + e.message, 'error');
    }
}

async function editProfile(id) {
    try {
        const p = await API.get(`/api/credentials/profiles/${id}`);
        const modal = `
            <div class="modal-overlay active" onclick="if(event.target===this)this.classList.remove('active')">
                <div class="modal">
                    <div class="modal-header"><h3>编辑凭据配置</h3><button class="btn btn-sm btn-outline" onclick="this.closest('.modal-overlay').remove()">✕</button></div>
                    <div class="modal-body">
                        <div class="form-group"><label>名称</label><input type="text" class="form-control" id="edit-profile-name" value="${escapeAttr(p.name)}"></div>
                        <div class="form-group">
                            <label>设备类型</label>
                            <select class="form-control" id="edit-profile-device-type">
                                <option value="cisco_ios" ${p.device_type==='cisco_ios'?'selected':''}>Cisco IOS</option>
                                <option value="cisco_ios_xe" ${p.device_type==='cisco_ios_xe'?'selected':''}>Cisco IOS-XE</option>
                                <option value="cisco_nxos" ${p.device_type==='cisco_nxos'?'selected':''}>Cisco NX-OS</option>
                                <option value="cisco_wlc" ${p.device_type==='cisco_wlc'?'selected':''}>Cisco WLC</option>
                                <option value="cisco_ap" ${p.device_type==='cisco_ap'?'selected':''}>Cisco AP</option>
                            </select>
                        </div>
                        <div class="form-group"><label>用户名</label><input type="text" class="form-control" id="edit-profile-username" value="${escapeAttr(p.username)}"></div>
                        <div class="form-group"><label>新密码 (留空不修改)</label><input type="password" class="form-control" id="edit-profile-password" placeholder="${p.has_password?'••••••':'未设置'}"></div>
                        <div class="form-group"><label>新Enable密码 (留空不修改)</label><input type="password" class="form-control" id="edit-profile-enable-password" placeholder="${p.has_enable_password?'••••••':'未设置'}"></div>
                        <div class="form-group"><label>SSH端口</label><input type="number" class="form-control" id="edit-profile-port" value="${p.port}"></div>
                        <div class="form-group">
                            <label>出口网卡 (源IP)</label>
                            <select class="form-control" id="edit-profile-source-ip">
                                <option value="">自动（系统默认路由）</option>
                            </select>
                        </div>
                        <div class="form-group"><label>说明</label><input type="text" class="form-control" id="edit-profile-desc" value="${escapeAttr(p.description || '')}"></div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button>
                        <button class="btn btn-primary" onclick="submitEditProfile(${id})">保存</button>
                    </div>
                </div>
            </div>
        `;
        document.getElementById('modalContainer').innerHTML = modal;
        loadNICsForProfile('edit-profile-source-ip', p.source_ip);
    } catch (e) {
        showToast('加载凭据信息失败: ' + e.message, 'error');
    }
}

async function submitEditProfile(id) {
    const password = document.getElementById('edit-profile-password').value;
    const enablePwd = document.getElementById('edit-profile-enable-password').value;
    const data = {
        name: document.getElementById('edit-profile-name').value,
        device_type: document.getElementById('edit-profile-device-type').value,
        username: document.getElementById('edit-profile-username').value,
        port: parseInt(document.getElementById('edit-profile-port').value),
        source_ip: document.getElementById('edit-profile-source-ip').value,
        description: document.getElementById('edit-profile-desc').value,
    };
    if (password) data.password = password;
    if (enablePwd) data.enable_password = enablePwd;
    try {
        await API.put(`/api/credentials/profiles/${id}`, data);
        showToast('凭据配置更新成功', 'success');
        document.querySelector('.modal-overlay').remove();
        loadProfiles();
    } catch (e) {
        showToast('更新失败: ' + e.message, 'error');
    }
}

async function deleteProfile(id) {
    if (!confirm('确定要删除此凭据配置吗？')) return;
    try {
        await API.delete(`/api/credentials/profiles/${id}`);
        showToast('凭据配置已删除', 'success');
        loadProfiles();
    } catch (e) {
        showToast('删除失败: ' + e.message, 'error');
    }
}

function showAddScheduleModal() {
    const modal = `
        <div class="modal-overlay active" onclick="if(event.target===this)this.classList.remove('active')">
            <div class="modal">
                <div class="modal-header"><h3>添加定时任务</h3><button class="btn btn-sm btn-outline" onclick="this.closest('.modal-overlay').remove()">✕</button></div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>任务类型</label>
                        <select class="form-control" id="sched-type">
                            <option value="backup">配置备份</option>
                            <option value="discovery">邻居发现</option>
                            <option value="info">信息采集</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Cron表达式</label>
                        <input type="text" class="form-control" id="sched-cron" value="0 2 * * *" placeholder="分 时 日 月 星期">
                    </div>
                    <div class="form-group">
                        <label>说明</label>
                        <input type="text" class="form-control" id="sched-desc" placeholder="每日备份">
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button>
                    <button class="btn btn-primary" onclick="submitAddSchedule()">添加</button>
                </div>
            </div>
        </div>
    `;
    document.getElementById('modalContainer').innerHTML = modal;
}

async function submitAddSchedule() {
    const data = {
        task_type: document.getElementById('sched-type').value,
        cron_expression: document.getElementById('sched-cron').value,
        description: document.getElementById('sched-desc').value,
        is_enabled: true,
    };
    try {
        await API.post('/api/schedule', data);
        showToast('定时任务添加成功', 'success');
        document.querySelector('.modal-overlay').remove();
        loadSchedules();
    } catch (e) {
        showToast('添加失败: ' + e.message, 'error');
    }
}

async function toggleSchedule(id) {
    try {
        await API.post(`/api/schedule/${id}/toggle`);
        loadSchedules();
    } catch (e) {
        showToast('操作失败: ' + e.message, 'error');
    }
}

async function editSchedule(id) {
    const schedules = await API.get('/api/schedule');
    const s = schedules.find(x => x.id === id);
    if (!s) return;

    const modal = `
        <div class="modal-overlay active" onclick="if(event.target===this)this.classList.remove('active')">
            <div class="modal">
                <div class="modal-header"><h3>编辑定时任务</h3><button class="btn btn-sm btn-outline" onclick="this.closest('.modal-overlay').remove()">✕</button></div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Cron表达式</label>
                        <input type="text" class="form-control" id="edit-sched-cron" value="${escapeAttr(s.cron_expression)}">
                    </div>
                    <div class="form-group">
                        <label>说明</label>
                        <input type="text" class="form-control" id="edit-sched-desc" value="${escapeAttr(s.description || '')}">
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button>
                    <button class="btn btn-primary" onclick="submitEditSchedule(${id}, '${s.task_type}')">保存</button>
                </div>
            </div>
        </div>
    `;
    document.getElementById('modalContainer').innerHTML = modal;
}

async function submitEditSchedule(id, taskType) {
    const data = {
        task_type: taskType,
        cron_expression: document.getElementById('edit-sched-cron').value,
        description: document.getElementById('edit-sched-desc').value,
        is_enabled: true,
    };
    try {
        await API.put(`/api/schedule/${id}`, data);
        showToast('定时任务更新成功', 'success');
        document.querySelector('.modal-overlay').remove();
        loadSchedules();
    } catch (e) {
        showToast('更新失败: ' + e.message, 'error');
    }
}

async function deleteSchedule(id) {
    if (!confirm('确定要删除此定时任务吗？')) return;
    try {
        await API.delete(`/api/schedule/${id}`);
        showToast('定时任务已删除', 'success');
        loadSchedules();
    } catch (e) {
        showToast('删除失败: ' + e.message, 'error');
    }
}

function showAddGroupModal() {
    const modal = `
        <div class="modal-overlay active" onclick="if(event.target===this)this.classList.remove('active')">
            <div class="modal">
                <div class="modal-header"><h3>添加分组</h3><button class="btn btn-sm btn-outline" onclick="this.closest('.modal-overlay').remove()">✕</button></div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>分组名称</label>
                        <input type="text" class="form-control" id="group-name" placeholder="核心交换机">
                    </div>
                    <div class="form-group">
                        <label>说明</label>
                        <input type="text" class="form-control" id="group-desc" placeholder="核心层设备">
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button>
                    <button class="btn btn-primary" onclick="submitAddGroup()">添加</button>
                </div>
            </div>
        </div>
    `;
    document.getElementById('modalContainer').innerHTML = modal;
}

async function submitAddGroup() {
    const data = {
        name: document.getElementById('group-name').value,
        description: document.getElementById('group-desc').value,
    };
    if (!data.name) { showToast('请输入分组名称', 'warning'); return; }
    try {
        await API.post('/api/devices/groups', data);
        showToast('分组添加成功', 'success');
        document.querySelector('.modal-overlay').remove();
        loadGroups();
    } catch (e) {
        showToast('添加失败: ' + e.message, 'error');
    }
}

async function deleteGroup(id) {
    if (!confirm('确定要删除此分组吗？')) return;
    try {
        await API.delete(`/api/devices/groups/${id}`);
        showToast('分组已删除', 'success');
        loadGroups();
    } catch (e) {
        showToast('删除失败: ' + e.message, 'error');
    }
}

async function decodeSerial() {
    const serial = document.getElementById('serialInput').value.trim();
    if (!serial) { showToast('请输入序列号', 'warning'); return; }
    try {
        const result = await API.get(`/api/serial/decode/${encodeURIComponent(serial)}`);
        document.getElementById('serialResult').innerHTML = `
            <table class="data-table">
                <tbody>
                    <tr><td style="width: 150px;"><strong>序列号</strong></td><td>${escapeHtml(result.serial_number)}</td></tr>
                    <tr><td><strong>格式</strong></td><td>${escapeHtml(result.format)}</td></tr>
                    <tr><td><strong>产地代码</strong></td><td>${escapeHtml(result.location_code)}</td></tr>
                    <tr><td><strong>产地</strong></td><td>${escapeHtml(result.location_name)}</td></tr>
                    <tr><td><strong>预估生产日期</strong></td><td><span class="badge badge-info">${escapeHtml(result.production_date || '无法估算')}</span></td></tr>
                    <tr><td><strong>备注</strong></td><td style="font-size: 12px; color: #666;">${escapeHtml(result.notes)}</td></tr>
                </tbody>
            </table>
        `;
    } catch (e) {
        showToast('查询失败: ' + e.message, 'error');
    }
}

// ---- Network Interfaces ----
async function loadNICs() {
    try {
        const data = await API.get('/api/nics');
        const body = document.getElementById('nicTable');
        if (data.interfaces && data.interfaces.length > 0) {
            body.innerHTML = data.interfaces.map(nic => `
                <tr>
                    <td><strong>${escapeHtml(nic.name)}</strong></td>
                    <td><code>${nic.ip_address}</code></td>
                    <td>${nic.is_up ? '<span class="badge badge-success">在线</span>' : '<span class="badge badge-secondary">离线</span>'}</td>
                    <td>${nic.is_loopback ? '回环' : '物理/虚拟'}</td>
                    <td>${data.selected_ip === nic.ip_address
                        ? '<span class="badge badge-success">当前默认</span>'
                        : (!nic.is_loopback && nic.is_up ? `<button class="btn btn-sm btn-outline" onclick="saveDefaultNIC('${escapeAttr(nic.ip_address)}')">设为默认</button>` : '-')}</td>
                </tr>
            `).join('');
        } else {
            body.innerHTML = '<tr><td colspan="5" class="empty-state">未检测到网卡</td></tr>';
        }
    } catch (e) {
        showToast('加载网卡列表失败: ' + e.message, 'error');
    }
}

async function saveDefaultNIC(sourceIp) {
    try {
        await API.put('/api/nics/selection', { source_ip: sourceIp });
        showToast(sourceIp ? `默认出口网卡已设置为 ${sourceIp}` : '已恢复系统自动路由', 'success');
        await loadNICs();
    } catch (e) {
        showToast('保存默认网卡失败: ' + e.message, 'error');
    }
}

// ---- Command Configuration ----
let allCommands = {};
let commandLabels = {};
let deviceTypeLabels = {};
let deviceTypeInfo = [];   // list of {key, label, builtin}
let currentDeviceType = '';

async function loadCommands() {
    try {
        const data = await API.get('/api/commands');
        allCommands = data.commands;
        commandLabels = data.labels;
        deviceTypeLabels = data.device_type_labels || {};
        deviceTypeInfo = data.device_types || [];
        renderCmdTabs();
    } catch (e) {
        showToast('加载命令配置失败: ' + e.message, 'error');
        document.getElementById('cmdTabs').innerHTML = '<span class="empty-state">加载失败</span>';
    }
}

function renderCmdTabs() {
    const tabsEl = document.getElementById('cmdTabs');
    if (!deviceTypeInfo || deviceTypeInfo.length === 0) {
        tabsEl.innerHTML = '<span class="empty-state">暂无设备类型</span>';
        return;
    }
    tabsEl.innerHTML = deviceTypeInfo.map(t => {
        const label = t.label || t.key;
        const active = (t.key === currentDeviceType) ? 'cmd-tab-active' : '';
        const builtin = !!t.builtin;
        // Built-ins are protected (no delete button) but can still be renamed.
        const actions = `
            <button class="btn btn-xs btn-link" onclick="renameDeviceTypePrompt('${escapeHtml(t.key)}')" title="重命名此类型" style="padding: 0 4px; font-size: 12px;">✎</button>
            ${builtin ? '' : `<button class="btn btn-xs btn-link" onclick="deleteDeviceTypePrompt('${escapeHtml(t.key)}')" title="删除此类型" style="padding: 0 4px; font-size: 12px; color: #c00;">✕</button>`}
        `;
        return `<div class="cmd-tab ${active}" style="display:inline-flex; align-items:center; gap:4px; padding: 4px 8px; border-radius: 4px; border: 1px solid ${active ? '#0d6efd' : '#ccc'}; background: ${active ? '#e7f1ff' : '#fff'};">
            <span class="cmd-tab-label" style="cursor: pointer;" onclick="selectDeviceType('${escapeHtml(t.key)}')">${escapeHtml(label)}</span>
            <span style="display:inline-flex; align-items:center;">${actions}</span>
        </div>`;
    }).join('');
    if (!currentDeviceType && deviceTypeInfo.length > 0) {
        selectDeviceType(deviceTypeInfo[0].key);
    }
}

function selectDeviceType(type) {
    currentDeviceType = type;
    renderCmdTabs();
    renderCmdTable();
}

function renderCmdTable() {
    const cmds = allCommands[currentDeviceType] || {};
    const body = document.getElementById('cmdTable');
    const keys = Object.keys(cmds);
    if (keys.length === 0) {
        body.innerHTML = '<tr><td colspan="5" class="empty-state">暂无命令配置，点击下方「添加命令」新增</td></tr>';
        document.getElementById('cmdEditor').style.display = 'block';
        return;
    }
    body.innerHTML = keys.map(key => {
        const cmd = cmds[key];
        const label = commandLabels[key] || key;
        return `
            <tr data-key="${escapeHtml(key)}">
                <td><code style="font-size:12px;">${escapeHtml(key)}</code></td>
                <td>
                    <input type="text" class="form-control" id="cmd-label-${key}" value="${escapeHtml(label)}" style="width: 100%;" readonly title="内置命令名称（不可编辑）">
                </td>
                <td>
                    <input type="text" class="form-control" id="cmd-input-${key}" value="${escapeHtml(cmd.command || '')}" style="width: 100%; font-family: monospace; font-size: 13px;">
                    ${cmd.description ? `<div style="font-size: 11px; color: #999; margin-top: 2px;">${escapeHtml(cmd.description)}</div>` : ''}
                </td>
                <td>
                    <input type="number" class="form-control" id="cmd-delay-${key}" value="${cmd.delay_factor || 1.5}" step="0.1" min="0.5" max="10" style="width: 80px;">
                </td>
                <td>
                    <button class="btn btn-sm btn-danger" onclick="deleteCmdRow('${key}')" title="删除此命令">🗑</button>
                </td>
            </tr>
        `;
    }).join('');
    document.getElementById('cmdEditor').style.display = 'block';
}

// Add an editable new command row (key + name + command + delay).
function addCommandRow() {
    document.getElementById('cmdEditor').style.display = 'block';
    const body = document.getElementById('cmdTable');
    if (body.querySelector('.empty-state')) body.innerHTML = '';
    const rowId = 'new-' + Date.now() + '-' + Math.floor(Math.random() * 1000);
    const tr = document.createElement('tr');
    tr.id = 'newrow-' + rowId;
    tr.innerHTML = `
        <td><input type="text" class="form-control" id="new-key-${rowId}" placeholder="命令Key，如 show_vlan" style="font-family: monospace; font-size: 12px;"></td>
        <td><input type="text" class="form-control" id="new-label-${rowId}" placeholder="命令名称，如 查看VLAN"></td>
        <td><input type="text" class="form-control" id="new-cmd-${rowId}" placeholder="show vlan brief" style="font-family: monospace; font-size: 13px;"></td>
        <td><input type="number" class="form-control" id="new-delay-${rowId}" value="1.5" step="0.1" min="0.5" max="10" style="width: 80px;"></td>
        <td><button class="btn btn-sm btn-danger" onclick="document.getElementById('newrow-${rowId}').remove()" title="移除该行">✕</button></td>
    `;
    body.appendChild(tr);
}

// Remove an existing command (mark for deletion; applied on save).
const deletedCmdKeys = new Set();
function deleteCmdRow(key) {
    if (!confirm(`确定删除命令「${key}」吗？保存后生效。`)) return;
    deletedCmdKeys.add(key);
    const row = document.querySelector(`#cmdTable tr[data-key="${CSS.escape(key)}"]`);
    if (row) row.remove();
}

async function saveCurrentCommands() {
    if (!currentDeviceType) return;
    const cmds = allCommands[currentDeviceType] || {};
    const updated = {};
    for (const key of Object.keys(cmds)) {
        if (deletedCmdKeys.has(key)) continue; // will be deleted via API
        const cmdInput = document.getElementById(`cmd-input-${key}`);
        const delayInput = document.getElementById(`cmd-delay-${key}`);
        if (cmdInput && delayInput) {
            updated[key] = {
                command: cmdInput.value,
                description: cmds[key].description || '',   // preserve original description
                delay_factor: parseFloat(delayInput.value) || 1.5,
            };
        }
    }
    // Collect newly added rows
    const newRows = Array.from(document.querySelectorAll('#cmdTable tr[id^="newrow-"]'));
    for (const tr of newRows) {
        const rowId = tr.id.replace('newrow-', '');
        const nk = document.getElementById(`new-key-${rowId}`).value.trim();
        const ncmd = document.getElementById(`new-cmd-${rowId}`).value.trim();
        const nlabel = document.getElementById(`new-label-${rowId}`).value.trim();
        const ndelay = parseFloat(document.getElementById(`new-delay-${rowId}`).value) || 1.5;
        if (!nk || !ncmd) {
            showToast('新增命令的「命令Key」和「SSH命令」不能为空', 'warning');
            return;
        }
        if (updated[nk]) {
            showToast(`命令Key「${nk}」已存在`, 'warning');
            return;
        }
        updated[nk] = { command: ncmd, description: nlabel || nk, delay_factor: ndelay };
    }
    try {
        await API.put(`/api/commands/${currentDeviceType}`, { device_type: currentDeviceType, commands: updated });
        // Delete removed keys
        for (const key of deletedCmdKeys) {
            try { await API.delete(`/api/commands/${currentDeviceType}/${key}`); }
            catch (e) { console.error('delete cmd', key, e); }
        }
        deletedCmdKeys.clear();
        allCommands[currentDeviceType] = updated;
        renderCmdTable();
        showToast(`${deviceTypeLabels[currentDeviceType] || currentDeviceType} 命令已保存`, 'success');
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

async function resetCurrentDeviceType() {
    if (!currentDeviceType) return;
    if (!confirm(`确定要重置 ${deviceTypeLabels[currentDeviceType] || currentDeviceType} 的所有命令为默认值吗？`)) return;
    try {
        const result = await API.post(`/api/commands/reset/${currentDeviceType}`);
        allCommands[currentDeviceType] = result.commands;
        renderCmdTable();
        showToast('已重置为默认命令', 'success');
    } catch (e) {
        showToast('重置失败: ' + e.message, 'error');
    }
}

async function resetAllCommands() {
    if (!confirm('确定要重置所有设备类型的命令为默认值吗？此操作不可撤销。')) return;
    try {
        const result = await API.post('/api/commands/reset');
        allCommands = result.commands;
        deviceTypeInfo = result.device_types || deviceTypeInfo;
        renderCmdTabs();
        renderCmdTable();
        showToast('所有命令已重置为默认值', 'success');
    } catch (e) {
        showToast('重置失败: ' + e.message, 'error');
    }
}

// ---- Device-type group management ----

async function addDeviceType() {
    const label = (prompt('请输入新增的设备类型名称（显示名，例如：HUAWEI VRP / H3C Comware / Juniper Junos）：') || '').trim();
    if (!label) return;
    // Offer a choice of which existing type to base commands on
    const baseOptions = (deviceTypeInfo || []).map(t => t.key + ' - ' + t.label).join('\n');
    const baseKey = (prompt(`请输入要克隆命令的源设备类型 key（留空则默认 cisco_ios）：\n${baseOptions}`) || 'cisco_ios').trim() || 'cisco_ios';
    try {
        const result = await API.post('/api/commands/types', { label, base_on: baseKey });
        if (result.device_types) deviceTypeInfo = result.device_types;
        // Refresh local commands cache too
        await loadCommands();
        currentDeviceType = result.key || '';
        renderCmdTabs();
        renderCmdTable();
        showToast(result.message || '设备类型已新增', 'success');
    } catch (e) {
        showToast('新增失败: ' + (e.message || e), 'error');
    }
}

async function renameDeviceTypePrompt(typeKey) {
    const currentLabel = (deviceTypeLabels[typeKey] || typeKey);
    const newLabel = (prompt(`重命名设备类型「${currentLabel}」(key: ${typeKey})：`, currentLabel) || '').trim();
    if (!newLabel || newLabel === currentLabel) return;
    try {
        const result = await API.put(`/api/commands/types/${encodeURIComponent(typeKey)}/label`, { label: newLabel });
        if (result.device_types) deviceTypeInfo = result.device_types;
        deviceTypeLabels[typeKey] = result.label;
        renderCmdTabs();
        showToast(result.message || '已重命名', 'success');
    } catch (e) {
        showToast('重命名失败: ' + (e.message || e), 'error');
    }
}

async function deleteDeviceTypePrompt(typeKey) {
    const t = (deviceTypeInfo || []).find(x => x.key === typeKey);
    if (t && t.builtin) {
        showToast('内置设备类型不可删除（可重命名或编辑其命令）', 'warning');
        return;
    }
    const label = (t && t.label) || typeKey;
    if (!confirm(`确定要删除设备类型「${label}」吗？\n\n删除后，所有引用此类型的设备在采集时会回退到 cisco_ios 模板。\n该设备的类型字段不会被清空。`)) return;
    try {
        const result = await API.delete(`/api/commands/types/${encodeURIComponent(typeKey)}`);
        if (result.device_types) deviceTypeInfo = result.device_types;
        // Remove from local map
        if (allCommands[typeKey]) {
            delete allCommands[typeKey];
            delete deviceTypeLabels[typeKey];
        }
        // If the deleted tab was active, switch to the first remaining
        if (currentDeviceType === typeKey) {
            currentDeviceType = (deviceTypeInfo && deviceTypeInfo[0] && deviceTypeInfo[0].key) || '';
        }
        renderCmdTabs();
        renderCmdTable();
        showToast(result.message || '已删除', 'success');
    } catch (e) {
        showToast('删除失败: ' + (e.message || e), 'error');
    }
}

async function resetSingleCommand(key) {
    // Fetch defaults from the reset endpoint for this device type
    try {
        const result = await API.post(`/api/commands/reset/${currentDeviceType}`);
        const defaultCmd = result.commands[key];
        if (defaultCmd) {
            const cmdInput = document.getElementById(`cmd-input-${key}`);
            const delayInput = document.getElementById(`cmd-delay-${key}`);
            if (cmdInput) cmdInput.value = defaultCmd.command;
            if (delayInput) delayInput.value = defaultCmd.delay_factor;
            // Update in memory but don't save automatically
            allCommands[currentDeviceType] = result.commands;
            showToast(`命令 ${commandLabels[key] || key} 已恢复默认值（需点击保存生效）`, 'info');
        }
    } catch (e) {
        showToast('重置失败: ' + e.message, 'error');
    }
}

loadSchedules();
loadGroups();
loadProfiles();
loadNICs();
loadCommands();
loadADConfig();
loadWebAuth();
initUserManagement();
loadSystemHealth();

// ---- AD / LDAP 集成 ----
async function loadADConfig() {
    try {
        const cfg = await API.get('/api/ad/config');
        document.getElementById('ad-host').value = cfg.host || '';
        document.getElementById('ad-port').value = cfg.port || 389;
        document.getElementById('ad-ssl').value = cfg.use_ssl ? '1' : '0';
        document.getElementById('ad-binddn').value = cfg.bind_dn || '';
        document.getElementById('ad-basedn').value = cfg.base_dn || '';
        document.getElementById('ad-filter').value = cfg.user_filter || '(objectClass=user)';
        document.getElementById('ad-sam').value = cfg.sam_attr || 'sAMAccountName';
        document.getElementById('ad-name').value = cfg.name_attr || 'displayName';
        document.getElementById('ad-mail').value = cfg.mail_attr || 'mail';
    } catch (e) { console.error('加载 AD 配置失败', e); }
}

function _adConfigPayload() {
    return {
        host: document.getElementById('ad-host').value.trim(),
        port: parseInt(document.getElementById('ad-port').value) || 389,
        use_ssl: document.getElementById('ad-ssl').value === '1',
        bind_dn: document.getElementById('ad-binddn').value.trim(),
        bind_password: document.getElementById('ad-bindpw').value,
        base_dn: document.getElementById('ad-basedn').value.trim(),
        user_filter: document.getElementById('ad-filter').value.trim() || '(objectClass=user)',
        sam_attr: document.getElementById('ad-sam').value.trim() || 'sAMAccountName',
        name_attr: document.getElementById('ad-name').value.trim() || 'displayName',
        mail_attr: document.getElementById('ad-mail').value.trim() || 'mail',
    };
}

async function saveADConfig() {
    try {
        await API.put('/api/ad/config', _adConfigPayload());
        showToast('AD 配置已保存', 'success');
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
}

async function testADConnection() {
    const box = document.getElementById('adStatus');
    box.innerHTML = '<span class="badge badge-info">连接中...</span>';
    try {
        const r = await API.post('/api/ad/test');
        if (r.ok) {
            box.innerHTML = `<span class="badge badge-success">✅ ${escapeHtml(r.message)}</span> <span style="color:#666;font-size:12px;">${escapeHtml(r.server||'')} ${r.ssl?'SSL':''} ｜ 默认命名上下文: ${escapeHtml(r.default_naming_context||'-')}</span>`;
        } else {
            box.innerHTML = `<span class="badge badge-danger">❌ ${escapeHtml(r.message)}</span>`;
        }
    } catch (e) { box.innerHTML = `<span class="badge badge-danger">❌ ${escapeHtml(e.message)}</span>`; }
}

async function syncADUsers() {
    const box = document.getElementById('adStatus');
    box.innerHTML = '<span class="badge badge-info">同步中...</span>';
    try {
        const r = await API.post('/api/ad/sync');
        box.innerHTML = `<span class="badge badge-success">✅ ${escapeHtml(r.message)}</span>`;
        showToast('AD 用户同步完成', 'success');
    } catch (e) { box.innerHTML = `<span class="badge badge-danger">❌ ${escapeHtml(e.message)}</span>`; }
}

async function verifyAD() {
    const box = document.getElementById('adVerifyStatus');
    const user = document.getElementById('ad-v-user').value.trim();
    const pw = document.getElementById('ad-v-pw').value;
    if (!user) { showToast('请输入用户名', 'warning'); return; }
    box.innerHTML = '<span class="badge badge-info">校验中...</span>';
    try {
        const r = await API.post('/api/ad/verify', { username: user, password: pw });
        if (r.ok) {
            box.innerHTML = `<span class="badge badge-success">✅ ${escapeHtml(r.message)}</span>`;
        } else {
            box.innerHTML = `<span class="badge badge-danger">❌ ${escapeHtml(r.message)}</span>`;
        }
    } catch (e) { box.innerHTML = `<span class="badge badge-danger">❌ ${escapeHtml(e.message)}</span>`; }
}

// ---- Web 登录口令 ----
async function loadWebAuth() {
    // 仅用于占位/未来展示；服务端不返回密码明文
    const st = document.getElementById('wa-status');
    if (st) st.textContent = '';
}

async function saveWebAuth() {
    const st = document.getElementById('wa-status');
    const cur = document.getElementById('wa-cur').value;
    const user = document.getElementById('wa-user').value.trim();
    const pass = document.getElementById('wa-pass').value;
    const conf = document.getElementById('wa-conf').value;
    if (pass !== conf) {
        st.innerHTML = '<span style="color:#e74c3c;">两次输入的新密码不一致</span>';
        return;
    }
    if (pass && pass.length < 12) {
        st.innerHTML = '<span style="color:#e74c3c;">新密码至少需要 12 个字符</span>';
        return;
    }
    try {
        const r = await API.post('/api/auth/set', {
            current_password: cur,
            username: user,
            password: pass,
            confirm_password: conf,
        });
        st.innerHTML = `<span style="color:#27ae60;">✅ ${escapeHtml(r.detail || '已保存')}</span>`;
        showToast('Web 登录口令已更新', 'success');
        document.getElementById('wa-cur').value = '';
        document.getElementById('wa-pass').value = '';
        document.getElementById('wa-conf').value = '';
    } catch (e) {
        st.innerHTML = `<span style="color:#e74c3c;">❌ ${escapeHtml(e.message)}</span>`;
    }
}

// ---- 用户管理（仅超级管理员）----
function moduleCheckboxesHtml(selected, disabled) {
    const sel = new Set(selected || []);
    const dis = disabled ? 'disabled' : '';
    return (window.MODULES || []).map(([key, label, icon]) => {
        const checked = sel.has(key) ? 'checked' : '';
        return `<label style="display:inline-flex;align-items:center;margin:4px 12px 4px 0;font-weight:normal;cursor:pointer;">
            <input type="checkbox" class="mod-chk" value="${key}" ${checked} ${dis}> ${icon} ${escapeHtml(label)}
        </label>`;
    }).join('');
}

function collectModules() {
    const set = new Set();
    document.querySelectorAll('#userModal .mod-chk:checked').forEach((c) => set.add(c.value));
    return [...set];
}

function userModalHTML(title, u) {
    u = u || {};
    const isEdit = !!u.id;
    const su = !!u.is_superuser;
    return `
    <div class="modal-overlay active" onclick="if(event.target===this)this.classList.remove('active')">
      <div class="modal" style="max-width:640px;">
        <div class="modal-header"><h3>${title}</h3><button class="btn btn-sm btn-outline" onclick="this.closest('.modal-overlay').remove()">✕</button></div>
        <div class="modal-body">
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
            <div class="form-group" style="flex:1;min-width:200px;"><label>账号 *</label><input type="text" class="form-control" id="um-user" value="${escapeHtml(u.username || '')}" ${isEdit ? '' : ''}></div>
            <div class="form-group" style="flex:1;min-width:200px;">
              <label style="display:inline-flex;align-items:center;font-weight:normal;cursor:pointer;">
                <input type="checkbox" id="um-su" ${su ? 'checked' : ''} onchange="toggleSu()"> 超级管理员（全部模块）
              </label>
            </div>
          </div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
            <div class="form-group" style="flex:1;min-width:200px;"><label>${isEdit ? '重置密码（留空不修改）' : '密码 *'}</label><input type="password" class="form-control" id="um-pass" placeholder="${isEdit ? '留空则保持原密码' : ''}"></div>
            <div class="form-group" style="flex:1;min-width:200px;"><label>${isEdit ? '确认新密码' : '确认密码 *'}</label><input type="password" class="form-control" id="um-conf"></div>
          </div>
          <div style="margin-top:8px;">
            <label style="display:inline-flex;align-items:center;font-weight:normal;cursor:pointer;margin-right:14px;">
              <input type="checkbox" id="um-active" ${u.is_active !== false ? 'checked' : ''}> 账号启用
            </label>
          </div>
          <div style="margin-top:10px;border-top:1px solid #eee;padding-top:10px;">
            <div style="font-size:13px;margin-bottom:4px;color:#555;">授权功能模块${su ? '（超级管理员无需勾选）' : ''}</div>
            <div id="um-modules">${moduleCheckboxesHtml(u.modules, su)}</div>
          </div>
          <div id="um-status" style="margin-top:10px;font-size:13px;"></div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline" onclick="this.closest('.modal-overlay').remove()">取消</button>
          <button class="btn btn-primary" id="um-save" onclick="saveUser(${u.id || 0})">保存</button>
        </div>
      </div>
    </div>`;
}

function toggleSu() {
    const su = document.getElementById('um-su').checked;
    document.querySelectorAll('#um-modules .mod-chk').forEach((c) => { c.disabled = su; if (su) c.checked = true; });
}

async function initUserManagement() {
    try {
        const me = await API.get('/api/auth/me');
        if (me.is_superuser) {
            const card = document.getElementById('userMgmtCard');
            if (card) card.style.display = '';
            await loadUsers();
        }
    } catch (e) { /* 非超级管理员或无权限：不显示 */ }
}

async function loadUsers() {
    try {
        const r = await API.get('/api/users');
        const tbody = document.getElementById('userTbody');
        tbody.innerHTML = r.users.map((u) => {
            let mods;
            if (u.is_superuser) mods = '<span class="badge badge-info">全部模块</span>';
            else if (!u.modules || u.modules.length === 0) mods = '<span class="badge badge-secondary">无</span>';
            else mods = u.modules.map((m) => `<span class="badge badge-light">${(window.MODULES.find((x) => x[0] === m) || [,'?'])[1]}</span>`).join(' ');
            const role = u.is_superuser ? '<span class="badge badge-danger">超级管理员</span>' : '<span class="badge badge-success">普通账号</span>';
            const st = u.is_active ? '<span class="badge badge-success">启用</span>' : '<span class="badge badge-secondary">禁用</span>';
            return `<tr>
                <td>${escapeHtml(u.username)}</td>
                <td>${role}</td>
                <td>${st}</td>
                <td>${mods}</td>
                <td>
                    <button class="btn btn-sm btn-outline" onclick="editUser(${u.id})">编辑</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteUser(${u.id})">删除</button>
                </td>
            </tr>`;
        }).join('');
    } catch (e) { showToast('加载用户失败：' + e.message, 'warning'); }
}

function showAddUserModal() {
    document.getElementById('modalContainer').innerHTML = userModalHTML('新增管理账号', {});
}
function editUser(id) {
    fetch('/api/users').then((x) => x.json()).then((r) => {
        const u = (r.users || []).find((x) => x.id === id);
        if (!u) return;
        document.getElementById('modalContainer').innerHTML = userModalHTML('编辑账号：' + u.username, u);
    });
}
function closeUserModal() { document.getElementById('modalContainer').innerHTML = ''; }

async function saveUser(id) {
    const box = document.getElementById('um-status');
    const username = document.getElementById('um-user').value.trim();
    const pass = document.getElementById('um-pass').value;
    const conf = document.getElementById('um-conf').value;
    const isSu = document.getElementById('um-su').checked;
    const active = document.getElementById('um-active').checked;
    if (!username) { box.innerHTML = '<span style="color:#e74c3c;">账号不能为空</span>'; return; }
    if (!id && !pass) { box.innerHTML = '<span style="color:#e74c3c;">密码不能为空</span>'; return; }
    if (pass && pass !== conf) { box.innerHTML = '<span style="color:#e74c3c;">两次输入的密码不一致</span>'; return; }
    if (pass && pass.length < 12) { box.innerHTML = '<span style="color:#e74c3c;">密码至少需要 12 个字符</span>'; return; }
    const payload = { username, password: pass, is_superuser: isSu, is_active: active, modules: isSu ? [] : collectModules() };
    try {
        const r = id ? await API.put('/api/users/' + id, payload) : await API.post('/api/users', payload);
        if (r.ok) {
            showToast(r.detail || '已保存', 'success');
            closeUserModal();
            await loadUsers();
        } else {
            box.innerHTML = `<span style="color:#e74c3c;">❌ ${escapeHtml(r.detail || '保存失败')}</span>`;
        }
    } catch (e) {
        box.innerHTML = `<span style="color:#e74c3c;">❌ ${escapeHtml(e.message)}</span>`;
    }
}

async function deleteUser(id) {
    if (!confirm('确定删除该管理账号？此操作不可撤销。')) return;
    try {
        const r = await API.delete('/api/users/' + id);
        if (r.ok) { showToast('已删除', 'success'); await loadUsers(); }
        else showToast(r.detail || '删除失败', 'warning');
    } catch (e) { showToast('删除失败：' + e.message, 'warning'); }
}
