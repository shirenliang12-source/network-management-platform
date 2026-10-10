import unittest
from unittest.mock import patch, MagicMock
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import Device, ConfigBackup
from app.routers.backups import router
from app.services.backup_service import backup_device_config


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.device = Device(name='preview', ip_address='192.0.2.1', status='online')
        self.db.add(self.device); self.db.commit()
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.db.close(); self.engine.dispose()

    def test_large_configuration_pages(self):
        text = '中<script>\n' * 150000
        row = ConfigBackup(device_id=self.device.id, config_text=text, config_hash='test-hash')
        self.db.add(row); self.db.commit()
        for offset in (0, 32768, len(text), len(text) + 100):
            response = self.client.get(f'/api/backups/{row.id}/content?offset={offset}&limit=32768')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['text'], text[offset:offset + 32768])
            self.assertEqual(response.json()['total'], len(text))
        self.assertEqual(self.client.get(f'/api/backups/{row.id}/content?limit=1000000').status_code, 422)
        self.assertEqual(self.client.get('/api/backups/999/content').status_code, 404)

    def test_failed_backup_does_not_mark_offline(self):
        ssh = MagicMock(); ssh.connect.return_value = False; ssh.last_error = 'Authentication failed'
        with patch('app.services.backup_service.SSHService', return_value=ssh):
            self.assertFalse(backup_device_config(self.device, self.db)['success'])
        self.db.refresh(self.device)
        self.assertEqual(self.device.status, 'online')
