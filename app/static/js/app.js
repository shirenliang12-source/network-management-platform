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
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
}

function showLoading() {
    document.getElementById('loadingOverlay').classList.add('active');
}

function hideLoading() {
    document.getElementById('loadingOverlay').classList.remove('active');
}

function formatDateTime(dt) {
    if (!dt) return '-';
    const d = new Date(dt);
    return d.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
    });
}

function formatRelative(dt) {
    if (!dt) return '从未';
    const d = new Date(dt);
    const now = new Date();
    const diff = now - d;
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
        '/devices': 'nav-devices',
        '/topology': 'nav-topology',
        '/backups': 'nav-backups',
        '/changes': 'nav-changes',
        '/ip-inventory': 'nav-ip',
        '/ipam': 'nav-ipam',
        '/datacenter': 'nav-datacenter',
        '/assets': 'nav-assets',
        '/servers': 'nav-servers',
        '/vms': 'nav-vms',
        '/integrations': 'nav-integrations',
        '/accounts': 'nav-accounts',
        '/schedule': 'nav-schedule',
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
});
