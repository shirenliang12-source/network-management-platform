const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let overlay, calls=[];
const nodes=new Map();
function element(selector){if(!nodes.has(selector))nodes.set(selector,{disabled:false,value:'pool-123',querySelectorAll:()=>[]});return nodes.get(selector);}
const snapshot={used:85,free:15,reserved:2,percent:85,warning:true,start:'192.0.2.10',end:'192.0.2.109',synced_at:'2026-09-10'};
const ctx=vm.createContext({
    document:{getElementById:()=>null,createElement:()=>({querySelector:element,remove(){}}),body:{appendChild(o){overlay=o;}}},
    window:{addEventListener(){}},setInterval(){},confirm:()=>true,
    escapeHtml:s=>String(s||'').replaceAll('<','&lt;').replaceAll('>','&gt;'),showToast:()=>{},
    API:{get:async url=>url==='/api/integrations/dhcp'?[{id:'pool-123',name:'<HQ>',server:'dhcp',scope:'192.0.2.0',mode:'DHCP'}]:{source_id:'pool-123',mode:'DHCP',snapshot},
         put:async(url,data)=>calls.push({url,data}),post:async(url,data)=>calls.push({url,data})},
});
vm.runInContext(fs.readFileSync('app/static/js/dhcp_integration.js','utf8'),ctx);
(async()=>{
    const html=ctx.dhcpStatistics({source_id:'pool-123',mode:'DHCP',snapshot,error:'<failed>'});
    assert.match(html,/&lt;failed&gt;/);assert.match(html,/85%/);assert.match(html,/上次成功/);
    await ctx.showPrefixDhcp(7);assert.equal(overlay.className,'modal-overlay active');
    assert.match(overlay.innerHTML,/&lt;HQ&gt;/);assert.match(overlay.innerHTML,/平台集成/);
    await element('[data-bind]').onclick({target:{disabled:false}});
    assert.equal(calls[0].url,'/api/ipam/prefixes/7/dhcp');assert.equal(calls[0].data.source_id,'pool-123');
    await element('[data-sync]').onclick({target:{disabled:false}});
    assert.equal(calls[1].url,'/api/ipam/prefixes/7/dhcp/sync');
    element('[data-source]').value='';
    await element('[data-bind]').onclick({target:{disabled:false}});
    assert.equal(calls[2].data.source_id,null);
    console.log('PASS: DHCP summary escaping, visible IPAM modal, binding, sync, unlink');
})().catch(e=>{console.error(e);process.exitCode=1;});
