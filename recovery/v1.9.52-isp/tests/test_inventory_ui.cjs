// Execute the actual page handlers against a minimal DOM; no production data.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = {};
const el = id => elements[id] ||= {innerHTML: '', value: ''};
let companies = [{company: 'Existing'}];
let target = null;
let source = 1;
let additions = 0;
const context = vm.createContext({
    console, Number, document: {
        getElementById: el,
        querySelectorAll: () => [{value: '10'}],
    },
    confirm: () => true,
    showToast() {}, escapeHtml: text => String(text || ''),
    loadCompanies() {}, loadGroups() {}, loadDevices() {},
    API: {
        async get(url) {
            if (url.endsWith('/companies')) return companies;
            if (url.includes('/neighbors')) return [{id: 10, name: 'next', ip: '192.0.2.2',
                category: '交换机', protocol: 'cdp', managed: !!target, managed_device_id: target}];
            throw new Error(url);
        },
        async put(url, data) { companies = data.names.map(company => ({company})); },
        async post(url, data) {
            assert.equal(url, `/api/devices/${source}/neighbors/add`);
            assert.deepEqual(Array.from(data.ids), [10]);
            additions++;
            target = source + 1;
            return {added_count: 1, skipped_count: 0, errors: []};
        },
    },
});
vm.runInContext(fs.readFileSync('app/static/js/device_inventory.js', 'utf8'), context);
// Load just the two independently testable detail-page functions.
const detail = fs.readFileSync('app/templates/device_detail.html', 'utf8');
const handlers = detail.slice(detail.indexOf('async function loadNeighbors()'), detail.indexOf('async function loadBackupHistory()'));
vm.runInContext('var deviceId = 1;\n' + handlers, context);
(async () => {
    await context.manageCompanies();
    assert.match(el('modalContainer').innerHTML, /class="modal-overlay active"/);
    assert.equal(el('companyNames').value, 'Existing');
    el('companyNames').value = 'Existing\nBranch';
    await context.saveCompanyCatalog();
    assert.equal(companies.length, 2);
    assert.equal(el('modalContainer').innerHTML, '');
    await context.showDeviceNeighbors(1);
    assert.match(el('modalContainer').innerHTML, /class="modal-overlay active"/);
    for (source = 1; source <= 2; source++) {
        context.deviceId = source;
        target = null;
        await context.loadNeighbors();
        assert.match(el('neighborsTable').innerHTML, /添加此设备/);
        const button = {disabled: false};
        await context.addDetailNeighbor(10, button);
        assert.equal(button.disabled, false);
        assert.match(el('neighborsTable').innerHTML, new RegExp(`href="/devices/${source + 1}"`));
        assert.match(el('neighborsTable').innerHTML, /进入设备继续发现/);
    }
    assert.equal(additions, 2);
    console.log('PASS: company modal visible/save; neighbor modal visible; A -> B -> C manual handlers');
})().catch(error => { console.error(error); process.exitCode = 1; });
