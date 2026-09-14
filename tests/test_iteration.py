"""Offline regressions for allocations, DHCP and retention. No network access."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from types import SimpleNamespace
import test_security
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import IPAMPrefix, IPAMIPAddress, VMInstance, VMIP, Device, ConfigBackup, SystemSetting, IPInventory
from app.services.ip_allocation import allocate, guard_vm_delete
from app.services.asset_relations import relations
from app.services import windows_dhcp, command_config
from app.services.backup_service import prune_old_backups
from app.services.firewall_parser import parse_firewall_version
from app.routers import ipam, ip_inventory, backups


class IterationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
        event.listen(self.engine,'connect',lambda conn,_:conn.execute('PRAGMA foreign_keys=ON'))
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine)
        app=FastAPI()
        self.superuser=True
        @app.middleware('http')
        async def user(request, call_next):
            request.state.user=SimpleNamespace(is_superuser=self.superuser,get_modules=lambda:['ipam'])
            return await call_next(request)
        for router in (ipam.router, ip_inventory.router, backups.router): app.include_router(router)
        app.dependency_overrides[get_db]=lambda:self.db
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close(); self.db.close(); self.engine.dispose()

    def fixture(self):
        prefix=IPAMPrefix(prefix='192.0.2.0/24')
        machine=VMInstance(name='VM1',management_ip='192.0.2.12')
        self.db.add_all([prefix,machine]);self.db.commit()
        return prefix.id,machine.id

    def test_claim_release_stats_and_guards(self):
        pid,vid=self.fixture()
        payload={'prefix_id':pid,'vm_id':vid,'address':'192.0.2.12'}
        res=self.client.post('/api/ipam/vm-allocation',json=payload)
        self.assertEqual(res.status_code,200,res.text)
        iid=res.json()['id']
        self.assertEqual(self.client.post('/api/ipam/vm-allocation',json=payload).status_code,200)
        row=self.db.get(IPAMIPAddress,iid)
        self.assertEqual((row.assigned_vm_id,row.status),(vid,'使用中'))
        stats=self.client.get(f'/api/ipam/prefixes/{pid}').json()
        self.assertEqual(stats['static_used'],1)
        self.assertEqual(stats['in_use_ips'],1)
        self.assertGreater(stats['utilization'],0)
        self.assertEqual(self.client.delete(f'/api/ipam/ips/{iid}').status_code,409)
        self.assertEqual(self.client.put(f'/api/ipam/ips/{iid}',json={'allocation_type':'DHCP'}).status_code,409)
        self.assertEqual(self.client.delete(f'/api/ipam/prefixes/{pid}').status_code,409)
        with self.assertRaises(HTTPException): guard_vm_delete(self.db,[vid])
        # Release remains available after VM IP changes.
        self.db.get(VMInstance,vid).management_ip='192.0.2.13'; self.db.commit()
        self.assertTrue(any(a.get('release') for a in relations(self.db,'vm',vid)['allocations']))
        res=self.client.post('/api/ipam/vm-allocation',json={**payload,'release':True})
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(row.status,'规划')
        self.assertIsNone(row.assigned_vm_id)

    def test_claim_conflicts_permissions_and_stale_ip(self):
        pid,vid=self.fixture()
        row=IPAMIPAddress(prefix_id=pid,address='192.0.2.12',allocation_type='DHCP')
        self.db.add(row);self.db.commit()
        data={'prefix_id':pid,'vm_id':vid,'address':'192.0.2.12'}
        self.assertEqual(self.client.post('/api/ipam/vm-allocation',json=data).status_code,409)
        row.allocation_type='静态';self.db.commit()
        dup=VMInstance(name='VM2',management_ip='192.0.2.12');self.db.add(dup);self.db.commit()
        self.assertEqual(self.client.post('/api/ipam/vm-allocation',json=data).status_code,409)
        self.db.delete(dup);self.db.commit()
        self.assertEqual(self.client.post('/api/ipam/vm-allocation',json={**data,'address':'192.0.2.14'}).status_code,409)
        self.superuser=False
        self.assertEqual(self.client.post('/api/ipam/vm-allocation',json=data).status_code,403)
        self.assertIsNone(row.assigned_vm_id)

    def test_extra_ipv6_claim(self):
        p=IPAMPrefix(prefix='2001:db8::/64');v=VMInstance(name='v');self.db.add_all([p,v]);self.db.flush()
        self.db.add(VMIP(vm_id=v.id,ip_address='2001:0db8::2/64'));self.db.commit()
        res=allocate(self.db,v.id,p.id,'2001:db8::2')
        self.assertEqual(self.db.get(IPAMIPAddress,res['id']).address,'2001:db8::2')

    def test_per_device_retention_preserves_baseline_and_other_devices(self):
        d=Device(name='D1',ip_address='192.0.2.1',username='u');d2=Device(name='D2',ip_address='192.0.2.2',username='u')
        self.db.add_all([d,d2]);self.db.flush()
        for i in range(9):
            self.db.add(ConfigBackup(device_id=d.id,config_text='x',config_hash=str(i),backup_time=datetime.utcnow()+timedelta(seconds=i),is_baseline=i==0))
        self.db.add(ConfigBackup(device_id=d2.id,config_text='keep',config_hash='other'))
        self.db.commit()
        res=self.client.put(f'/api/backups/retention/devices/{d.id}',json={'keep':5})
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(self.db.query(ConfigBackup).count(),10)
        prune_old_backups(self.db,d.id)
        self.assertEqual(self.db.query(ConfigBackup).filter_by(device_id=d.id).count(),6)
        self.assertEqual(self.db.query(ConfigBackup).filter_by(device_id=d2.id).count(),1)
        self.assertEqual(self.client.put(f'/api/backups/retention/devices/{d.id}',json={'keep':-1}).status_code,422)
        self.client.put(f'/api/backups/retention/devices/{d.id}',json={'keep':0})
        prune_old_backups(self.db,d.id)
        self.assertEqual(self.db.query(ConfigBackup).count(),7)

    def test_dhcp_success_warning_and_failure_keeps_snapshot(self):
        item=IPInventory(subnet='192.0.2.0/24');self.db.add(item);self.db.commit()
        endpoint=f'/api/ip-inventory/{item.id}/dhcp'
        data={'mode':'DHCP','server':'dhcp.example.local','scope':'192.0.2.0','threshold':80,'interval':0}
        self.assertEqual(self.client.put(endpoint,json=data).status_code,200)
        sample={'scope':'192.0.2.0','mask':'255.255.255.0','start':'192.0.2.10','end':'192.0.2.109','state':'Active','used':85,'free':15,'reserved':2,'percent':85}
        with patch.object(windows_dhcp,'collect',return_value=sample):
            r=self.client.post(endpoint+'/sync',json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['snapshot']['warning'])
        self.assertEqual(self.client.get('/api/ip-inventory').json()[0]['dhcp']['snapshot']['used'],85)
        with patch.object(windows_dhcp,'collect',side_effect=ValueError('unreachable')):
            self.assertEqual(self.client.post(endpoint+'/sync',json={}).status_code,400)
        current=self.client.get(endpoint).json()
        self.assertEqual(current['snapshot']['used'],85)
        self.assertEqual(current['error'],'unreachable')
        self.assertEqual(self.client.put(endpoint,json={**data,'server':"host'; bad"}).status_code,422)
        self.superuser=False
        self.assertEqual(self.client.put(endpoint,json=data).status_code,403)

    def test_dhcp_wrong_subnet_does_not_overwrite(self):
        item=IPInventory(subnet='198.51.100.0/24');self.db.add(item);self.db.commit()
        windows_dhcp.save_config(self.db,item.id,{'mode':'DHCP','server':'dhcp','scope':'192.0.2.0','threshold':80,'interval':0})
        with patch.object(windows_dhcp,'collect',return_value={'scope':'192.0.2.0','mask':'255.255.255.0'}):
            with self.assertRaises(ValueError):windows_dhcp.sync(self.db,item.id)
        self.assertNotIn('snapshot',windows_dhcp.read_config(self.db,item.id))

    def test_firewall_commands_drivers_and_version(self):
        for driver in ('fortinet','cisco_asa','paloalto_panos'):
            self.assertEqual(command_config.resolve_device_driver(driver),driver)
            self.assertNotEqual(command_config.get_command(driver,'show_version'),'show_version')
            self.assertEqual(command_config.get_command_with_fallbacks(driver,'cdp_neighbors'),[])
        self.assertEqual(command_config.get_command('fortinet','show_version'),'get system status')
        self.assertEqual(parse_firewall_version('hostname: PA\nmodel: PA-440\nsw-version: 11.1.0\nserial: 1234','paloalto_panos')['serial_number'],'1234')
        self.assertEqual(parse_firewall_version('Version: FortiGate-60F v7.4.1,build123\nHostname: FG\nSerial-Number: FG123','fortinet')['model'],'FortiGate-60F')


if __name__=='__main__':unittest.main()
