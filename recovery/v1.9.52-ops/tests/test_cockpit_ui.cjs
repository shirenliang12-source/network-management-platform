const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'app', 'static', 'js', 'cockpit.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app', 'static', 'css', 'cockpit.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'app', 'templates', 'cockpit.html'), 'utf8');

for (const endpoint of ['/api/dashboard', '/api/ipam/prefix-utilization', '/api/vms/summary', '/api/dc/summary', '/api/assets/summary', '/api/isp', '/api/integrations/sync-runs?limit=12']) {
    assert.ok(js.includes(endpoint), `missing ${endpoint}`);
}
assert.ok(js.includes('setInterval(loadCockpit'));
assert.ok(js.includes('requestFullscreen'));
assert.ok(js.includes("response.status === 401"));
assert.ok(js.includes("if (!response.ok) return null"));
assert.ok(js.includes('renderAlerts'));
assert.ok(css.includes('.cockpit-grid-main'));
assert.ok(css.includes('@media (max-width: 760px)'));
assert.ok(html.includes('网络运营驾驶舱'));
assert.ok(html.includes('异常优先'));

console.log('PASS cockpit: protected data sources, auto refresh, fullscreen, alert posture, responsive layout');
