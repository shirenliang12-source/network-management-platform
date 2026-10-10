const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/js/integrations.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.match(/function integrationMatchesState\(row, state\) \{[\s\S]*?\n\}/)[0], context);
for (const [row, state, expected] of [
    [{change_type:'new'}, 'new', true], [{change_type:'update', imported:true}, 'actionable', true],
    [{change_type:'unchanged', imported:true}, 'actionable', false],
    [{error:'conflict', change_type:'new'}, 'new', false], [{error:'failed'}, 'error', true],
    [{imported:true}, 'imported', true], [{imported:false}, 'imported', false],
]) assert.equal(context.integrationMatchesState(row, state), expected);
assert.match(source, /visibleVMs\.map/);
console.log('PASS import state filter, changed and error classification');
