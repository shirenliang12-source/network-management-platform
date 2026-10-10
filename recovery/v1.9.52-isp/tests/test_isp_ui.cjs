const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = require('path');
const elements = {};
for (const name of ['search','summary','rows','form','error','editor-title','provider','number',
    'location','notes','networks','used','reserved','editor','save','add','cancel','refresh','linked']) {
    elements['isp-'+name] = {value:'', hidden:true, disabled:false, textContent:'', innerHTML:'',
        events:{}, addEventListener(event,fn){this.events[event]=fn;}, reset(){}, focus(){}};
}
let lastPayload, deleted=false;
const row = {id:1,provider:'<script>bad</script>',circuit_number:'A-1',networks:['203.0.113.0/29'],
    used_ips:['203.0.113.2'],reserved_ips:['203.0.113.1'],location:'机房A',notes:'测试',
    usable_count:'6',used_count:1,reserved_count:1,available_count:'4',utilization:16.67,
    ipam_linked:true,ipam_used_ips:['203.0.113.2'],ipam_reserved_ips:['203.0.113.1']};
const context = {document:{getElementById:id=>elements[id]}, encodeURIComponent,
    escapeHtml:value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
    confirm:()=>true,showToast(){},
    API:{get:async()=>deleted?[]:[row],post:async(url,payload)=>{lastPayload=payload;},
         put:async(url,payload)=>{lastPayload=payload;},delete:async()=>{deleted=true;}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app/static/js/isp.js'),'utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
    await tick();
    assert(elements['isp-rows'].innerHTML.includes('&lt;script&gt;'));
    assert(!elements['isp-rows'].innerHTML.includes('<script>'));
    await elements['isp-rows'].events.click({target:{closest:()=>({dataset:{edit:'1'}})}});
    assert.equal(elements['isp-editor'].hidden,false);
    assert.equal(elements['isp-location'].value,'机房A');
    assert(elements['isp-linked'].textContent.includes('203.0.113.2'));
    elements['isp-location'].value='机房B';
    elements['isp-used'].value='203.0.113.2\r\n203.0.113.3';
    await elements['isp-form'].events.submit({preventDefault(){}});
    assert.equal(lastPayload.location,'机房B');
    assert.equal(lastPayload.used_ips.length,2);
    assert.equal(elements['isp-save'].disabled,false);
    await elements['isp-rows'].events.click({target:{closest:()=>({dataset:{delete:'1'}})}});
    assert(deleted);
    assert(elements['isp-rows'].innerHTML.includes('暂无线路'));
    console.log('PASS ISP: escaped rendering, editable location, multiline IPs, save and confirmed deletion');
})().catch(error=>{console.error(error);process.exitCode=1;});
