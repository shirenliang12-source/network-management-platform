const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = require('path');
const elements = {};
const names = [
    'search','summary','rows','form','error','editor-title','provider','number','bandwidth',
    'purpose','location','notes','networks','used','reserved','editor','save','add','cancel',
    'refresh','linked','filter-purpose','filter-location','filter-company','filter-function',
    'filter-region','filter-clear','company-group','function-group','region-group',
    'groups','groups-button','groups-save','groups-cancel','groups-error',
    'company-groups','function-groups','region-groups','dashboard','dashboard-customize',
    'dashboard-settings','dashboard-options','dashboard-save','import','import-file','import-mode',
];
for (const name of names) {
    elements['isp-'+name] = {
        value:'', hidden:true, disabled:false, textContent:'', innerHTML:'', files:[],
        events:{}, addEventListener(event,fn){this.events[event]=fn;}, reset(){}, focus(){}, click(){this.clicked=true;}
    };
}
let lastPayload, deleted=false, groupsSaved=false;
const row = {
    id:1,provider:'<script>bad</script>',circuit_number:'A-1',bandwidth_mbps:1000,
    networks:['203.0.113.0/29'],used_ips:['203.0.113.2'],reserved_ips:['203.0.113.1'],
    company_group:'集团',function_group:'互联网出口',region_group:'华东',
    purpose:'公网出口',location:'机房A',notes:'测试',usable_count:'6',used_count:1,
    reserved_count:1,available_count:'4',utilization:16.67,ipam_linked:true,
    ipam_used_ips:['203.0.113.2'],ipam_reserved_ips:['203.0.113.1']
};
const dashboardChecks = [{value:'line_count'},{value:'by_bandwidth'}];
const context = {
    document:{
        getElementById:id=>elements[id],
        querySelectorAll:()=>dashboardChecks,
    },
    URLSearchParams, encodeURIComponent,
    escapeHtml:value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
    escapeAttr:value=>String(value).replace(/&/g,'&amp;').replace(/"/g,'&quot;'),
    confirm:()=>true,showToast(){},FormData:class{append(){}},
    fetch:async()=>({ok:true,json:async()=>({message:'imported'})}),
    API:{
        get:async url=>{
            if(url.includes('filter-options'))return {purpose:['公网出口'],location:['机房A'],company:['集团'],function:['互联网出口'],region:['华东']};
            if(url.includes('dashboard-settings'))return {widgets:['line_count','by_purpose','by_location']};
            if(url.endsWith('/groups'))return {company:['集团'],function:['互联网出口'],region:['华东']};
            return deleted?[]:[row];
        },
        post:async(url,payload)=>{lastPayload=payload;},
        put:async(url,payload)=>{
            lastPayload=payload;
            if(url.endsWith('/groups')){groupsSaved=true;return payload;}
            if(url.includes('dashboard-settings'))return payload;
            return {};
        },
        delete:async()=>{deleted=true;}
    }
};
context.window=context;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app/static/js/isp.js'),'utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
    await tick();await tick();
    assert(elements['isp-rows'].innerHTML.includes('&lt;script&gt;'));
    assert(!elements['isp-rows'].innerHTML.includes('<script>'));
    assert(elements['isp-rows'].innerHTML.includes('1 Gbps'));
    assert(elements['isp-dashboard'].innerHTML.includes('线路数量'));
    assert(elements['isp-dashboard'].innerHTML.includes('公网出口'));
    await elements['isp-rows'].events.click({target:{closest:()=>({dataset:{edit:'1'}})}});
    assert.equal(elements['isp-editor'].hidden,false);
    assert.equal(elements['isp-purpose'].value,'公网出口');
    assert.equal(elements['isp-location'].value,'机房A');
    assert.equal(elements['isp-bandwidth'].value,1000);
    elements['isp-purpose'].value='云专线';
    elements['isp-location'].value='机房B';
    elements['isp-bandwidth'].value='200';
    elements['isp-used'].value='203.0.113.2\r\n203.0.113.3';
    await elements['isp-form'].events.submit({preventDefault(){}});
    assert.equal(lastPayload.purpose,'云专线');
    assert.equal(lastPayload.location,'机房B');
    assert.equal(lastPayload.bandwidth_mbps,200);
    await elements['isp-groups-button'].events.click();
    await tick();
    assert.equal(elements['isp-company-groups'].value,'集团');
    elements['isp-company-groups'].value='集团\n子公司A';
    await elements['isp-groups-save'].events.click();
    assert(groupsSaved);
    assert.deepEqual(lastPayload.company,['集团','子公司A']);
    assert(fs.readFileSync('app/templates/isp.html','utf8').includes('/api/isp/export'));
    assert(fs.readFileSync('app/templates/isp.html','utf8').includes('一键导入 CSV'));
    await elements['isp-rows'].events.click({target:{closest:()=>({dataset:{delete:'1'}})}});
    assert(deleted);
    assert(elements['isp-rows'].innerHTML.includes('暂无线路'));
    console.log('PASS ISP dashboard, grouping, bandwidth, filters, import/export UI and escaped rendering');
})().catch(error=>{console.error(error);process.exitCode=1;});
