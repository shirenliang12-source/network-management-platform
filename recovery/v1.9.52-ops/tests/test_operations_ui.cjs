const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(require('path').join(__dirname,'../app/static/js/operations.js'),'utf8');
function node(){
  return {style:{},value:'',innerHTML:'',textContent:'',children:[],nodes:{},
    querySelector(s){return this.nodes[s] ||= node();},
    querySelectorAll(){return [];},
    append(n){this.children.push(n);},prepend(n){this.children.unshift(n);},remove(){}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function page(path){
  const main=node(),body=node(),ids={},calls=[];
  const context={URLSearchParams,Date,encodeURIComponent,console,confirm:()=>true,alert(){},
    location:{pathname:path,reload(){}},
    document:{body,querySelector:()=>main,createElement:()=>node(),getElementById:id=>ids[id] ||= node()},
    fetch:async(url,opt)=>{calls.push([url,opt]);let data={};
      if(url.includes('filter-options'))data={companies:['<HQ>'],groups:[{id:1,name:'G'}],statuses:['online']};
      else if(url.includes('/alerts'))data=[];
      else if(url.includes('/vms/summary'))data={total:8,by_status:{'运行中':2,'已关机':3,'挂起':1,'模板':1,'其他':1}};
      else if(url.includes('/module-summary'))data={note:'test',modules:[{module:'devices',label:'设备',events:1,errors:1,warnings:0,sources:['审计'],analysis:'failure'}]};
      else if(url.includes('/module-events'))data=[{time:'now',source:'audit',status:'failed',message:'<script>bad</script>'}];
      else if(url.includes('/addresses'))data={items:[{address:'192.0.2.1',status:'可用',purpose:'<bad>'}],total:'254',next_offset:'100',ipam_linked:true};
      return {ok:true,json:async()=>data};}};
  context.window=context;context.loadBackups=()=>{};context.showBackupRetention=()=>{};context.loadVms=()=>{};
  vm.runInNewContext(source,context);await tick();await tick();
  return {context,main,body,ids,calls};
}
(async()=>{
  let p=await page('/backups'),filters=p.main.children[1];
  assert(filters.querySelector('[data-company]').innerHTML.includes('&lt;HQ&gt;'));
  filters.querySelector('[data-company]').value='HQ';
  filters.querySelector('[data-from]').value='2026-01-02';
  filters.querySelector('[data-to]').value='2026-01-03';
  const query=new URLSearchParams(p.context.backupFilterQuery().slice(1));
  assert.equal(query.get('company'),'HQ');
  assert.equal(new Date(query.get('date_to')).getTime()-new Date(query.get('date_from')).getTime(),2*86400000);
  assert(source.includes('/api/backups/alerts/batch-acknowledge'));
  assert(source.includes('/api/backups/alerts/batch-delete'));
  assert(source.includes('一键选择全部'));
  p=await page('/vms');
  assert(p.main.children[0].querySelector('[data-states]').innerHTML.includes('已关机 3'));
  p.main.children[0].onclick({target:{closest:()=>({dataset:{state:'模板'}})}});
  assert.equal(p.ids.filterStatus.value,'模板');
  p.context.renderVmStateCounts({total:10,by_status:{'模板':5}});
  assert(p.main.children[0].querySelector('[data-states]').innerHTML.includes('模板 5'));
  p=await page('/system/logs');
  await p.main.children[0].onclick({target:{closest:()=>({dataset:{module:'devices'}})}});
  assert(p.main.children[0].querySelector('[data-events]').innerHTML.includes('&lt;script&gt;'));
  p=await page('/isp');await p.context.openIspAddresses(1);
  const box=p.body.children[0];
  assert(box.querySelector('[data-table]').innerHTML.includes('&lt;bad&gt;'));
  await box.querySelector('[data-next]').onclick();await tick();
  assert(p.calls.some(([url])=>url.includes('offset=100')));
  console.log('PASS operations: date filters, bulk backup alerts, VM state switch/refresh, log escaping, ISP paging');
})().catch(e=>{console.error(e);process.exitCode=1;});
