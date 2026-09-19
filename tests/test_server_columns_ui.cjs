const fs = require('node:fs');
const assert = require('node:assert/strict');
const html = fs.readFileSync('app/templates/servers.html', 'utf8');
const headers = html.match(/<thead>([\s\S]*?)<\/thead>/)[1];
assert.equal((headers.match(/<th[ >]/g) || []).length, 9);
assert.match(headers, /站点<\/th>\s*<th>IP 地址<\/th>\s*<th>U 位/);
assert.doesNotMatch(html, /colspan="8"/);
console.log('PASS: server IP header and nine-column alignment');
