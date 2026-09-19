const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('app/templates/ipam.html', 'utf8');
const source = html.match(/function switchTab\(name\)\{[\s\S]*?\n\}/)[0];
const tabs = ['overview', 'aggregates', 'prefixes', 'ips'];
const panels = Object.fromEntries(tabs.map(t => ['tab-' + t, {style: {}}]));
const active = {};
const buttons = tabs.map(t => ({classList: {toggle: (_, value) => active[t] = value}}));
const context = vm.createContext({document: {
  getElementById: id => panels[id], querySelectorAll: () => buttons,
}, loadStats(){}, loadAggregates(){}, loadPrefixes(){}, loadPrefixOptions(){}, loadIps(){}});
vm.runInContext("let currentIpamTab = 'overview';\n" + source, context);
for (const tab of tabs) {
  vm.runInContext(`switchTab('${tab}')`, context);
  assert.equal(vm.runInContext('currentIpamTab', context), tab);
  for (const other of tabs) {
    assert.equal(active[other], other === tab);
    assert.equal(panels['tab-' + other].style.display, other === tab ? 'block' : 'none');
  }
}
console.log('IPAM tab state and event-free navigation passed');
