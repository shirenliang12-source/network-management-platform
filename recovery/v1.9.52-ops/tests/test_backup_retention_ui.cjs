const fs = require('node:fs'), assert = require('node:assert/strict');
const template = fs.readFileSync('app/templates/backups.html','utf8');
const script = fs.readFileSync('app/static/js/backup_retention.js','utf8');
for (const marker of ['backup-retention-card','global-retention-keep','retentionDeviceTable']) {
    assert.ok(template.includes(marker), marker);
}
for (const marker of ['loadBackupRetentionTable','saveGlobalBackupRetention','saveDeviceBackupRetention',
                      '/api/backups/retention/global','/api/backups/retention/devices/']) {
    assert.ok(script.includes(marker), marker);
}
assert.match(template, /0 表示全部保留/);
assert.match(template, /设备独立数量优先/);
console.log('PASS visible global/per-device backup retention controls');
