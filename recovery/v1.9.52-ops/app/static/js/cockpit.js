(function () {
    'use strict';
    let refreshTimer = null;
    let loading = false;

    function el(id) { return document.getElementById(id); }
    function setText(id, value) { const node = el(id); if (node) node.textContent = value == null ? '-' : String(value); }
    function number(value) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }
    function percent(value) { return Math.max(0, Math.min(100, number(value))); }

    async function safeGet(url) {
        try {
            const response = await fetch(url, {headers: {'Accept': 'application/json'}});
            if (response.status === 401) { window.location.href = '/login'; return null; }
            if (!response.ok) return null;
            return await response.json();
        } catch (error) {
            return null;
        }
    }

    function taskLabel(type) {
        return ({backup:'配置备份', discovery:'邻居发现', info:'设备采集', topology:'拓扑更新', zabbix_sync:'Zabbix同步', vcenter_sync:'vCenter同步'})[type] || type || '系统任务';
    }

    function statusLabel(status) {
        return ({success:'成功', failed:'失败', partial:'部分成功', running:'运行中', pending:'等待中'})[status] || status || '未知';
    }

    function formatBandwidth(mbps) {
        const value = number(mbps);
        return value >= 1000 ? (value / 1000).toFixed(value % 1000 ? 1 : 0) + ' Gbps' : value + ' Mbps';
    }

    function renderBars(targetId, entries, total, formatter) {
        const target = el(targetId);
        if (!target) return;
        if (!entries.length) { target.innerHTML = '<div class="cockpit-empty">暂无可展示数据</div>'; return; }
        const denominator = Math.max(number(total), 1);
        target.innerHTML = entries.slice(0, 6).map(function (entry) {
            const width = Math.max(entry.value > 0 ? 3 : 0, Math.min(100, number(entry.value) / denominator * 100));
            const display = formatter ? formatter(entry) : entry.value;
            return '<div class="bar-row"><span title="' + escapeAttr(entry.label) + '">' + escapeHtml(entry.label) + '</span>' +
                '<div class="bar-track"><i style="width:' + width.toFixed(1) + '%"></i></div><strong>' + escapeHtml(display) + '</strong></div>';
        }).join('');
    }

    function renderVmStates(summary) {
        const target = el('ck-vm-states');
        if (!target) return;
        const entries = Object.entries((summary && summary.by_status) || {});
        if (!entries.length) { target.innerHTML = '<div class="cockpit-empty">暂无虚机数据</div>'; return; }
        target.innerHTML = entries.slice(0, 6).map(function (entry) {
            return '<div><span>' + escapeHtml(entry[0] || '其他') + '</span><strong>' + number(entry[1]) + '</strong></div>';
        }).join('');
    }

    function renderAlerts(dashboard, vmSummary, syncRuns) {
        const alerts = [];
        if (number(dashboard.offline_devices)) alerts.push({tone:'danger', title:'离线设备', detail:'设备连接或状态需要确认', value:dashboard.offline_devices});
        if (number(dashboard.unknown_devices)) alerts.push({tone:'warning', title:'未知状态设备', detail:'尚未获得可靠运行状态', value:dashboard.unknown_devices});
        if (number(dashboard.devices_never_backed_up)) alerts.push({tone:'warning', title:'从未备份', detail:'设备尚无可恢复配置', value:dashboard.devices_never_backed_up});
        if (number(dashboard.devices_with_changes)) alerts.push({tone:'warning', title:'近24小时配置变化', detail:'建议进入变更中心确认', value:dashboard.devices_with_changes});
        const failedTasks = (dashboard.recent_tasks || []).filter(function (row) { return row.status === 'failed' || number(row.failed_count) > 0; });
        if (failedTasks.length) alerts.push({tone:'danger', title:'最近任务失败', detail:'任务记录中存在失败结果', value:failedTasks.length});
        const failedSync = (syncRuns || []).filter(function (row) { return row.status === 'failed'; });
        if (failedSync.length) alerts.push({tone:'danger', title:'平台同步失败', detail:'请检查平台集成连接与权限', value:failedSync.length});
        const vmAttention = Object.entries((vmSummary && vmSummary.by_status) || {}).filter(function (entry) { return !/运行|poweredOn|online/i.test(entry[0]); }).reduce(function (sum, entry) { return sum + number(entry[1]); }, 0);
        if (vmAttention) alerts.push({tone:'warning', title:'非运行中虚机', detail:'包含关机、挂起、模板或其他状态', value:vmAttention});
        if (!alerts.length) alerts.push({tone:'good', title:'当前未发现待处理异常', detail:'请结合现场监控继续观察', value:'✓'});
        setText('ck-alert-count', alerts[0].tone === 'good' ? 0 : alerts.length);
        el('ck-alerts').innerHTML = alerts.slice(0, 7).map(function (item) {
            return '<div class="cockpit-alert ' + escapeAttr(item.tone) + '"><i></i><div><strong>' + escapeHtml(item.title) + '</strong><span>' + escapeHtml(item.detail) + '</span></div><b>' + escapeHtml(item.value) + '</b></div>';
        }).join('');
        return alerts.some(function (item) { return item.tone === 'danger'; }) ? '需处理' : (alerts.length > 1 ? '需关注' : '稳定');
    }

    function renderTasks(tasks) {
        const target = el('ck-tasks');
        if (!target) return;
        if (!tasks || !tasks.length) { target.innerHTML = '<div class="cockpit-empty">暂无任务记录</div>'; return; }
        target.innerHTML = tasks.slice(0, 6).map(function (task) {
            const failed = task.status === 'failed' || number(task.failed_count) > 0;
            return '<div class="cockpit-task"><div><strong>' + escapeHtml(taskLabel(task.task_type)) + '</strong><span>' + escapeHtml(formatRelative(task.started_at)) + ' · 成功 ' + number(task.success_count) + ' / 失败 ' + number(task.failed_count) + '</span></div><b class="' + (failed ? 'failed' : '') + '">' + escapeHtml(statusLabel(task.status)) + '</b></div>';
        }).join('');
    }

    async function loadCockpit() {
        if (loading) return;
        loading = true;
        const refreshButton = el('cockpit-refresh');
        if (refreshButton) { refreshButton.disabled = true; refreshButton.textContent = '刷新中…'; }
        try {
            const results = await Promise.all([
                safeGet('/api/dashboard'), safeGet('/api/ipam/prefix-utilization'), safeGet('/api/vms/summary'),
                safeGet('/api/dc/summary'), safeGet('/api/assets/summary'), safeGet('/api/isp'),
                safeGet('/api/integrations/sync-runs?limit=12')
            ]);
            const dashboard = results[0];
            if (!dashboard) { showToast('驾驶舱核心数据读取失败', 'error'); return; }
            const ipam = results[1]; const vmSummary = results[2]; const dc = results[3];
            const assets = results[4]; const ispLines = Array.isArray(results[5]) ? results[5] : [];
            const syncRuns = Array.isArray(results[6]) ? results[6] : [];

            const total = number(dashboard.total_devices); const online = number(dashboard.online_devices);
            const offline = number(dashboard.offline_devices); const unknown = number(dashboard.unknown_devices);
            const onlineRate = total ? online / total * 100 : 0;
            setText('ck-device-total', total); setText('ck-device-online', online); setText('ck-online-rate', '可用率 ' + onlineRate.toFixed(1) + '%');
            setText('ck-device-risk', offline + unknown); setText('ck-backup-today', number(dashboard.backups_today));
            setText('ck-config-change', number(dashboard.devices_with_changes)); setText('ck-neighbors', number(dashboard.total_neighbors));
            setText('ck-health-rate', onlineRate.toFixed(1) + '%'); setText('ck-legend-online', online); setText('ck-legend-offline', offline); setText('ck-legend-unknown', unknown);
            const ring = el('ck-health-ring');
            if (ring) ring.style.background = 'conic-gradient(var(--cockpit-green) 0%, var(--cockpit-green) ' + onlineRate.toFixed(1) + '%, rgba(73,104,129,.22) ' + onlineRate.toFixed(1) + '%)';
            const typeEntries = Object.entries(dashboard.device_type_distribution || {}).map(function (entry) { return {label:deviceTypeLabel(entry[0]), value:number(entry[1])}; }).sort(function (a,b) { return b.value-a.value; });
            renderBars('ck-device-types', typeEntries, total);

            const overall = (ipam && ipam.overall) || {};
            setText('ck-ipam-util', ipam ? number(overall.utilization).toFixed(1) + '%' : '未授权');
            setText('ck-ipam-count', ipam ? '规划网段 ' + number(overall.prefix_count) : '无访问权限');
            setText('ck-posture-ipam', ipam ? number(overall.utilization).toFixed(1) + '%' : '-');
            const prefixes = ((ipam && ipam.prefixes) || []).slice().sort(function (a,b) { return number(b.utilization)-number(a.utilization); }).slice(0, 6).map(function (row) { return {label:row.prefix, value:number(row.utilization)}; });
            renderBars('ck-ipam-prefixes', prefixes, 100, function (entry) { return entry.value.toFixed(1) + '%'; });

            const runningVm = Object.entries((vmSummary && vmSummary.by_status) || {}).filter(function (entry) { return /运行|poweredOn|online/i.test(entry[0]); }).reduce(function (sum, entry) { return sum + number(entry[1]); }, 0);
            setText('ck-vm-running', vmSummary ? runningVm : '未授权'); setText('ck-vm-total', vmSummary ? '虚机总数 ' + number(vmSummary.total) : '无访问权限');
            renderVmStates(vmSummary);

            const totalBandwidth = ispLines.reduce(function (sum, row) { return sum + number(row.bandwidth_mbps); }, 0);
            const avgIspUtil = ispLines.length ? ispLines.reduce(function (sum, row) { return sum + number(row.utilization); }, 0) / ispLines.length : 0;
            setText('ck-isp-lines', results[5] ? ispLines.length : '未授权'); setText('ck-isp-bandwidth', results[5] ? '总带宽 ' + formatBandwidth(totalBandwidth) : '无访问权限');
            setText('ck-isp-util', results[5] ? avgIspUtil.toFixed(1) + '%' : '-');
            if (el('ck-isp-util-bar')) el('ck-isp-util-bar').style.width = percent(avgIspUtil) + '%';

            setText('ck-sites', dc ? number(dc.sites_count) : '未授权'); setText('ck-racks', dc ? number(dc.racks_count) : '未授权');
            setText('ck-posture-rack', dc ? number(dc.racks_count) + ' 柜' : '-');
            setText('ck-it-assets', assets ? number(assets.it_count) : '未授权'); setText('ck-server-assets', assets ? number(assets.server_count) : '未授权');
            const latestSync = syncRuns[0];
            setText('ck-posture-sync', latestSync ? statusLabel(latestSync.status) : (results[6] ? '暂无记录' : '未授权'));
            setText('ck-posture-backup', number(dashboard.backups_today) + ' 次');

            const posture = renderAlerts(dashboard, vmSummary, syncRuns);
            setText('ck-posture-state', posture);
            setText('ck-posture-summary', '在线设备 ' + online + '/' + total + ' · 今日备份 ' + number(dashboard.backups_today) + ' · 地址使用 ' + number(overall.in_use_ips) + ' · ISP线路 ' + ispLines.length);
            renderTasks(dashboard.recent_tasks || []);
            setText('cockpit-update', '更新于 ' + new Date().toLocaleTimeString('zh-CN', {hour12:false}));
        } finally {
            loading = false;
            if (refreshButton) { refreshButton.disabled = false; refreshButton.textContent = '立即刷新'; }
        }
    }

    function configureRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        const seconds = number(el('cockpit-interval').value);
        if (seconds > 0) refreshTimer = setInterval(loadCockpit, seconds * 1000);
    }

    function updateClock() { setText('cockpit-clock', new Date().toLocaleTimeString('zh-CN', {hour12:false})); }

    document.addEventListener('DOMContentLoaded', function () {
        el('cockpit-refresh').addEventListener('click', loadCockpit);
        el('cockpit-interval').addEventListener('change', configureRefresh);
        el('cockpit-fullscreen').addEventListener('click', async function () {
            try {
                if (!document.fullscreenElement) await document.documentElement.requestFullscreen(); else await document.exitFullscreen();
                this.textContent = document.fullscreenElement ? '退出全屏' : '全屏';
            } catch (error) { showToast('浏览器不允许进入全屏', 'warning'); }
        });
        document.addEventListener('fullscreenchange', function () { el('cockpit-fullscreen').textContent = document.fullscreenElement ? '退出全屏' : '全屏'; });
        document.addEventListener('visibilitychange', function () { if (!document.hidden) loadCockpit(); });
        updateClock(); setInterval(updateClock, 1000); configureRefresh(); loadCockpit();
    });
})();
