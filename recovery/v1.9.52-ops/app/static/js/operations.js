/* Additive operations UI. Never inject operator-provided strings as HTML. */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const request = async (method,url,data) => {
        const response=await fetch(url,{method,headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
        const result=await response.json();
        if (!response.ok) throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail||result));
        return result;
    };
    const get=url=>request('GET',url);
    const main=document.querySelector('.main-content');
    if (!main) return;
    function panel(title,html) {
        const div=document.createElement('section'); div.className='card'; div.style.cssText='padding:16px;margin:16px 0';
        div.innerHTML='<h3>'+esc(title)+'</h3>'+html; main.prepend(div); return div;
    }
    function modal(title,html) {
        const root=document.createElement('div'); root.className='modal-overlay active';
        root.innerHTML='<div class="modal" style="max-width:1100px;width:95%;max-height:90vh;overflow:auto"><div class="modal-header"><h3>'+esc(title)+'</h3><button data-close class="btn">关闭</button></div><div class="modal-body">'+html+'</div></div>';
        root.querySelector('[data-close]').onclick=()=>root.remove(); document.body.append(root); return root;
    }
    const options=(items,value,label)=>items.map(item=>'<option value="'+esc(value(item))+'">'+esc(label(item))+'</option>').join('');
    const error=(root,e)=>{let node=root.querySelector('[data-error]');if(!node){node=document.createElement('p');root.append(node);}node.textContent=e.message;node.style.color='#b91c1c';};
    const path=location.pathname;
    if (path==='/devices' || /^\/devices\/\d+$/.test(path)) {
        const root=panel('机柜位置与双向反查','<button class="btn" data-open>维护设备机柜位置</button><p>仅显式绑定，不根据同名或IP自动移动设备；机房资产的现有关联也可反查。</p>');
        root.querySelector('[data-open]').onclick=async()=>{
            try {
                const [devices,racks]=await Promise.all([get('/api/devices'),get('/api/devices/rack-options')]);
                const box=modal('设备机柜位置','<form><label>设备<select name="device" class="form-control">'+options(devices,d=>d.id,d=>d.name+' · '+d.ip_address)+'</select></label><label>机柜<select name="rack" class="form-control"><option value="">解除直接绑定</option>'+options(racks,r=>r.id,r=>r.name)+'</select></label><label>位置（如U13–14、侧挂）<input name="position" maxlength="100" class="form-control"></label><p data-current></p><button type="button" data-query class="btn">查看已有反查</button> <button class="btn btn-primary">保存绑定</button><p data-error></p></form>');
                const form=box.querySelector('form');
                const current=async()=>{
                    const [info,binding]=await Promise.all([get('/api/devices/'+form.elements.device.value+'/relations'),get('/api/devices/'+form.elements.device.value+'/rack-location')]);
                    form.elements.rack.value=binding.rack_id??'';
                    form.elements.position.value=binding.position||'';
                    box.querySelector('[data-current]').innerHTML=info.groups.map(g=>esc(g.title)+'：'+g.items.map(i=>'<a href="/datacenter?relation_kind='+encodeURIComponent(i.kind)+'&relation_id='+i.id+'">'+esc(i.name)+' '+esc(i.detail)+'</a>').join('；')).join('<br>')||'尚未绑定';
                };
                form.elements.device.value=path.split('/')[2]||devices[0]?.id||'';
                box.querySelector('[data-query]').onclick=()=>current().catch(e=>error(box,e));
                form.elements.device.onchange=()=>current().catch(e=>error(box,e));
                form.onsubmit=async e=>{e.preventDefault();try {
                    if(!confirm('确认修改设备机柜关系？')) return;
                    await request('PUT','/api/devices/'+form.elements.device.value+'/rack-location',{rack_id:form.elements.rack.value?Number(form.elements.rack.value):null,position:form.elements.position.value});
                    await current();
                } catch(e){error(box,e);}};
                await current();
            }catch(e){error(root,e);}
        };
    }
    if (path==='/backups') {
        const root=panel('备份记录筛选','<label>公司 <select data-company><option value="">全部公司</option></select></label> <label>分组 <select data-group><option value="">全部分组</option></select></label> <label>状态 <select data-status><option value="">全部状态</option></select></label> <label>开始日期 <input type="date" data-from></label> <label>结束日期 <input type="date" data-to></label> <button class="btn" data-filter>查询</button><p data-error></p>');
        window.backupFilterQuery=()=>{
            const p=new URLSearchParams();
            for(const [selector,key] of [['company','company'],['group','group_id'],['status','status']]) {
                const value=root.querySelector('[data-'+selector+']').value;if(value)p.set(key,value);
            }
            const from=root.querySelector('[data-from]').value,to=root.querySelector('[data-to]').value;
            if(from)p.set('date_from',new Date(from+'T00:00:00').toISOString());
            if(to){const end=new Date(to+'T00:00:00');end.setDate(end.getDate()+1);p.set('date_to',end.toISOString());}
            return '&'+p.toString();
        };
        root.querySelector('[data-filter]').onclick=()=>window.loadBackups();
        get('/api/backups/filter-options').then(filters=>{
            root.querySelector('[data-company]').innerHTML+=options(filters.companies,x=>x,x=>x);
            root.querySelector('[data-group]').innerHTML+=options(filters.groups,x=>x.id,x=>x.name);
            root.querySelector('[data-status]').innerHTML+=options(filters.statuses,x=>x,x=>x);
        }).catch(e=>error(root,e));
        const alerts=panel('备份失败告警（确认后可移除）','<button class="btn" data-refresh>刷新告警</button> <button class="btn" data-select-all>一键选择全部</button> <button class="btn btn-primary" data-batch-ack>确认选中</button> <button class="btn btn-danger" data-batch-delete>删除选中</button> <span data-selection>已选择 0 条</span><p>删除前必须先确认；只移除告警展示，原任务和审计历史永久保留。</p><div style="overflow:auto" data-rows></div><p data-error></p>');
        function selectedAlerts(){
            return Array.from(alerts.querySelectorAll('.backup-alert-check:checked')).map(box=>({
                task_id:Number(box.dataset.task),index:Number(box.dataset.index)
            }));
        }
        function showAlertSelection(){
            alerts.querySelector('[data-selection]').textContent='已选择 '+selectedAlerts().length+' 条';
        }
        async function loadAlerts(){
            try{
                const items=await get('/api/backups/alerts');
                alerts.querySelector('[data-rows]').innerHTML=items.length?'<table class="data-table"><thead><tr><th>选择</th><th>任务/时间</th><th>设备/IP</th><th>失败阶段</th><th>错误明细</th><th>状态/处理</th></tr></thead><tbody>'+items.map(r=>'<tr><td><input type="checkbox" class="backup-alert-check" data-task="'+r.task_id+'" data-index="'+r.index+'"></td><td>#'+r.task_id+'<br>'+esc(r.time)+'</td><td>'+esc(r.device_name)+'<br>'+esc(r.ip_address)+'</td><td>'+esc(r.stage)+'</td><td style="white-space:pre-wrap">'+esc(r.error)+'</td><td>'+esc(r.state)+' <button class="btn" data-task="'+r.task_id+'" data-index="'+r.index+'" data-action="'+(r.state==='open'?'ack':'delete')+'">'+(r.state==='open'?'确认':'删除告警')+'</button></td></tr>').join('')+'</tbody></table>':'<p class="empty-state">当前没有未处理的备份失败告警</p>';
                showAlertSelection();
            }catch(e){error(alerts,e);}
        }
        alerts.querySelector('[data-refresh]').onclick=loadAlerts;
        alerts.querySelector('[data-select-all]').onclick=()=>{
            const boxes=Array.from(alerts.querySelectorAll('.backup-alert-check'));
            const checked=boxes.some(box=>!box.checked);
            boxes.forEach(box=>box.checked=checked);showAlertSelection();
        };
        async function batchAlert(action){
            const items=selectedAlerts();
            if(!items.length)throw new Error('请先选择告警');
            const deleting=action==='delete';
            if(!confirm(deleting?'删除选中告警？未确认的告警将被拒绝，原任务和审计仍保留。':'确认选中的备份失败告警？'))return;
            const endpoint=deleting?'/api/backups/alerts/batch-delete':'/api/backups/alerts/batch-acknowledge';
            const result=await request('POST',endpoint,{items});
            alert(result.message);await loadAlerts();
        }
        alerts.querySelector('[data-batch-ack]').onclick=()=>batchAlert('ack').catch(e=>error(alerts,e));
        alerts.querySelector('[data-batch-delete]').onclick=()=>batchAlert('delete').catch(e=>error(alerts,e));
        alerts.onclick=async e=>{const b=e.target.closest('[data-action]');if(!b)return;try{
            if(!confirm(b.dataset.action==='ack'?'已确认该设备失败信息？':'从告警列表移除？原任务和审计仍保留。'))return;
            b.disabled=true;const url='/api/backups/alerts/'+b.dataset.task+'/'+b.dataset.index;
            await request(b.dataset.action==='ack'?'POST':'DELETE',url+(b.dataset.action==='ack'?'/acknowledge':''));
            await loadAlerts();
        }catch(e){error(alerts,e);b.disabled=false;}};
        alerts.onchange=e=>{if(e.target.matches('.backup-alert-check'))showAlertSelection();};
        loadAlerts();
    }
    if (path==='/integrations') {
        const root=panel('删除集成连接','<p>只删除本平台连接配置并禁用相关定时同步；不删除远端主机、虚拟机或已导入资产。</p><button class="btn" data-vcenter>删除当前选中的vCenter / ESXi连接</button> <button class="btn" data-zabbix>删除Zabbix连接</button><p data-error></p>');
        async function remove(source){
            if(!source||source==='new')throw new Error('请先选择已保存的连接');
            if(!confirm('确认删除连接 '+source+'？保留本地导入资产。'))return;
            const result=await request('DELETE','/api/integrations/sources/'+encodeURIComponent(source));
            alert(result.message);location.reload();
        }
        root.querySelector('[data-vcenter]').onclick=()=>remove(document.getElementById('vcenter-source')?.value).catch(e=>error(root,e));
        root.querySelector('[data-zabbix]').onclick=()=>remove('zabbix').catch(e=>error(root,e));
    }
    if (path==='/vms') {
        const root=panel('虚拟机状态分布','<p id="vm-other-states">正在读取各状态数量…</p><div data-states></div><p>默认只展示运行中；其它状态完整保留，点击分类独立查看。升级前被排除的对象需要重新发现/同步。</p>');
        window.renderVmStateCounts=s=>{
            const states=['运行中','已关机','挂起','模板','其他'];
            root.querySelector('[data-states]').innerHTML=states.map(state=>'<button class="btn" data-state="'+state+'">'+state+' '+(s.by_status[state]||0)+'</button>').join('')+'<button class="btn" data-state="">全部 '+s.total+'</button>';
            document.getElementById('vm-other-states').textContent=states.map(x=>x+' '+(s.by_status[x]||0)).join(' · ');
        };
        get('/api/vms/summary').then(window.renderVmStateCounts).catch(e=>error(root,e));
        root.onclick=e=>{const b=e.target.closest('[data-state]');if(b){document.getElementById('filterStatus').value=b.dataset.state;window.loadVms();}};
    }
    if (path==='/system/logs') {
        const root=panel('模块日志汇总与分析','<label>时间范围 <select data-days><option value="1">24小时</option><option value="7" selected>7天</option><option value="30">30天</option><option value="90">90天</option></select></label> <button class="btn" data-refresh>刷新汇总</button><p data-note></p><div data-summary></div><div data-events></div>');
        async function summary(){
            try{
                const data=await get('/api/logs/module-summary?days='+root.querySelector('[data-days]').value);
                root.querySelector('[data-note]').textContent=data.note;
                root.querySelector('[data-summary]').innerHTML='<table class="data-table"><thead><tr><th>模块</th><th>记录</th><th>失败</th><th>警告</th><th>最近事件</th><th>分析</th></tr></thead><tbody>'+data.modules.map(r=>'<tr><td><button class="btn" data-module="'+esc(r.module)+'">'+esc(r.label)+'</button></td><td>'+r.events+'</td><td>'+r.errors+'</td><td>'+r.warnings+'</td><td>'+esc(r.latest||'-')+'</td><td>'+esc(r.analysis)+' · '+esc(r.sources.join('/'))+'</td></tr>').join('')+'</tbody></table>';
            }catch(e){error(root,e);}
        }
        root.onclick=async e=>{const b=e.target.closest('[data-module]');if(!b)return;try{
            const data=await get('/api/logs/module-events?module='+encodeURIComponent(b.dataset.module)+'&days='+root.querySelector('[data-days]').value);
            root.querySelector('[data-events]').innerHTML='<h4>最近事件（各来源最多200条）</h4>'+data.map(r=>'<p><b>'+esc(r.time)+' · '+esc(r.source)+' · '+esc(r.status)+'</b><br><span style="white-space:pre-wrap">'+esc(r.message)+'</span></p>').join('');
        }catch(e){error(root,e);}};
        root.querySelector('[data-refresh]').onclick=summary;summary();
    }
    window.openIspAddresses=async lineId=>{
        const root=modal('ISP地址明细','<p>按CIDR分页自动生成全部可用地址；只保存已编辑元数据，不批量写入海量空记录。端口为人工台账，不代表实际探测结果。停用地址仍占位；IPAM占用不会被手工释放。</p><input data-search placeholder="完整IP地址"><button class="btn" data-find>查找 / 清空后重置</button><p data-total></p><div style="overflow:auto" data-table></div><button class="btn" data-prev>上一页</button> <button class="btn" data-next>下一页</button><p data-error></p>');
        let offset='0', next=null, history=[],records=[];
        async function load(){
            const data=await get('/api/isp/'+lineId+'/addresses?offset='+offset+'&address='+encodeURIComponent(root.querySelector('[data-search]').value.trim()));
            records=data.items;next=data.next_offset;
            root.querySelector('[data-total]').textContent='可用地址总数 '+data.total+'，起始偏移 '+offset+'；'+(data.ipam_linked?'已关联IPAM':'无IPAM权限，仅手工台账');
            root.querySelector('[data-prev]').disabled=!history.length;
            root.querySelector('[data-next]').disabled=!next;
            root.querySelector('[data-table]').innerHTML='<table class="data-table"><thead><tr><th>IP</th><th>本地/有效状态</th><th>IPAM</th><th>用途</th><th>端口</th><th>负责人</th><th>操作</th></tr></thead><tbody>'+records.map((r,i)=>'<tr><td>'+esc(r.address)+'</td><td>'+esc(r.status)+' / '+esc(r.effective_status)+'</td><td>'+esc(r.ipam_status||'-')+'</td><td>'+esc(r.purpose)+'</td><td>'+esc(r.ports)+'</td><td>'+esc(r.owner)+'</td><td><button class="btn" data-edit-ip="'+i+'">编辑</button></td></tr>').join('')+'</tbody></table>';
        }
        root.querySelector('[data-find]').onclick=()=>{offset='0';history=[];load().catch(e=>error(root,e));};
        root.querySelector('[data-prev]').onclick=()=>{offset=history.pop()||'0';load().catch(e=>error(root,e));};
        root.querySelector('[data-next]').onclick=()=>{if(next){history.push(offset);offset=next;load().catch(e=>error(root,e));}};
        root.onclick=e=>{const b=e.target.closest('[data-edit-ip]');if(!b)return;const row=records[Number(b.dataset.editIp)];
            const formBox=modal('编辑 '+row.address,'<form><label>状态<select name="status" class="form-control">'+options(['可用','使用中','预留','停用'],x=>x,x=>x)+'</select></label>'+[['purpose','用途',500],['ports','开放端口，如 TCP/443, UDP/53, TCP/8000-8010, ICMP',3000],['owner','负责人/使用位置',200],['notes','备注',5000]].map(([name,label,max])=>'<label>'+label+'<textarea name="'+name+'" maxlength="'+max+'" class="form-control"></textarea></label>').join('')+'<button class="btn btn-primary">保存</button><p data-error></p></form>');
            const form=formBox.querySelector('form');for(const key of ['status','purpose','ports','owner','notes'])form.elements[key].value=row[key]||'';
            form.onsubmit=async event=>{event.preventDefault();try{
                const payload={};for(const key of ['status','purpose','ports','owner','notes'])payload[key]=form.elements[key].value;
                await request('PUT','/api/isp/'+lineId+'/addresses/'+encodeURIComponent(row.address),payload);
                formBox.remove();await load();document.getElementById('isp-refresh')?.click();
            }catch(e){error(formBox,e);}};
        };
        try{await load();}catch(e){error(root,e);}
    };
})();
