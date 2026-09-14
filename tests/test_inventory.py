"""Offline inventory workflow regressions; never touch installed app data."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_security  # establishes isolated data directory before app imports
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Device, Neighbor, DeviceIP, VMInstance, VMIP, SystemSetting, IPInventory, DCSite, DCRack, ServerAsset, IPAMPrefix, IPAMIPAddress
from app.services.asset_relations import relations
from app.routers import devices, vms, commands, ip_inventory
from app.services import command_config, discovery_service, integration_sync_service


class InventoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        event.listen(self.engine, 'connect', lambda conn, _: conn.execute('PRAGMA foreign_keys=ON'))
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        app = FastAPI()
        app.include_router(devices.router)
        app.include_router(vms.router)
        app.include_router(commands.router)
        app.include_router(ip_inventory.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_bidirectional_vm_ip_hardware_datacenter_relations(self):
        site = DCSite(name='DC'); self.db.add(site); self.db.flush()
        rack = DCRack(site_id=site.id, rack_number='R1'); self.db.add(rack); self.db.flush()
        server = ServerAsset(name='ESXi-1', rack_id=rack.id)
        prefix = IPAMPrefix(prefix='192.0.2.0/24')
        self.db.add_all([server, prefix]); self.db.flush()
        address = IPAMIPAddress(prefix_id=prefix.id, address='192.0.2.22')
        machine = VMInstance(name='VM', management_ip='2001:db8::1', host_id=server.id)
        self.db.add_all([address, machine]); self.db.flush()
        self.db.add(VMIP(vm_id=machine.id, ip_address='192.0.2.22'))
        self.db.commit()
        def targets(kind, row_id, allowed=None):
            return {(n['kind'], n['id']) for group in relations(self.db, kind, row_id, allowed)['groups'] for n in group['items']}
        self.assertTrue({('prefix', prefix.id), ('ip', address.id), ('server', server.id), ('rack', rack.id), ('site', site.id)} <= targets('vm', machine.id))
        for kind, row_id in [('prefix', prefix.id), ('ip', address.id), ('server', server.id), ('rack', rack.id), ('site', site.id)]:
            self.assertIn(('vm', machine.id), targets(kind, row_id))
        self.assertEqual(targets('vm', machine.id, {'vms'}), set())
        self.assertNotIn(('vm', machine.id), targets('rack', rack.id, {'dc', 'servers'}))
        self.assertIsNone(address.assigned_device_id)
        self.assertEqual(self.db.query(IPAMIPAddress).count(), 1)

    def test_relation_candidates_and_duplicate_ips_do_not_autobind(self):
        site = DCSite(name='DC'); self.db.add(site); self.db.flush()
        rack = DCRack(site_id=site.id, rack_number='R1'); self.db.add(rack); self.db.flush()
        self.db.add_all([ServerAsset(name='same', rack_id=rack.id), ServerAsset(name='same', rack_id=rack.id)])
        prefix = IPAMPrefix(prefix='192.0.2.0/24')
        one = VMInstance(name='one', management_ip='192.0.2.10', host_name='same')
        two = VMInstance(name='two', management_ip='192.0.2.10')
        self.db.add_all([prefix, one, two]); self.db.commit()
        result = relations(self.db, 'vm', one.id)
        self.assertEqual(len(next(g for g in result['groups'] if '候选' in g['title'])['items']), 2)
        self.assertIsNone(one.host_id)
        result = relations(self.db, 'prefix', prefix.id)
        self.assertEqual(len(result['groups'][0]['items']), 2)
        self.assertTrue(any('相同 IP' in text for text in result['notes']))

    def test_vm_host_can_be_unlinked(self):
        site = DCSite(name='DC'); self.db.add(site); self.db.flush()
        rack = DCRack(site_id=site.id, rack_number='R1'); self.db.add(rack); self.db.flush()
        server = ServerAsset(name='host', rack_id=rack.id); self.db.add(server); self.db.flush()
        machine = VMInstance(name='VM', host_id=server.id, host_name='host'); self.db.add(machine); self.db.commit()
        result = self.client.put(f'/api/vms/{machine.id}', json={'host_id': None, 'host_name': ''})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(result.json()['host_id'])

    def test_ip_inventory_clear_device_and_long_text(self):
        content = '中文备注' * 2000
        result = self.client.post('/api/ip-inventory', json={'subnet': '192.0.2.0/24', 'remarks': content})
        self.assertEqual(result.status_code, 200, result.text)
        row_id = result.json()['id']
        result = self.client.put(f'/api/ip-inventory/{row_id}', json={'device_id': None, 'sort_order': None})
        self.assertEqual(result.status_code, 200, result.text)
        rows = self.client.get('/api/ip-inventory').json()
        self.assertEqual(rows[0]['remarks'], content)
        self.assertIsNone(rows[0]['device_id'])

    def test_ip_inventory_legacy_empty_integer_fields_still_load(self):
        self.db.add(IPInventory(ip_segment='192.0.2.1-2001:db8::1', device_id='', sort_order=''))
        self.db.commit()
        result = self.client.get('/api/ip-inventory')
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()[0]['device_id'])
        self.assertEqual(result.json()[0]['sort_order'], 0)

    def test_ip_inventory_import_is_not_limited_to_4000_characters(self):
        content = '备注' * 3000
        response = self.client.post('/api/ip-inventory/import', json={'csv': 'subnet,remarks\n192.0.2.0/24,' + content})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(self.client.get('/api/ip-inventory').json()[0]['remarks'], content)

    def test_ip_inventory_range_does_not_crash_on_ipv6_device(self):
        self.db.add(Device(name='ipv6', ip_address='2001:db8::1'))
        self.db.commit()
        result = self.client.post('/api/ip-inventory', json={'ip_segment': '192.0.2.1-192.0.2.254'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get('/api/ip-inventory').status_code, 200)

    def test_batch_delete_is_explicit_atomic_and_cleans_links(self):
        first = Device(name='one', ip_address='10.0.0.1', company='总部')
        second = Device(name='two', ip_address='10.0.0.2')
        self.db.add_all([first, second]); self.db.flush()
        ids = [first.id, second.id]
        self.db.add(DeviceIP(device_id=first.id, ip_address='10.0.1.1'))
        self.db.add(Neighbor(device_id=second.id, neighbor_device_id=first.id, neighbor_name='one'))
        self.db.commit()
        self.assertEqual(self.client.post('/api/devices/batch-delete', json={'ids': []}).status_code, 422)
        self.assertEqual(self.client.post('/api/devices/batch-delete', json={'ids': [ids[0], 999]}).status_code, 409)
        self.assertEqual(self.db.query(Device).count(), 2)
        response = self.client.post('/api/devices/batch-delete', json={'ids': [ids[0], ids[0]]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['deleted'], 1)
        self.assertEqual(self.db.query(DeviceIP).count(), 0)
        self.db.expire_all()
        self.assertIsNone(self.db.query(Neighbor).one().neighbor_device_id)
        self.assertEqual(self.db.query(Device).one().id, ids[1])

    def test_vm_multi_delete_preserves_unselected(self):
        rows = [VMInstance(name=name) for name in ('a', 'b', 'c')]
        self.db.add_all(rows); self.db.flush()
        self.db.add(VMIP(vm_id=rows[0].id, ip_address='10.0.0.8'))
        self.db.commit()
        response = self.client.post('/api/vms/batch-delete', json={'ids': [rows[0].id, rows[1].id]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.db.query(VMInstance).one().name, 'c')
        self.assertEqual(self.db.query(VMIP).count(), 0)

    def test_company_catalog_and_combined_filters(self):
        row = Device(name='switch', ip_address='10.0.0.1', company='总部', model='C9300', device_type='cisco_xe')
        self.db.add(row); self.db.flush()
        self.db.add(DeviceIP(device_id=row.id, ip_address='10.0.1.1')); self.db.commit()
        response = self.client.put('/api/devices/companies', json={'names': ['总部', '分公司', '分公司']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.client.get('/api/devices/companies').json()), 2)
        self.assertEqual(self.client.put('/api/devices/companies', json={'names': ['分公司']}).status_code, 400)
        self.assertEqual(json.loads(self.db.get(SystemSetting, 'device_company_catalog').value), ['总部', '分公司'])
        self.assertEqual(len(self.client.get('/api/devices?search=10.0.1.1&company=总部&device_type=cisco_xe').json()), 1)
        self.assertEqual(len(self.client.get('/api/devices?search=C9300&device_type=cisco_ios').json()), 0)

    def test_selective_neighbors_and_endpoint_classification(self):
        source = Device(name='source', ip_address='10.0.0.1', company='总部')
        self.db.add(source); self.db.flush()
        phone = Neighbor(device_id=source.id, neighbor_ip='10.0.0.10', neighbor_name='phone', neighbor_platform='Cisco IP Phone 8841')
        vg = Neighbor(device_id=source.id, neighbor_ip='10.0.0.11', neighbor_name='vg', neighbor_platform='Cisco VG310')
        self.db.add_all([phone, vg]); self.db.commit()
        response = self.client.post(f'/api/devices/{source.id}/neighbors/add', json={'ids': [phone.id]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['added_count'], 1)
        added = self.db.query(Device).filter_by(ip_address='10.0.0.10').one()
        self.assertFalse(added.is_active)
        self.assertEqual(added.group.name, 'IPT 电话')
        self.assertEqual(added.company, '总部')
        self.assertIsNone(self.db.query(Device).filter_by(ip_address='10.0.0.11').first())
        self.assertEqual(discovery_service.classify_neighbor('Cisco VG310'), 'VG 语音网关')
        self.assertEqual(discovery_service.classify_neighbor('Cisco C9130AXI'), 'AP 无线接入点')
        self.assertEqual(discovery_service.classify_neighbor('Cisco C9300'), '交换机')
        self.assertEqual(discovery_service.classify_neighbor('unknown'), '待识别设备')
        response = self.client.post(f'/api/devices/{source.id}/neighbors/add', json={'ids': [phone.id]})
        self.assertEqual(response.json()['added_count'], 0)

    def test_manual_neighbor_chain_can_continue_on_new_device(self):
        source = Device(name='A', ip_address='192.0.2.1')
        self.db.add(source); self.db.commit()
        def discover(device, db):
            octet = int(device.ip_address.rsplit('.', 1)[1]) + 1
            db.add(Neighbor(device_id=device.id, neighbor_name=f'SW-{octet}',
                            neighbor_ip=f'192.0.2.{octet}', neighbor_platform='Cisco C9300',
                            protocol='cdp'))
            db.commit()
            return {'success': True}
        current = source.id
        with patch.object(discovery_service, 'discover_device_neighbors', side_effect=discover):
            for expected_count in (2, 3):
                response = self.client.post(f'/api/devices/{current}/neighbors/discover')
                self.assertTrue(response.json()['success'])
                # Discovery alone must not add any managed devices.
                self.assertEqual(self.db.query(Device).count(), expected_count - 1)
                neighbor = self.client.get(f'/api/devices/{current}/neighbors').json()[0]
                self.assertIsNone(neighbor['managed_device_id'])
                result = self.client.post(f'/api/devices/{current}/neighbors/add', json={'ids': [neighbor['id']]})
                self.assertEqual(result.json()['added_count'], 1)
                target = self.client.get(f'/api/devices/{current}/neighbors').json()[0]
                self.assertTrue(target['managed'])
                current = target['managed_device_id']
                self.assertEqual(self.client.get(f'/api/devices/{current}').status_code, 200)
                self.assertEqual(self.db.query(Device).count(), expected_count)

    def test_neighbor_target_resolves_additional_ip(self):
        source = Device(name='source', ip_address='192.0.2.1')
        target = Device(name='target', ip_address='192.0.2.2')
        self.db.add_all([source, target]); self.db.flush()
        self.db.add(DeviceIP(device_id=target.id, ip_address='192.0.2.22'))
        self.db.add(Neighbor(device_id=source.id, neighbor_ip='192.0.2.22', neighbor_name='target'))
        self.db.commit()
        row = self.client.get(f'/api/devices/{source.id}/neighbors').json()[0]
        self.assertEqual(row['managed_device_id'], target.id)
        self.assertTrue(row['managed'])

    def test_custom_type_inherits_driver_and_deletion_is_guarded(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(command_config, 'COMMANDS_FILE', Path(directory) / 'commands.json'):
            created = self.client.post('/api/commands/types', json={'label': 'My NX switch', 'base_on': 'cisco_nxos'})
            self.assertEqual(created.status_code, 200, created.text)
            key = created.json()['key']
            self.assertEqual(command_config.resolve_device_driver(key), 'cisco_nxos')
            self.client.put(f'/api/commands/types/{key}/label', json={'label': '交换机'})
            self.assertEqual(command_config.resolve_device_driver(key), 'cisco_nxos')
            self.assertEqual(self.client.put(f'/api/commands/types/{key}/driver', json={'driver': 'not_a_driver'}).status_code, 400)
            row = Device(name='used', ip_address='10.0.0.22', device_type=key)
            self.db.add(row); self.db.commit()
            self.assertEqual(self.client.delete(f'/api/commands/types/{key}').status_code, 400)
            row.device_type = 'cisco_ios'; self.db.commit()
            self.assertEqual(self.client.delete(f'/api/commands/types/{key}').status_code, 200)

    def test_excluded_vms_are_not_updated_or_marked_missing(self):
        vm = VMInstance(name='local-off', source_type='vcenter', external_id='off', status='运行中')
        self.db.add(vm); self.db.commit()
        inventory = {'vms': [{'external_id': 'off', 'name': 'remote-off', 'status': '已关机'},
                             {'external_id': 'template', 'name': 'template', 'status': '模板'},
                             {'external_id': 'on', 'name': 'on', 'status': '运行中'}], 'storage': []}
        preview = integration_sync_service.preview_inventory(self.db, 'vcenter', copy.deepcopy(inventory), detect_missing=True)
        self.assertEqual(preview['excluded_vm_count'], 2)
        self.assertEqual([v['external_id'] for v in preview['vms']], ['on'])
        self.assertEqual(preview['missing_vms'], [])
        result = integration_sync_service.apply_inventory(self.db, 'vcenter', {'host': 'test'}, inventory,
                    selected_ids=None, update_existing=True, mark_missing=True, mode='manual')
        self.assertEqual(result['created'], 1)
        self.db.refresh(vm)
        self.assertEqual(vm.name, 'local-off')
        self.assertNotEqual(vm.sync_state, 'stale')
