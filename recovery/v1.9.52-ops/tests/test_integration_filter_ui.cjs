const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/js/integrations.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.match(/function integrationMatchesState\(row, state\) \{[\s\S]*?\n\}/)[0], context);
vm.runInContext(source.match(/function integrationStateCounts\(result\) \{[\s\S]*?\n\}/)[0], context);
vm.runInContext(source.match(/function integrationMatchesPower\(row, power\) \{[\s\S]*?\n\}/)[0], context);
for (const [row, state, expected] of [
    [{change_type:'new'}, 'new', true], [{change_type:'update', imported:true}, 'actionable', true],
    [{change_type:'unchanged', imported:true}, 'actionable', false],
    [{error:'conflict', change_type:'new'}, 'new', false], [{error:'failed'}, 'error', true],
    [{imported:true}, 'imported', true], [{imported:false}, 'imported', false],
]) assert.equal(context.integrationMatchesState(row, state), expected);
const inventory = {vms:[
    {status:'运行中'}, {status:'已关机'}, {status:'挂起'}, {status:'模板'}, {status:'unknown'}, {error:'denied'},
]};
assert.deepEqual({...context.integrationStateCounts(inventory)}, {'运行中':1,'已关机':1,'挂起':1,'模板':1,'其他':1});
assert.equal(context.integrationMatchesPower({status:'已关机'}, '已关机'), true);
assert.equal(context.integrationMatchesPower({status:'运行中'}, '已关机'), false);
assert.equal(context.integrationMatchesPower({status:'unknown'}, '其他'), true);
assert.match(source, /visibleVMs\.map/);
assert.match(fs.readFileSync('app/templates/integrations.html','utf8'), /integration-power-filter/);
console.log('PASS import and power-state filters, changed/error classification');
