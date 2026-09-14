const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const ctx = vm.createContext({escapeHtml: value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
vm.runInContext(fs.readFileSync('app/static/js/rack_view.js','utf8'), ctx);
const rack = {u_height:42, rack_number:'R1'};
const html = ctx.rackUHTML(rack, [{name:'switch',u_start:13,u_size:2}]);
const rows = [...html.matchAll(/data-rack-u="(\d+)"[\s\S]*?data-occupied="(true|false)"/g)];
assert.equal(rows.length,42);
assert.deepEqual(rows.filter(r=>r[2]==='true').map(r=>Number(r[1])),[14,13]);
assert.deepEqual(rows.map(r=>Number(r[1])), Array.from({length:42},(_,i)=>42-i));
assert.ok(!html.includes('margin:1px 0'));
assert.match(ctx.rackUHTML(rack,[{name:'bad',u_start:42,u_size:2}]),/U 位超界或无效/);
assert.match(ctx.rackUHTML(rack,[{name:'a',u_start:1,u_size:1},{name:'b',u_start:1,u_size:1}]),/占用冲突/);
assert.ok(!ctx.rackUHTML(rack,[{name:'<script>',u_start:1,u_size:1}]).includes('<script>'));
for (const page of ['dc','servers']) {
    const template = fs.readFileSync(`app/templates/${page}.html`,'utf8');
    assert.ok(template.includes('/static/js/rack_view.js'));
    assert.ok(!template.includes('function rackUHTML('));
}
console.log('PASS: U13–14, 42 shared grid rows, bounds, conflicts, escaping, both pages');
