const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(key){if(!nodes.has(key))nodes.set(key,{value:key==='[data-module]'?'0':'',textContent:'',disabled:false,querySelectorAll:()=>[],set innerHTML(v){this.html=v;if(key==='[data-field]')this.value=(v.match(/value="([^"]+)"/)||[])[1];},get innerHTML(){return this.html||'';}});return nodes.get(key);}
let overlay,calls=[],confirmed=true,reloaded=false;
const context=vm.createContext({location:{pathname:'/vms',reload(){reloaded=true;}},window:{addEventListener(){}},
    document:{getElementById:()=>null,createElement:()=>({querySelector:node,querySelectorAll:()=>[],remove(){},set innerHTML(v){this.html=v;}}),body:{appendChild(o){overlay=o;}}},
    escapeHtml:v=>String(v||''),confirm:()=>confirmed,
    API:{get:async()=>[{id:1,name:'one'},{id:2,name:'two'}],put:async(url,data)=>calls.push({url,data}),delete:async url=>{calls.push({url});if(url.endsWith('/2'))throw new Error('关联保护');}},
});
vm.runInContext(fs.readFileSync('app/static/js/bulk_inventory.js','utf8'),context);
(async()=>{
    await context.showBulkInventory();
    assert.equal(overlay.className,'modal-overlay active');
    node('[data-all]').onclick();
    node('[data-value]').value='用途';
    await node('[data-edit]').onclick();
    assert.equal(calls.length,2);assert.equal(calls[0].data.function,'用途');
    node('[data-all]').onclick();
    node('[data-search]').value='two';node('[data-search]').oninput();
    assert.match(node('[data-count]').textContent,/选中 0/);
    node('[data-all]').onclick();confirmed=false;await node('[data-delete]').onclick();assert.equal(calls.length,2);
    confirmed=true;await node('[data-delete]').onclick();assert.equal(calls.length,3);
    assert.match(node('[data-status]').textContent,/失败 1.*关联保护/);
    assert.match(node('[data-count]').textContent,/选中 1/);
    node('[data-close]').onclick();assert.equal(reloaded,true);
    console.log('PASS: batch selection, filter reset, explicit confirm, allowed field, per-row failure, refresh');
})().catch(e=>{console.error(e);process.exitCode=1;});
