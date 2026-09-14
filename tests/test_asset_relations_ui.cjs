const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let overlay, close = {}, calls = [];
const context = vm.createContext({
    Number, URLSearchParams, encodeURIComponent,
    window: {addEventListener() {}},
    document: {
        getElementById: () => overlay,
        createElement: () => ({querySelector: () => close, querySelectorAll: () => [], remove() { overlay = null; }}),
        body: {appendChild: element => { overlay = element; }},
    },
    escapeHtml: value => String(value || '').replaceAll('<','&lt;').replaceAll('>','&gt;'),
    showToast: message => { throw new Error(message); },
    API: {get: async url => {
        calls.push(url);
        return {root:{name:'VM <test>'}, notes:['只读'], groups:[{title:'宿主', items:[{kind:'server', id:9, name:'host', detail:'U1'}]}]};
    }},
});
vm.runInContext(fs.readFileSync('app/static/js/asset_relations.js','utf8'), context);
(async () => {
    await context.showAssetRelations('vm', 1);
    assert.equal(calls[0], '/api/vms/1/relations');
    assert.equal(overlay.className, 'modal-overlay active');
    assert.match(overlay.innerHTML, /VM &lt;test&gt;/);
    assert.match(overlay.innerHTML, /\/servers\?relation_kind=server&relation_id=9/);
    await context.showAssetRelations('server', 9);
    assert.equal(calls[1], '/api/assets/servers/9/relations');
    close.onclick();
    assert.equal(overlay, null);
    console.log('PASS: visible relation modal, escaped names, cross-module navigation, close');
})().catch(error => { console.error(error); process.exitCode = 1; });
