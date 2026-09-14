"""Regression tests for IPAM import integrity and explicit DHCP credentials."""
import json
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base,get_db
from app.models import IPAMAggregate,IPAMPrefix,IPAMIPAddress,Device,IPInventory,SystemSetting
from app.routers import ipam,dhcp,ip_inventory
from app.services import dhcp_integration,windows_dhcp,ipam_import


class FixTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine);self.db=Session(self.engine)
        app=FastAPI()
        @app.middleware('http')
        async def admin(request,next_handler):
            request.state.user=SimpleNamespace(is_superuser=True)
            return await next_handler(request)
        for router in (ipam.router,dhcp.router,dhcp.ipam_router,ip_inventory.router):app.include_router(router)
        app.dependency_overrides[get_db]=lambda:self.db
        self.client=TestClient(app)
        self.secret='not-real-password-!"中'
        self.config={'mode':'DHCP','name':'test','server':'dhcp.example.local','scope':'192.0.2.0','interval':0,'threshold':80,'auth_mode':'manual','username':'TEST\\reader','password':self.secret}
        self.snapshot={'scope':'192.0.2.0','mask':'255.255.255.0','start':'192.0.2.10','end':'192.0.2.109','used':30,'free':70,'reserved':2,'percent':30,'state':'Active'}

    def tearDown(self):
        self.client.close();self.db.close();self.engine.dispose()

    def post_csv(self,kind,text):
        return self.client.post(f'/api/ipam/{kind}/import',json={'csv':text})

    def test_large_import_and_canonical_duplicate(self):
        csv='网段,描述\n'+'\n'.join(f'10.0.{i}.7/24,'+'长描述'*30 for i in range(100))
        self.assertGreater(len(csv),4000)
        result=self.post_csv('prefixes',csv)
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['created'],100)
        self.assertEqual(self.post_csv('prefixes','网段\n10.0.0.0/24').json()['skipped'],1)
        self.assertEqual(self.db.query(IPAMPrefix).count(),100)
        self.assertEqual(self.client.get('/api/ipam/tree').json()[0]['name'],'未关联聚合 / 待检查关系')

    def test_parent_order_export_roundtrip(self):
        csv='网段,聚合,父网段,分配池\n10.1.1.1/24,10.0.0.0/8,10.1.0.0/16,是\n10.1.0.0/16,10.0.0.0/8,,否'
        result=self.post_csv('prefixes',csv);self.assertEqual(result.status_code,200,result.text)
        parent=self.db.query(IPAMPrefix).filter_by(prefix='10.1.0.0/16').one()
        child=self.db.query(IPAMPrefix).filter_by(prefix='10.1.1.0/24').one()
        self.assertEqual(child.parent_id,parent.id);self.assertEqual(child.aggregate_id,parent.aggregate_id)
        exported=self.client.get('/api/ipam/export/prefixes').content.decode('utf-8-sig')
        self.assertIn('父网段',exported)
        other=create_engine('sqlite://');Base.metadata.create_all(other)
        with Session(other) as db:
            self.assertEqual(ipam_import.run_import(db,'prefixes',exported)['created'],2)
            self.assertIsNotNone(db.query(IPAMPrefix).filter_by(prefix='10.1.1.0/24').one().parent_id)
        other.dispose()

    def test_error_rolls_back_all_rows_and_auto_created_aggregate(self):
        result=self.post_csv('prefixes','网段,聚合\n10.1.0.0/16,10.0.0.0/8\n192.0.2.0/24,10.0.0.0/8')
        self.assertEqual(result.status_code,400,result.text)
        self.assertIn('第 3 行',result.text)
        self.assertEqual(self.db.query(IPAMAggregate).count(),0);self.assertEqual(self.db.query(IPAMPrefix).count(),0)
        self.assertEqual(self.post_csv('prefixes','网段,聚合\n10.1.0.0/16,不存在').status_code,400)

    def test_ambiguous_device_does_not_bind_or_create_prefix(self):
        self.db.add_all([Device(name='same',ip_address='192.0.2.1',username='u'),Device(name='same',ip_address='192.0.2.2',username='u')]);self.db.commit()
        csv='网段,IP地址,关联设备\n192.0.2.0/24,192.0.2.10,same'
        self.assertEqual(self.post_csv('ips',csv).status_code,400)
        self.assertEqual(self.db.query(IPAMPrefix).count(),0)
        result=self.post_csv('ips',csv.replace(',same',',192.0.2.1'))
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNotNone(self.db.query(IPAMIPAddress).one().assigned_device_id)

    def test_tab_headers_versions_and_missing_fields(self):
        self.assertEqual(self.post_csv('ips','网段\tIP地址\n192.0.2.0/24\t2001:db8::1').status_code,400)
        self.assertEqual(self.post_csv('ips','描述\n没有IP').status_code,400)
        result=self.post_csv('ips','\ufeff网段\tIP地址\n2001:db8::/64\t2001:0db8::2')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.db.query(IPAMIPAddress).one().address,'2001:db8::2')

    def test_legacy_cycle_audit_is_readonly_and_tree_terminates(self):
        p=IPAMPrefix(prefix='192.0.2.0/24');q=IPAMPrefix(prefix='192.0.2.0/25');self.db.add_all([p,q]);self.db.flush()
        p.parent_id=q.id;q.parent_id=p.id;self.db.commit()
        result=self.client.get('/api/ipam/relations-check').json();self.assertGreater(result['count'],0);self.assertFalse(result['modified'])
        self.assertEqual(self.client.get('/api/ipam/tree').status_code,200)
        self.assertEqual(p.parent_id,q.id)

    def test_clear_parent_and_validate_containment(self):
        csv='网段,聚合,父网段\n10.1.0.0/16,10.0.0.0/8,\n10.1.1.0/24,10.0.0.0/8,10.1.0.0/16'
        self.assertEqual(self.post_csv('prefixes',csv).status_code,200)
        child=self.db.query(IPAMPrefix).filter_by(prefix='10.1.1.0/24').one()
        result=self.client.put(f'/api/ipam/prefixes/{child.id}',json={'parent_id':None,'aggregate_id':None})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(child.parent_id);self.assertIsNone(child.aggregate_id)
        parent=self.db.query(IPAMPrefix).filter_by(prefix='10.1.0.0/16').one()
        result=self.client.post('/api/ipam/prefixes',json={'prefix':'192.0.2.0/24','parent_id':parent.id})
        self.assertEqual(result.status_code,409,result.text)

    def test_ip_move_rejects_outside_and_existing_target_address(self):
        p=IPAMPrefix(prefix='192.0.2.0/24');q=IPAMPrefix(prefix='198.51.100.0/24');self.db.add_all([p,q]);self.db.flush()
        a=IPAMIPAddress(prefix_id=p.id,address='192.0.2.10');b=IPAMIPAddress(prefix_id=q.id,address='198.51.100.10');self.db.add_all([a,b]);self.db.commit()
        endpoint=f'/api/ipam/ips/{a.id}'
        self.assertEqual(self.client.put(endpoint,json={'prefix_id':q.id}).status_code,400)
        self.assertEqual(self.client.put(endpoint,json={'prefix_id':q.id,'address':'198.51.100.10'}).status_code,409)
        result=self.client.put(endpoint,json={'prefix_id':q.id,'address':'198.51.100.11'})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(a.prefix_id,q.id)

    def test_corrupt_password_never_falls_back_to_service_identity(self):
        result=self.client.post('/api/integrations/dhcp',json=self.config);sid=result.json()['id']
        row=self.db.get(SystemSetting,dhcp_integration.source_key(sid));saved=json.loads(row.value);saved['password_enc']='v2:broken';row.value=json.dumps(saved);self.db.commit()
        with patch.object(windows_dhcp,'collect') as call:
            result=self.client.post(f'/api/integrations/dhcp/{sid}/sync',json={});call.assert_not_called()
        self.assertEqual(result.status_code,400);self.assertIn('无法解密',result.text)

    def test_manual_credentials_encrypted_redacted_and_used(self):
        result=self.client.post('/api/integrations/dhcp',json=self.config)
        self.assertEqual(result.status_code,200,result.text);sid=result.json()['id']
        saved=dhcp_integration.get_source(self.db,sid)
        self.assertNotIn(self.secret,json.dumps(saved));self.assertTrue(saved['password_enc'].startswith('v2:'))
        self.assertNotIn('password_enc',result.json());self.assertTrue(result.json()['password_configured'])
        with patch.object(windows_dhcp,'collect',return_value=self.snapshot) as call:
            result=self.client.post(f'/api/integrations/dhcp/{sid}/sync',json={})
            call.assert_called_once_with('dhcp.example.local','192.0.2.0',username='TEST\\reader',password=self.secret)
        self.assertEqual(result.status_code,200,result.text);self.assertNotIn('password_enc',result.json())
        listing=self.client.get('/api/integrations/dhcp').text
        self.assertNotIn('password_enc',listing);self.assertNotIn(self.secret,listing)
        old=saved['password_enc']
        result=self.client.put(f'/api/integrations/dhcp/{sid}',json={**self.config,'password':'','threshold':90})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(dhcp_integration.get_source(self.db,sid)['password_enc'],old)
        self.assertEqual(self.client.put(f'/api/integrations/dhcp/{sid}',json={**self.config,'password':'','username':'TEST\\other'}).status_code,422)

    def test_legacy_ip_inventory_never_returns_ciphertext(self):
        item=IPInventory(subnet='192.0.2.0/24');self.db.add(item);self.db.commit()
        config={k:v for k,v in self.config.items() if k!='name'}
        endpoint=f'/api/ip-inventory/{item.id}/dhcp'
        self.assertEqual(self.client.put(endpoint,json=config).status_code,200)
        for path in (endpoint,'/api/ip-inventory','/api/integrations/dhcp'):
            response=self.client.get(path);self.assertNotIn('password_enc',response.text);self.assertNotIn(self.secret,response.text)
        config.pop('auth_mode');config.pop('username');config.pop('password')
        self.client.put(endpoint,json=config)
        self.assertTrue(windows_dhcp.read_config(self.db,item.id)['password_enc'])

    def test_subprocess_receives_password_only_over_stdin_and_cleans_session(self):
        response=SimpleNamespace(returncode=0,stdout=json.dumps(self.snapshot).encode(),stderr=b'')
        with patch.object(windows_dhcp.subprocess,'run',return_value=response) as run:
            windows_dhcp.collect('dhcp.example.local','192.0.2.0',username='TEST\\reader',password=self.secret)
            args=run.call_args.args[0];kwargs=run.call_args.kwargs
            script=base64.b64decode(args[-1]).decode('utf-16-le')
            self.assertNotIn(self.secret,' '.join(args));self.assertNotIn(self.secret,script)
            self.assertEqual(json.loads(kwargs['input'])['password'],self.secret)
            self.assertIn('-Credential $credential',script);self.assertIn('Remove-CimSession',script)

    def test_dhcp_permission_diagnostics_identify_stage_and_account_mode(self):
        for stage, label in [('authentication','建立远程 CIM/DCOM 会话'), ('scope','读取 DHCP 作用域'), ('statistics','读取 DHCP 地址池统计')]:
            response=SimpleNamespace(returncode=1,stdout=json.dumps({'error_stage':stage,'error_category':'permission'}).encode(),stderr=self.secret.encode())
            with patch.object(windows_dhcp.subprocess,'run',return_value=response):
                with self.assertRaises(ValueError) as error:
                    windows_dhcp.collect('dhcp','192.0.2.0',username='TEST\\reader',password=self.secret)
                self.assertIn(label,str(error.exception))
                self.assertIn('未回退到服务账号',str(error.exception))
                self.assertNotIn(self.secret,str(error.exception))
                with self.assertRaises(ValueError) as error:
                    windows_dhcp.collect('dhcp','192.0.2.0')
                self.assertIn('不是当前网页登录账号',str(error.exception))

    def test_dhcp_error_diagnostics_do_not_echo_stderr(self):
        for diagnostic,expected in [({'error_stage':'module'},'模块加载失败'),({'error_category':'permission'},'拒绝访问'),({'error_category':'logon'},'登录认证失败'),({'error_category':'rpc'},'RPC/DCOM')]:
            response=SimpleNamespace(returncode=1,stdout=json.dumps(diagnostic).encode(),stderr=self.secret.encode())
            with patch.object(windows_dhcp.subprocess,'run',return_value=response):
                with self.assertRaises(ValueError) as error:windows_dhcp.collect('dhcp','192.0.2.0')
                self.assertIn(expected,str(error.exception));self.assertNotIn(self.secret,str(error.exception))


if __name__=='__main__':unittest.main()
