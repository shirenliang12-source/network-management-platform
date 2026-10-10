"""Operations iteration tests: isolated DB, no services or remote devices."""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app import operations, isp
from app.database import Base, get_db
from app.models import Device, DCSite, DCRack, ServerAsset, TaskLog, ConfigBackup, SystemSetting, AuditLog, DataLog, VMInstance, ScheduleConfig
from app.routers import backups
from app.services.asset_relations import relations
from app.services.backup_retention import policy, excess


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine)
        self.user=SimpleNamespace(username='tester',is_superuser=True,get_modules=lambda:[])
        app=FastAPI()
        @app.middleware('http')
        async def identity(request,call_next):
            request.state.user=self.user
            return await call_next(request)
        app.include_router(operations.router)
        app.include_router(backups.router)
        app.include_router(isp.router)
        app.dependency_overrides[get_db]=lambda:self.db
        self.client=TestClient(app)
        self.device=Device(name='SW1',ip_address='192.0.2.10',company='HQ',status='online')
        self.db.add(self.device);self.db.commit()

    def tearDown(self):
        self.client.close();self.db.close();self.engine.dispose()

    def test_rack_bidirectional_and_permissions(self):
        site=DCSite(name='DC');self.db.add(site);self.db.flush()
        rack=DCRack(site_id=site.id,rack_number='A01',u_height=42);self.db.add(rack);self.db.commit()
        url=f'/api/devices/{self.device.id}/rack-location'
        self.assertEqual(self.client.put(url,json={'rack_id':rack.id,'u_start':13,'u_size':2}).status_code,200)
        location=self.client.get(url).json()
        self.assertEqual((location['u_start'],location['u_size']),(13,2))
        self.assertEqual(location['position'],'U13–U14')
        listed=self.client.get('/api/devices/rack-locations').json()
        self.assertEqual((listed[0]['rack_id'],listed[0]['u_start'],listed[0]['u_size']),(rack.id,13,2))
        self.assertIn(self.device.id,[n['id'] for g in relations(self.db,'rack',rack.id)['groups'] for n in g['items'] if n['kind']=='device'])
        self.assertIn(rack.id,[n['id'] for g in relations(self.db,'device',self.device.id)['groups'] for n in g['items'] if n['kind']=='rack'])
        second=Device(name='SW2',ip_address='192.0.2.11');self.db.add(second);self.db.commit()
        self.assertEqual(self.client.put(f'/api/devices/{second.id}/rack-location',json={'rack_id':rack.id,'u_start':14,'u_size':1}).status_code,409)
        server=ServerAsset(name='SRV1',rack_id=rack.id,u_start=20,u_size=2);self.db.add(server);self.db.commit()
        self.assertEqual(self.client.put(f'/api/devices/{second.id}/rack-location',json={'rack_id':rack.id,'u_start':21,'u_size':1}).status_code,409)
        self.assertEqual(self.client.put(f'/api/devices/{second.id}/rack-location',json={'rack_id':rack.id,'u_start':42,'u_size':2}).status_code,409)
        self.user.is_superuser=False
        self.assertEqual(self.client.put(url,json={'rack_id':None}).status_code,403)
        self.user.is_superuser=True
        self.assertEqual(self.client.put(url,json={'rack_id':None}).status_code,200)
        self.assertIsNone(self.client.get(url).json()['rack_id'])

    def test_backup_alert_ack_delete_preserves_task(self):
        task=TaskLog(task_type='backup',status='partial',failed_count=1,
            error_details=[{'device_name':'SW1','ip_address':'192.0.2.10','stage':'配置读取','error':'timeout password=secret'}])
        self.db.add(task);self.db.commit()
        url=f'/api/backups/alerts/{task.id}/0'
        self.assertEqual(self.client.delete(url).status_code,409)
        rows=self.client.get('/api/backups/alerts').json()
        self.assertEqual(rows[0]['stage'],'配置读取')
        self.assertNotIn('password=secret',rows[0]['error'])
        self.assertEqual(self.client.post(url+'/acknowledge').status_code,200)
        self.assertEqual(self.client.delete(url).status_code,200)
        self.assertEqual(self.client.get('/api/backups/alerts').json(),[])
        self.assertEqual(self.db.get(TaskLog,task.id).failed_count,1)

    def test_backup_alert_batch_requires_ack_and_preserves_tasks(self):
        task=TaskLog(task_type='backup',status='partial',failed_count=2,error_details=[
            {'device_name':'SW1','stage':'连接','error':'timeout'},
            {'device_name':'SW2','stage':'配置读取','error':'eof'},
        ])
        self.db.add(task);self.db.commit()
        items=[{'task_id':task.id,'index':0},{'task_id':task.id,'index':1}]
        self.assertEqual(self.client.post('/api/backups/alerts/batch-delete',json={'items':items}).status_code,409)
        ack=self.client.post('/api/backups/alerts/batch-acknowledge',json={'items':items})
        self.assertEqual((ack.status_code,ack.json()['count']),(200,2))
        deleted=self.client.post('/api/backups/alerts/batch-delete',json={'items':items})
        self.assertEqual((deleted.status_code,deleted.json()['count']),(200,2))
        self.assertEqual(self.client.get('/api/backups/alerts').json(),[])
        self.assertEqual(self.db.get(TaskLog,task.id).failed_count,2)

    def test_global_and_device_retention_and_baseline(self):
        self.assertEqual(self.client.put('/api/backups/retention/global',json={'keep':5}).status_code,200)
        self.assertEqual(policy(self.db,self.device.id),5)
        url=f'/api/backups/retention/devices/{self.device.id}'
        self.assertEqual(self.client.put(url,json={'keep':0}).status_code,200)
        self.assertEqual(policy(self.db,self.device.id),0)
        self.assertEqual(excess(self.db,self.device.id,0),[])
        self.client.put(url,json={'keep':None})
        self.assertEqual(policy(self.db,self.device.id),5)
        self.assertEqual(self.client.put('/api/backups/retention/global',json={'keep':-1}).status_code,422)
        for index in range(7):
            self.db.add(ConfigBackup(device_id=self.device.id,config_text='test',config_hash=str(index),backup_time=datetime(2026,1,index+1),is_baseline=index==0))
        self.db.commit()
        self.assertEqual(len(excess(self.db,self.device.id,5)),1)

    def test_backup_filters_utc_and_validation(self):
        self.db.add(ConfigBackup(device_id=self.device.id,backup_time=datetime(2026,1,2,1),config_text='test',config_hash='hash'));self.db.commit()
        url='/api/backups?company=HQ&status=online&date_from=2026-01-02T00:00:00%2B08:00&date_to=2026-01-03T00:00:00%2B08:00'
        self.assertEqual(len(self.client.get(url).json()),1)
        self.assertEqual(self.client.get('/api/backups?company=other').json(),[])
        self.assertEqual(self.client.get('/api/backups?date_from=2026-02-01&date_to=2026-01-01').status_code,422)

    def test_isp_pagination_edit_consistency_and_shrink_protection(self):
        payload={'provider':'ISP','circuit_number':'A','networks':['203.0.113.0/29'],'used_ips':[],'reserved_ips':[]}
        row=self.client.post('/api/isp',json=payload)
        self.assertEqual(row.status_code,201,row.text)
        line=row.json()['id'];url=f'/api/isp/{line}/addresses'
        page=self.client.get(url+'?limit=2').json()
        self.assertEqual([r['address'] for r in page['items']],['203.0.113.1','203.0.113.2'])
        self.assertEqual(page['next_offset'],'2')
        self.assertEqual(page['total'],'6')
        edit=url+'/203.0.113.5'
        self.assertEqual(self.client.put(edit,json={'status':'使用中','purpose':'VPN','ports':'tcp/443, UDP/500'}).status_code,200)
        self.assertEqual(self.client.get(url+'?address=203.0.113.5').json()['items'][0]['ports'],'TCP/443, UDP/500')
        self.assertEqual(self.client.put(edit,json={'ports':'TCP/99999'}).status_code,422)
        payload['networks']=['203.0.113.0/30']
        self.assertEqual(self.client.put(f'/api/isp/{line}',json=payload).status_code,409)
        payload['networks']=['203.0.113.0/29']
        self.assertEqual(self.client.put(f'/api/isp/{line}',json=payload).status_code,200)
        self.assertEqual(self.client.get(url+'?address=203.0.113.5').json()['items'][0]['status'],'可用')

    def test_ipv6_does_not_materialize_entire_range(self):
        result=self.client.post('/api/isp',json={'provider':'ISP','circuit_number':'V6','networks':['2001:db8::/64']})
        self.assertEqual(result.status_code,201,result.text)
        page=self.client.get(f"/api/isp/{result.json()['id']}/addresses?limit=2").json()
        self.assertEqual(len(page['items']),2)
        self.assertGreater(int(page['total']),10**18)
        self.assertEqual(self.db.query(isp.ISPAddress).count(),0)

    def test_module_logs_detail_and_invalid_module(self):
        self.db.add_all([AuditLog(action='put:devices',path='/api/devices/1',success=False,status_code=500),
                         DataLog(category='ssh',level='ERROR',message='SSH failed',error='password=test-secret'),
                         TaskLog(task_type='backup',status='partial',failed_count=1)])
        self.db.commit()
        data=self.client.get('/api/logs/module-summary').json()
        self.assertEqual(next(r for r in data['modules'] if r['module']=='devices')['errors'],2)
        events=self.client.get('/api/logs/module-events?module=devices').json()
        self.assertEqual(len(events),2)
        self.assertNotIn('test-secret',json.dumps(events))
        self.assertEqual(self.client.get('/api/logs/module-events?module=unknown').status_code,422)

    def test_delete_source_preserves_assets_and_can_reconfigure(self):
        from app.routers.integrations import _save_config
        from app.services.integration_sync_service import apply_inventory
        _save_config(self.db,'vcenter',{'host':'vc.test','username':'user','password':'test'})
        self.db.add(VMInstance(name='keep-me',source_type='vcenter',external_id='vm1'))
        schedule=ScheduleConfig(task_type='vcenter_sync',cron_expression='0 * * * *',is_enabled=True)
        self.db.add(schedule);self.db.commit()
        with patch('app.scheduler.tasks.scheduler',MagicMock()):
            response=self.client.delete('/api/integrations/sources/vcenter')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.db.query(VMInstance).count(),1)
        self.assertFalse(self.db.get(ScheduleConfig,schedule.id).is_enabled)
        self.assertIsNotNone(self.db.get(SystemSetting,'removed_integration_vcenter'))
        with self.assertRaises(RuntimeError):
            apply_inventory(self.db,'vcenter',{}, {'vms':[],'storage':[]})
        _save_config(self.db,'vcenter',{'host':'new.test','username':'user','password':'new'})
        self.assertIsNone(self.db.get(SystemSetting,'removed_integration_vcenter'))


if __name__=='__main__': unittest.main()
