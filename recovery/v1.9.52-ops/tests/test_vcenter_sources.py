import unittest
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import VMInstance
from app.routers.integrations import router
from app.services.integration_sync_service import load_stored_config, preview_inventory
from app.services.integration_sync_service import filter_sync_inventory
from app.services.integration_service import _vmware_status


class VcenterSourceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.db = Session(self.engine)
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda:self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.db.close(); self.engine.dispose()

    def add(self, host):
        response = self.client.post('/api/integrations/vcenter-sources', json={'host':host, 'username':'reader', 'password':'test-password'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_config_isolation_and_credential_redaction(self):
        first = self.add('one.example'); second = self.add('two.example')
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 20)
        response = self.client.get('/api/integrations/vcenter-sources')
        self.assertEqual(len(response.json()), 2)
        self.assertNotIn('test-password', response.text)
        self.assertEqual(load_stored_config(self.db, first)['host'], 'one.example')
        self.assertEqual(load_stored_config(self.db, second)['password'], 'test-password')
        duplicate = self.client.post('/api/integrations/vcenter-sources', json={'host':'ONE.example', 'username':'reader', 'password':'test-password'})
        self.assertEqual(duplicate.status_code, 409)
        changed = self.client.put(f'/api/integrations/vcenter-sources/{first}', json={'host':'other.example', 'username':'reader'})
        self.assertEqual(changed.status_code, 409)

    def test_same_external_id_does_not_cross_sources(self):
        first = self.add('one.example'); second = self.add('two.example')
        one = VMInstance(name='one', source_type=first, external_id='vm-1')
        two = VMInstance(name='two', source_type=second, external_id='vm-1')
        self.db.add_all([one,two]); self.db.commit()
        inventory = {'vms':[{'external_id':'vm-1','name':'one','status':'running'}], 'storage':[]}
        preview_inventory(self.db, first, inventory, detect_missing=True)
        self.assertEqual(inventory['vms'][0]['local_id'], one.id)
        self.assertFalse(any(row['local_id'] == two.id for row in inventory.get('missing_vms', [])))

    def test_all_vm_power_states_are_retained_and_counted(self):
        self.assertEqual(_vmware_status(False, 'poweredOn'), '运行中')
        self.assertEqual(_vmware_status(False, 'VirtualMachinePowerState.poweredOff'), '已关机')
        self.assertEqual(_vmware_status(False, 'suspended'), '挂起')
        self.assertEqual(_vmware_status(True, 'poweredOff'), '模板')
        self.assertEqual(_vmware_status(False, 'unknown'), '其他')
        inventory={'vms':[
            {'status':'运行中'},{'status':'已关机'},{'status':'挂起'},
            {'status':'模板'},{'status':'unknown'},{'error':'permission denied'},
        ],'storage':[]}
        filter_sync_inventory(inventory)
        self.assertEqual(inventory['summary']['vms'],6)
        self.assertEqual(inventory['summary']['states'],
                         {'运行中':1,'已关机':1,'挂起':1,'模板':1,'其他':1})
        self.assertEqual(inventory['excluded_vm_count'],0)
