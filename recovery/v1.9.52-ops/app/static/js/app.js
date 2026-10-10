// Common utilities
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text).replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function escapeAttr(text) {
    return escapeHtml(text)
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const API = {
    get: (url) => _request('GET', url),
    post: (url, data) => _request('POST', url, data),
    put: (url, data) => _request('PUT', url, data),
    delete: (url) => _request('DELETE', url),
};

// Shared request wrapper: throw on error, and bounce to /login when the
// session has expired (401) — except for /api/auth/* which handle 401 itself.
async function _request(method, url, data) {
    const opts = { method, headers: {} };
    if (data !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(data);
    }
    const res = await fetch(url, opts);
    if (res.status === 401 && !url.startsWith('/api/auth/')) {
        window.location.href = '/login';
        throw new Error('登录已过期，请重新登录');
    }
    if (res.status === 403 && !url.startsWith('/api/auth/')) {
        window.location.href = '/forbidden';
        throw new Error('无权限访问该模块');
    }
    if (!res.ok) throw new Error(await _errDetail(res));
    return res.json();
}

// Extract a human-readable error message from a failed API response.
async function _errDetail(res) {
    let detail = `HTTP ${res.status}`;
    try {
        const j = await res.json();
        if (j && j.detail) {
            detail = (typeof j.detail === 'string') ? j.detail : JSON.stringify(j.detail);
        }
    } catch (e) { /* ignore parse errors */ }
    return detail;
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
}

function showLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.add('active');
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('active');
}

// Database timestamp fields are UTC even when legacy JSON omits the suffix.
function parsePlatformTime(dt) {
    if (typeof dt === 'string' && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(dt)) {
        dt = dt.replace(' ', 'T');
        if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(dt)) dt += 'Z';
    }
    return new Date(dt);
}

function formatDateTime(dt) {
    if (!dt) return '-';
    const d = parsePlatformTime(dt);
    if (Number.isNaN(d.getTime())) return '-';
    return d.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
    });
}

function formatRelative(dt) {
    if (!dt) return '从未';
    const d = parsePlatformTime(dt);
    if (Number.isNaN(d.getTime())) return '-';
    const now = new Date();
    const diff = now - d;
    if (diff < 0) {
        const minutesUntil = Math.ceil(-diff / 60000);
        if (minutesUntil >= 1440) return `${Math.ceil(minutesUntil / 1440)}天后`;
        if (minutesUntil >= 60) return `${Math.ceil(minutesUntil / 60)}小时后`;
        return `${minutesUntil}分钟后`;
    }
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    if (days > 0) return `${days}天前`;
    if (hours > 0) return `${hours}小时前`;
    if (minutes > 0) return `${minutes}分钟前`;
    return '刚刚';
}

function statusBadge(status) {
    const map = {
        'online': '<span class="badge badge-success">在线</span>',
        'offline': '<span class="badge badge-danger">离线</span>',
        'unknown': '<span class="badge badge-secondary">未知</span>',
        'success': '<span class="badge badge-success">成功</span>',
        'failed': '<span class="badge badge-danger">失败</span>',
        'partial': '<span class="badge badge-warning">部分成功</span>',
        'running': '<span class="badge badge-info">运行中</span>',
        'discovered': '<span class="badge badge-info">已发现</span>',
    };
    return map[status] || `<span class="badge badge-secondary">${escapeHtml(status)}</span>`;
}

function deviceTypeLabel(type) {
    const map = {
        'cisco_ios': 'Cisco IOS',
        'cisco_ios_xe': 'Cisco IOS-XE',
        'cisco_nxos': 'Cisco NX-OS',
        'cisco_wlc': 'Cisco WLC',
        'cisco_ap': 'Cisco AP',
    };
    return map[type] || escapeHtml(type);
}

// Set active nav item
(function() {
    const path = window.location.pathname;
    const navMap = {
        '/': 'nav-dashboard',
        '/cockpit': 'nav-cockpit',
        '/devices': 'nav-devices',
        '/topology': 'nav-topology',
        '/backups': 'nav-backups',
        '/changes': 'nav-changes',
        '/ip-inventory': 'nav-ip',
        '/ipam': 'nav-ipam',
        '/isp': 'nav-isp',
        '/datacenter': 'nav-datacenter',
        '/assets': 'nav-assets',
        '/servers': 'nav-servers',
        '/vms': 'nav-vms',
        '/integrations': 'nav-integrations',
        '/accounts': 'nav-accounts',
        '/schedule': 'nav-schedule',
        '/system/logs': 'nav-logs',
        '/settings': 'nav-settings',
    };
    // Find matching nav
    for (const [route, navId] of Object.entries(navMap)) {
        if (path === route || (route !== '/' && path.startsWith(route))) {
            const el = document.getElementById(navId);
            if (el) el.classList.add('active');
        }
    }
})();

// ---- 当前用户信息 + 按模块隐藏无权限导航 ----
(async function initCurrentUser() {
    try {
        const me = await API.get('/api/auth/me');
        const nameEl = document.getElementById('sideUsername');
        if (nameEl) nameEl.textContent = me.username + (me.is_superuser ? '（超级）' : '');
        const avatar = document.getElementById('sideAvatar');
        if (avatar) avatar.textContent = (me.username || 'A').slice(0, 1).toUpperCase();
        // 非超级管理员：隐藏未被授权的导航项
        if (!me.is_superuser) {
            const allowed = new Set(me.modules || []);
            document.querySelectorAll('.sidebar-nav a[data-module]').forEach((a) => {
                const mod = a.getAttribute('data-module');
                if (!allowed.has(mod)) a.style.display = 'none';
            });
        }
    } catch (e) {
        // 未登录等：交由 /login 重定向处理
    }
})();

// ---- 退出登录 ----
document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('logoutBtn');
    if (btn) {
        btn.addEventListener('click', async function (e) {
            e.preventDefault();
            try { await API.post('/api/auth/logout'); } catch (e) {}
            window.location.href = '/login';
        });
    }

    initApplicationShell();
});

function initApplicationShell() {
    const sidebar = document.getElementById('appSidebar');
    const scrim = document.getElementById('mobileScrim');
    const toggle = document.getElementById('sidebarToggle');
    const close = document.getElementById('sidebarClose');
    const search = document.getElementById('navSearch');
    const navEmpty = document.getElementById('navEmpty');

    function setNav(open) {
        if (!sidebar) return;
        sidebar.classList.toggle('active', open);
        if (scrim) scrim.classList.toggle('active', open);
        document.body.classList.toggle('nav-open', open);
        if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (toggle) toggle.addEventListener('click', () => setNav(!sidebar.classList.contains('active')));
    if (close) close.addEventListener('click', () => setNav(false));
    if (scrim) scrim.addEventListener('click', () => setNav(false));
    if (sidebar) sidebar.querySelectorAll('a[href]').forEach((link) => link.addEventListener('click', () => setNav(false)));

    if (search) {
        search.addEventListener('input', () => {
            const query = search.value.trim().toLocaleLowerCase('zh-CN');
            let visible = 0;
            document.querySelectorAll('#sidebarNav a').forEach((link) => {
                const permitted = link.style.display !== 'none';
                const matched = !query || link.textContent.toLocaleLowerCase('zh-CN').includes(query);
                link.hidden = !(permitted && matched);
                if (!link.hidden) visible += 1;
            });
            document.querySelectorAll('#sidebarNav .nav-section-label').forEach((label) => {
                let node = label.nextElementSibling;
                let hasVisible = false;
                while (node && !node.classList.contains('nav-section-label')) {
                    if (node.matches && node.matches('a') && !node.hidden && node.style.display !== 'none') hasVisible = true;
                    node = node.nextElementSibling;
                }
                label.hidden = !hasVisible;
            });
            if (navEmpty) navEmpty.hidden = visible > 0;
        });
    }

    const title = document.querySelector('.page-header h1');
    const currentPage = document.getElementById('currentPageTitle');
    if (title && currentPage) {
        const clean = title.textContent.trim().replace(/^[^\w\u4e00-\u9fff]+/u, '').trim();
        currentPage.textContent = clean || '工作台';
        document.title = `${clean || '工作台'} · Cisco Network Manager`;
    }
    const date = document.getElementById('topbarDate');
    if (date) date.textContent = new Intl.DateTimeFormat('zh-CN', {month:'long', day:'numeric', weekday:'short'}).format(new Date());

    initModuleWorkspace();
    enhanceUi(document);
    const modalRoot = document.getElementById('modalContainer');
    if (modalRoot && typeof MutationObserver !== 'undefined') {
        new MutationObserver((records) => records.forEach((record) => record.addedNodes.forEach((node) => {
            if (node.nodeType === 1) enhanceUi(node);
        }))).observe(modalRoot, {childList: true, subtree: true});
    }

    document.addEventListener('keydown', (event) => {
        const editing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement && document.activeElement.tagName);
        if (event.key === '/' && !editing && search) {
            event.preventDefault();
            if (window.matchMedia('(max-width: 900px)').matches) setNav(true);
            search.focus();
        }
        if (event.key === 'Escape') {
            const modal = document.querySelector('.modal-overlay.active');
            if (modal) {
                const closeButton = modal.querySelector('[data-close], .modal-header .btn');
                if (closeButton) closeButton.click(); else modal.classList.remove('active');
            }
            setNav(false);
        }
    });
}

const MODULE_PRESENTATION = {
    '/': ['运营总览', '集中查看平台运行状态与需要优先处理的事项。', ['系统健康', '待处理异常', '今日任务']],
    '/devices': ['设备管理', '统一掌握网络设备状态、连接能力和位置关联。', ['在线状态', '采集连接', '机柜与分组']],
    '/topology': ['网络拓扑', '从邻居关系了解链路覆盖和未纳管节点。', ['链路覆盖', '邻居关系', '未入库节点']],
    '/backups': ['配置保障', '关注备份结果、失败设备和配置保留策略。', ['备份结果', '失败告警', '保留策略']],
    '/changes': ['变更治理', '区分预期与异常变化，确认设备是否偏离基线。', ['待确认变更', '异常变化', '基线偏离']],
    '/ip-inventory': ['地址清单', '维护网段台账以及设备、公司和安全区域关联。', ['网段台账', '设备关联', '导入质量']],
    '/ipam': ['地址规划', '从空间利用、地址占用和DHCP来源查看地址健康度。', ['空间利用', '地址占用', 'DHCP同步']],
    '/isp': ['线路运营', '综合查看线路容量、业务用途和实际使用位置。', ['线路容量', '用途与位置', '地址利用']],
    '/datacenter': ['数据中心', '统一查看站点、机柜容量和设备落位关系。', ['站点机柜', 'U位容量', '设备落位']],
    '/assets': ['资产管理', '跟踪其他资产状态、位置和网络设备关联。', ['资产状态', '机柜定位', '设备关联']],
    '/servers': ['服务器与存储', '集中查看硬件状态、管理地址和虚拟化承载关系。', ['硬件状态', 'IP与机柜', '虚拟化关联']],
    '/vms': ['虚拟机系统', '按运行状态查看虚拟机、宿主资源和地址占用。', ['运行状态', '宿主与存储', 'IP占用']],
    '/integrations': ['平台集成', '确认外部平台连接健康、同步变化和导入结果。', ['连接健康', '同步变化', '导入状态']],
    '/accounts': ['账号密码', '管理运维凭据来源、使用范围和设备关联。', ['凭据来源', '使用范围', '设备关联']],
    '/schedule': ['自动任务', '掌握任务启用情况、下次运行和最近执行结果。', ['任务启用', '运行计划', '最近结果']],
    '/system/logs': ['运行日志', '按模块汇总异常、任务故障和管理操作审计。', ['模块异常', '任务故障', '操作审计']],
    '/settings': ['系统设置', '集中维护账号权限、采集策略和平台运行配置。', ['账号权限', '采集策略', '系统配置']],
};

function presentationForPath(path) {
    if (/^\/devices\/\d+/.test(path)) {
        return ['设备详情', '汇总单台设备的基础状态、邻居关系和配置历史。', ['基础状态', '邻居关系', '配置历史']];
    }
    return MODULE_PRESENTATION[path] || null;
}

function initModuleWorkspace() {
    const pageHeader = document.querySelector('#mainContent > .page-header');
    if (!pageHeader || pageHeader.dataset.workspaceReady === 'true') return;
    const presentation = presentationForPath(window.location.pathname);
    if (!presentation) return;
    pageHeader.dataset.workspaceReady = 'true';

    const title = pageHeader.querySelector('h1');
    if (title) {
        let heading = title.parentElement;
        if (heading === pageHeader) {
            heading = document.createElement('div');
            heading.className = 'page-heading';
            pageHeader.insertBefore(heading, title);
            heading.appendChild(title);
        } else {
            heading.classList.add('page-heading');
        }
        if (!heading.querySelector('.page-subtitle') && !heading.querySelector('p')) {
            const subtitle = document.createElement('p');
            subtitle.className = 'page-subtitle';
            subtitle.textContent = presentation[1];
            heading.appendChild(subtitle);
        }
    }

    const overview = document.createElement('section');
    overview.className = 'module-overview';
    overview.setAttribute('aria-label', `${presentation[0]}管理重点`);
    const copy = document.createElement('div');
    copy.className = 'module-overview-copy';
    const icon = document.createElement('span');
    icon.className = 'module-overview-icon'; icon.textContent = '◎'; icon.setAttribute('aria-hidden', 'true');
    const copyText = document.createElement('div');
    const copyTitle = document.createElement('strong'); copyTitle.textContent = `${presentation[0]}视图`;
    const copyDescription = document.createElement('span'); copyDescription.textContent = presentation[1];
    copyText.append(copyTitle, copyDescription); copy.append(icon, copyText);
    const focus = document.createElement('div'); focus.className = 'module-focus';
    const focusLabel = document.createElement('span'); focusLabel.className = 'module-focus-label'; focusLabel.textContent = '管理重点'; focus.appendChild(focusLabel);
    presentation[2].forEach((item) => { const tag = document.createElement('span'); tag.className = 'module-focus-item'; tag.textContent = item; focus.appendChild(tag); });
    overview.append(copy, focus);
    pageHeader.insertAdjacentElement('afterend', overview);

    const cards = Array.from(document.querySelectorAll('#mainContent .card')).filter((card) => !card.classList.contains('stat-card'));
    const sections = [];
    cards.forEach((card, index) => {
        const header = card.querySelector(':scope > .card-header');
        if (!header) return;
        const clone = header.cloneNode(true);
        clone.querySelectorAll('button, a, input, select').forEach((node) => node.remove());
        const label = clone.textContent.replace(/\s+/g, ' ').trim();
        if (!label) return;
        const id = `module-section-${index + 1}`;
        card.id = card.id || id;
        card.classList.add('module-section');
        sections.push([card.id, label.length > 18 ? `${label.slice(0, 18)}…` : label]);
    });
    if (sections.length >= 2) {
        const nav = document.createElement('nav');
        nav.className = 'section-jumpbar'; nav.setAttribute('aria-label', '本页内容导航');
        sections.forEach(([id, label]) => { const link = document.createElement('a'); link.href = `#${id}`; link.textContent = label; nav.appendChild(link); });
        overview.insertAdjacentElement('afterend', nav);
    }
}

function enhanceUi(root) {
    const tables = [];
    if (root.matches && root.matches('table.data-table')) tables.push(root);
    if (root.querySelectorAll) tables.push(...root.querySelectorAll('table.data-table'));
    tables.forEach((table) => {
        if (table.dataset.uiEnhanced === 'true') return;
        table.dataset.uiEnhanced = 'true';
        table.querySelectorAll('thead th').forEach((th) => th.setAttribute('scope', 'col'));
        const container = table.closest('.table-container');
        if (container && !container.hasAttribute('tabindex')) {
            container.setAttribute('tabindex', '0');
            container.setAttribute('role', 'region');
            const heading = container.closest('.card')?.querySelector('.card-header');
            container.setAttribute('aria-label', `${heading ? heading.textContent.trim() : '数据'}表格，可横向滚动`);
        }
        const tbody = table.querySelector('tbody');
        if (container && tbody) {
            const summary = document.createElement('div');
            summary.className = 'table-summary';
            const label = document.createElement('span'); label.textContent = '当前视图';
            const count = document.createElement('strong'); summary.append(label, count);
            container.insertBefore(summary, table);
            const updateCount = () => {
                const rows = Array.from(tbody.querySelectorAll(':scope > tr'));
                const records = rows.filter((row) => !row.querySelector('.empty-state') && !row.textContent.includes('加载中'));
                count.textContent = records.length ? `${records.length} 条记录` : '暂无记录';
            };
            updateCount();
            if (typeof MutationObserver !== 'undefined') new MutationObserver(updateCount).observe(tbody, {childList: true});
        }
    });

    const modals = [];
    if (root.matches && root.matches('.modal')) modals.push(root);
    if (root.querySelectorAll) modals.push(...root.querySelectorAll('.modal'));
    modals.forEach((modal) => {
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        const heading = modal.querySelector('.modal-header h2, .modal-header h3');
        if (heading) {
            if (!heading.id) heading.id = `modal-title-${Math.random().toString(36).slice(2, 9)}`;
            modal.setAttribute('aria-labelledby', heading.id);
        }
    });
}
