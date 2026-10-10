"""Shared DHCP integration/IPAM contracts; all Windows calls are mocked."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import IPAMPrefix, IPAMIPAddress, IPAMAggregate, IPInventory, SystemSetting
from app.routers import dhcp, ipam, ip_inventory
from app.services import dhcp_integration as service, windows_dhcp


class DHCPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.db=Session(self.engine)
        self.admin=True
        app=FastAPI()
        @app.middleware('http')
        async def identity(request, call_next):
            request.state.user=SimpleNamespace(is_superuser=self.admin,get_modules=lambda:['ipam'])
            return await call_next(request)
        for router in (dhcp.router,dhcp.ipam_router,ipam.router,ip_inventory.router):app.include_router(router)
        app.dependency_overrides[get_db]=lambda:self.db
        self.client=TestClient(app)
        self.payload={'name':'HQ','mode':'DHCP','server':'dhcp.example.local','scope':'192.0.2.0','threshold':80,'interval':0}
        self.snapshot={'scope':'192.0.2.0','mask':'255.255.255.0','start':'192.0.2.10','end':'192.0.2.109','state':'Active','used':85,'free':15,'reserved':2,'percent':85}
        p=IPAMPrefix(prefix='192.0.2.0/24');self.db.add(p);self.db.commit();self.pid=p.id
        self.binding=f'/api/ipam/prefixes/{p.id}/dhcp'

    def tearDown(self):
        self.client.close();self.db.close();self.engine.dispose()

    def create(self):
        result=self.client.post('/api/integrations/dhcp',json=self.payload)
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['id']

    def test_firewall_vdom_isolation_duplicates_and_failed_sync_preservation(self):
        payload={**self.payload,'provider':'fortinet','interface':'port5','vdom':'office','api_token':'never-expose'}
        first=self.client.post('/api/integrations/dhcp',json=payload)
        self.assertEqual(first.status_code,200,first.text);sid=first.json()['id']
        self.assertNotIn('never-expose',first.text);self.assertNotIn('api_token_enc',first.text)
        self.assertEqual(self.client.post('/api/integrations/dhcp',json=payload).status_code,409)
        self.assertEqual(self.client.post('/api/integrations/dhcp',json={**payload,'vdom':'guest'}).status_code,200)
        with patch('app.services.firewall_dhcp.collect',return_value=dict(self.snapshot)):
            response=self.client.post(f'/api/integrations/dhcp/{sid}/sync')
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.put(self.binding,json={'source_id':sid}).status_code,200)
        self.assertEqual(self.client.put(f'/api/integrations/dhcp/{sid}',json={**payload,'vdom':'other'}).status_code,409)
        with patch('app.services.firewall_dhcp.collect',side_effect=ValueError('unsupported format')):
            self.assertEqual(self.client.post(f'/api/integrations/dhcp/{sid}/sync').status_code,400)
        saved=self.client.get(self.binding).json()
        self.assertEqual(saved['snapshot']['used'],85);self.assertEqual(saved['error'],'unsupported format')

    def sync(self,sid):
        with patch.object(windows_dhcp,'collect',return_value=dict(self.snapshot)):
            response=self.client.post(f'/api/integrations/dhcp/{sid}/sync',json={})
        self.assertEqual(response.status_code,200,response.text)

    def test_central_sync_binding_shared_stats_without_allocations(self):
        sid=self.create()
        self.assertEqual(self.client.put(self.binding,json={'source_id':sid}).status_code,409)
        self.sync(sid)
        result=self.client.put(self.binding,json={'source_id':sid})
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['snapshot']['warning'])
        row=self.client.get(f'/api/ipam/prefixes/{self.pid}').json()
        self.assertEqual(row['dhcp']['snapshot']['used'],85)
        self.assertEqual(row['in_use_ips'],0)
        self.assertEqual(self.db.query(IPAMIPAddress).count(),0)
        with patch.object(windows_dhcp,'collect',return_value={**self.snapshot,'used':20,'free':80,'percent':20}):
            self.assertEqual(self.client.post(self.binding+'/sync',json={}).status_code,200)
        self.assertEqual(self.client.get('/api/integrations/dhcp').json()[0]['snapshot']['used'],20)
        self.assertFalse(self.client.get(self.binding).json()['snapshot']['warning'])

    def test_binding_wrong_network_and_missing_source(self):
        sid=self.create();self.sync(sid)
        wrong=IPAMPrefix(prefix='192.0.2.0/25');self.db.add(wrong);self.db.commit()
        self.assertEqual(self.client.put(f'/api/ipam/prefixes/{wrong.id}/dhcp',json={'source_id':sid}).status_code,409)
        self.assertEqual(self.client.put(self.binding,json={'source_id':'malformed'}).status_code,404)

    def test_bound_config_and_prefix_cannot_be_deleted_or_retargeted(self):
        sid=self.create();self.sync(sid)
        self.client.put(self.binding,json={'source_id':sid})
        self.assertEqual(self.client.delete(f'/api/integrations/dhcp/{sid}').status_code,409)
        self.assertEqual(self.client.put(f'/api/integrations/dhcp/{sid}',json={**self.payload,'scope':'198.51.100.0'}).status_code,409)
        self.assertEqual(self.client.delete(f'/api/ipam/prefixes/{self.pid}').status_code,409)
        self.assertEqual(self.client.put(f'/api/ipam/prefixes/{self.pid}',json={'prefix':'192.0.2.0/25'}).status_code,409)
        self.assertEqual(self.client.put(self.binding,json={'source_id':None}).status_code,200)
        self.assertEqual(self.client.delete(f'/api/integrations/dhcp/{sid}').status_code,200)

    def test_legacy_config_is_same_record_and_kept_on_upgrade(self):
        item=IPInventory(subnet='192.0.2.0/24');self.db.add(item);self.db.commit()
        legacy={k:v for k,v in self.payload.items() if k!='name'}
        windows_dhcp.save_config(self.db,item.id,legacy)
        original=self.db.get(SystemSetting,f'dhcp_scope:{item.id}').value
        rows=self.client.get('/api/integrations/dhcp').json();self.assertEqual(len(rows),1)
        sid=rows[0]['id'];self.assertTrue(rows[0]['legacy'])
        self.assertEqual(self.db.get(SystemSetting,f'dhcp_scope:{item.id}').value,original)
        self.assertEqual(self.client.post('/api/integrations/dhcp',json=self.payload).status_code,409)
        self.sync(sid)
        self.assertEqual(self.client.put(self.binding,json={'source_id':sid}).status_code,200)
        response=self.client.put(f'/api/integrations/dhcp/{sid}',json={**self.payload,'threshold':90})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(windows_dhcp.read_config(self.db,item.id)['threshold'],90)
        self.assertFalse(self.client.get(self.binding).json()['snapshot']['warning'])
        self.assertEqual(self.client.delete(f'/api/ip-inventory/{item.id}').status_code,409)
        self.assertEqual(self.client.put(f'/api/ip-inventory/{item.id}/dhcp',json={**legacy,'scope':'198.51.100.0'}).status_code,409)

    def test_failed_sync_keeps_snapshot_and_permissions(self):
        sid=self.create();self.sync(sid);self.client.put(self.binding,json={'source_id':sid})
        with patch.object(windows_dhcp,'collect',side_effect=ValueError('unreachable')):
            self.assertEqual(self.client.post(self.binding+'/sync',json={}).status_code,400)
        data=self.client.get(self.binding).json()
        self.assertEqual(data['snapshot']['used'],85);self.assertEqual(data['error'],'unreachable')
        self.admin=False
        self.assertEqual(self.client.get(self.binding).status_code,200)
        self.assertEqual(self.client.get('/api/integrations/dhcp').status_code,403)
        self.assertEqual(self.client.post('/api/integrations/dhcp',json=self.payload).status_code,403)
        self.assertEqual(self.client.put(self.binding,json={'source_id':None}).status_code,403)
        self.assertEqual(self.client.post(self.binding+'/sync',json={}).status_code,403)

    def test_scheduler_reuses_central_source_not_one_call_per_prefix(self):
        sid=self.create();self.sync(sid)
        self.client.put(self.binding,json={'source_id':sid})
        service.save_source(self.db,{**self.payload,'interval':60},sid)
        with patch.object(windows_dhcp,'collect',return_value=dict(self.snapshot)) as collect:
            service.sync_due(self.db)
            collect.assert_not_called()
        row=self.db.get(SystemSetting,service.source_key(sid))
        import json
        config=json.loads(row.value);config.pop('last_attempt');row.value=json.dumps(config);self.db.commit()
        with patch.object(windows_dhcp,'collect',return_value=dict(self.snapshot)) as collect:
            service.sync_due(self.db);collect.assert_called_once()

    def test_subnet_change_hides_mismatched_statistics(self):
        sid=self.create();self.sync(sid);self.client.put(self.binding,json={'source_id':sid})
        with patch.object(windows_dhcp,'collect',return_value={**self.snapshot,'mask':'255.255.255.128'}):
            self.assertEqual(self.client.post(self.binding+'/sync',json={}).status_code,200)
        summary=self.client.get(self.binding).json()
        self.assertIsNone(summary['snapshot'])
        self.assertIn('不再匹配',summary['error'])

    def test_config_edit_during_sync_is_not_overwritten(self):
        sid=self.create();self.sync(sid)
        def edit_during_read(*args):
            service.save_source(self.db,{**self.payload,'threshold':95},sid)
            return dict(self.snapshot)
        with patch.object(windows_dhcp,'collect',side_effect=edit_during_read):
            response=self.client.post(f'/api/integrations/dhcp/{sid}/sync',json={})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(service.get_source(self.db,sid)['threshold'],95)


if __name__=='__main__':unittest.main()
