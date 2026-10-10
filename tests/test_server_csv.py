import unittest
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import DCSite, DCRack, ServerAsset, ServerIP
from app.routers.assets import router


class ServerCSVTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.db = Session(self.engine)
        site = DCSite(name='test-site'); self.db.add(site); self.db.flush()
        self.db.add(DCRack(site_id=site.id, rack_number='A01', u_height=42)); self.db.commit()
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda:self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.db.close(); self.engine.dispose()

    def upload(self, csv):
        return self.client.post('/api/assets/servers/import', json={'csv':csv})

    def test_roundtrip_and_duplicate(self):
        raw = 'name,site,rack,u_start,u_size,management_ip,additional_ips\nserver,test-site,A01,13,2,192.0.2.1,"[""192.0.2.2""]"'
        result = self.upload(raw); self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['created'], 1)
        exported = self.client.get('/api/assets/servers/export')
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(self.upload(exported.content.decode('utf-8-sig')).json()['skipped'], 1)
        self.assertEqual(self.db.query(ServerAsset).count(), 1)
        self.assertEqual(self.db.query(ServerIP).count(), 1)
        self.assertEqual(self.db.query(ServerAsset).one().u_start, 13)

    def test_failure_rolls_back(self):
        result = self.upload('name,site,rack,u_start\na,test-site,A01,1\nb,test-site,A01,1')
        self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.db.query(ServerAsset).count(), 0)

    def test_extra_ip_collision_and_same_file_duplicates(self):
        raw = 'name,site,rack,u_start,management_ip\na,test-site,A01,1,192.0.2.1\na,test-site,A01,2,192.0.2.2\nb,test-site,A01,3,192.0.2.1'
        result = self.upload(raw); self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['created'], 1)
        self.assertEqual(result.json()['skipped'], 2)

    def test_template_and_bad_ip(self):
        self.assertEqual(self.client.get('/api/assets/servers/export?template=true').status_code, 200)
        self.assertEqual(self.upload('name,site,rack,management_ip\na,test-site,A01,invalid').status_code, 400)
