const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/js/app.js', 'utf8');
const start = source.indexOf('function parsePlatformTime(');
const end = source.indexOf('function statusBadge(');
const ctx = vm.createContext({});
vm.runInContext(source.slice(start, end), ctx);
for (const input of ['2026-09-15T01:30:00', '2026-09-15 01:30:00',
    '2026-09-15T01:30:00Z', '2026-09-15T09:30:00+08:00']) {
    assert.equal(vm.runInContext(`parsePlatformTime('${input}').toISOString()`, ctx), '2026-09-15T01:30:00.000Z');
}
assert.equal(vm.runInContext("formatDateTime('bad')", ctx), '-');
assert.equal(vm.runInContext("formatRelative('bad')", ctx), '-');
assert.match(vm.runInContext('formatRelative(new Date(Date.now() + 120000).toISOString())', ctx), /分钟后$/);
assert.match(vm.runInContext('formatRelative(new Date(Date.now() - 120000).toISOString())', ctx), /分钟前$/);
console.log('PASS UTC timestamps, explicit offsets, invalid dates and future schedules');
