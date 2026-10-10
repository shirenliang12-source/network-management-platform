import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_security  # isolated settings before importing the application
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import isp, auth
from app.database import get_db
from app.models import SystemSetting
from app.middleware import AuthMiddleware


class ISPTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
        isp.ISPLine.__table__.create(self.engine)
        isp.ISPGroup.__table__.create(self.engine)
        isp.ISPAddress.__table__.create(self.engine)
        SystemSetting.__table__.create(self.engine)
        self.app = FastAPI()
        self.app.include_router(isp.router)
        def session():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = session
        self.client = TestClient(self.app)
        self.payload = dict(provider='电信', circuit_number='SH-001', networks=['203.0.113.0/29'],
                            used_ips=['203.0.113.2', '203.0.113.2'], reserved_ips=['203.0.113.1'],
                            bandwidth_mbps=1000, company_group='集团', function_group='互联网出口',
                            region_group='华东', purpose='网站互联网出口',
                            location='上海机房 / 防火墙公网口', notes='网站出口')

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_crud_dedup_statistics_and_location(self):
        response = self.client.post('/api/isp', json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual((row['usable_count'],row['used_count'],row['reserved_count'],row['available_count']), ('6',1,1,'4'))
        self.assertEqual(self.client.post('/api/isp', json=self.payload).status_code,409)
        self.payload['location'] = '北京分部'
        self.payload['used_ips'] = ['203.0.113.2','203.0.113.3']
        updated = self.client.put('/api/isp/'+str(row['id']),json=self.payload)
        self.assertEqual(updated.json()['available_count'],'3')
        self.assertEqual(self.client.get('/api/isp?q=北京').json()[0]['location'],'北京分部')
        self.assertEqual(self.client.get('/api/isp?q=不存在').json(),[])
        self.assertEqual(self.client.delete('/api/isp/'+str(row['id'])).status_code,200)
        self.assertEqual(self.client.get('/api/isp').json(),[])
        self.assertEqual(self.client.put('/api/isp/999',json=self.payload).status_code,404)

    def test_groups_filters_dashboard_and_used_group_protection(self):
        catalogs={'company':['集团','子公司A'],'function':['互联网出口','云专线'],'region':['华东','华南']}
        saved=self.client.put('/api/isp/groups',json=catalogs)
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual({key:set(value) for key,value in saved.json().items()},
                         {key:set(value) for key,value in catalogs.items()})
        created=self.client.post('/api/isp',json=self.payload)
        self.assertEqual(created.status_code,201,created.text)
        row=created.json()
        self.assertEqual((row['bandwidth_mbps'],row['purpose'],row['company_group']),
                         (1000,'网站互联网出口','集团'))
        self.assertEqual(len(self.client.get('/api/isp?purpose=网站互联网出口&location=上海机房%20/%20防火墙公网口&company_group=集团&function_group=互联网出口&region_group=华东').json()),1)
        filters=self.client.get('/api/isp/filter-options').json()
        self.assertIn('网站互联网出口',filters['purpose'])
        catalogs['company']=['子公司A']
        rejected=self.client.put('/api/isp/groups',json=catalogs)
        self.assertEqual(rejected.status_code,409,rejected.text)
        settings=self.client.put('/api/isp/dashboard-settings',json={'widgets':['line_count','by_bandwidth']})
        self.assertEqual(settings.status_code,200,settings.text)
        self.assertEqual(self.client.get('/api/isp/dashboard-settings').json()['widgets'],
                         ['line_count','by_bandwidth'])

    def test_csv_import_export_dedup_and_explicit_update(self):
        content=('运营商,线路编号,带宽Mbps,网段CIDR,已使用IP,预留IP,公司分组,功能分组,区域分组,用途,使用位置,备注\n'
                 '联通,BJ-001,100Mbps,198.51.100.0/30,,,公司B,云专线,华北,云互联,北京机房,首条\n'
                 '联通,BJ-001,200Mbps,198.51.100.0/30,,,公司B,云专线,华北,重复,北京机房,重复行\n').encode('utf-8')
        first=self.client.post('/api/isp/import?mode=skip',files={'file':('lines.csv',content,'text/csv')})
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual((first.json()['created'],first.json()['duplicate_rows']),(1,1))
        exported=self.client.get('/api/isp/export')
        self.assertEqual(exported.status_code,200)
        self.assertTrue(exported.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('云互联',exported.content.decode('utf-8-sig'))
        skipped=self.client.post('/api/isp/import?mode=skip',files={'file':('lines.csv',content,'text/csv')})
        self.assertEqual(skipped.json()['skipped'],1)
        update=content.replace(b'100Mbps',b'1000Mbps').splitlines()[:2]
        updated=self.client.post('/api/isp/import?mode=update',
            files={'file':('lines.csv',b'\n'.join(update)+b'\n','text/csv')})
        self.assertEqual((updated.status_code,updated.json()['updated']),(200,1))
        self.assertEqual(self.client.get('/api/isp').json()[0]['bandwidth_mbps'],1000)
        invalid=('运营商,线路编号,带宽Mbps,网段CIDR\n'
                 '移动,GZ-001,200,192.0.2.0/30\n'
                 '移动,GZ-BAD,200,不是网段\n').encode('gb18030')
        rejected=self.client.post('/api/isp/import?mode=skip',
            files={'file':('gbk-lines.csv',invalid,'text/csv')})
        self.assertEqual(rejected.status_code,422,rejected.text)
        self.assertEqual(len(self.client.get('/api/isp').json()),1)

    def test_network_boundaries_and_invalid_usage(self):
        for changes in [
            {'networks':['203.0.113.3/29']}, {'provider':'  '},
            {'networks':['203.0.113.0/29','203.0.113.0/30']},
            {'used_ips':['203.0.113.0']}, {'used_ips':['203.0.113.7']},
            {'used_ips':['192.0.2.1']}, {'used_ips':['203.0.113.1']},
        ]:
            self.assertEqual(self.client.post('/api/isp',json=self.payload | changes).status_code,422,changes)

    def test_ipam_automatic_merge_refresh_and_permissions(self):
        from app.models import IPAMIPAddress
        IPAMIPAddress.__table__.create(self.engine)
        user = SimpleNamespace(is_superuser=False, get_modules=lambda:['isp','ipam'])
        @self.app.middleware('http')
        async def identity(request, call_next):
            request.state.user = user
            return await call_next(request)
        with Session(self.engine) as db:
            for address, status in [
                ('203.0.113.2/29','使用中'), ('203.0.113.2','使用中'),
                ('203.0.113.3','使用中'), ('203.0.113.3','预分配'),
                ('203.0.113.4','预分配'), ('203.0.113.5','规划'),
                ('203.0.113.6','已停用'), ('203.0.113.7','使用中'),
                ('192.0.2.1','使用中'), ('bad-ip','使用中'),
            ]:
                db.add(IPAMIPAddress(prefix_id=1,address=address,status=status))
            db.commit()
        result=self.client.post('/api/isp',json=self.payload)
        self.assertEqual(result.status_code,201,result.text)
        row=result.json()
        self.assertTrue(row['ipam_linked'])
        self.assertEqual((row['used_count'],row['reserved_count'],row['available_count']),(2,2,'2'))
        self.assertEqual(row['used_ips'],['203.0.113.2'])
        self.assertEqual(row['ipam_used_ips'],['203.0.113.2','203.0.113.3'])
        with Session(self.engine) as db:
            self.assertEqual(db.query(IPAMIPAddress).count(),10)
            db.query(IPAMIPAddress).filter_by(address='203.0.113.3').update({'status':'已停用'})
            db.commit()
        row=self.client.get('/api/isp').json()[0]
        self.assertEqual((row['used_count'],row['available_count']),(1,'3'))
        user.get_modules=lambda:['isp']
        row=self.client.get('/api/isp').json()[0]
        self.assertFalse(row['ipam_linked'])
        self.assertEqual(row['ipam_used_ips'],[])
        self.assertEqual((row['used_count'],row['reserved_count'],row['available_count']),(1,1,'4'))

    def test_small_and_large_networks_without_expansion(self):
        for network, count in [('192.0.2.0/31','2'),('192.0.2.2/32','1'),
                               ('2001:db8::/127','2'),('2001:db8::1/128','1'),
                               ('2001:db8::/64',str(2**64-1))]:
            result = self.client.post('/api/isp',json=dict(provider='ISP',circuit_number=network,networks=[network]))
            self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(result.json()['usable_count'],count)

    def test_module_permissions(self):
        self.app.add_middleware(AuthMiddleware)
        self.assertEqual(auth.module_for_path('/api/isp/1'),'isp')
        self.assertEqual(auth.module_for_path('/isp'),'isp')
        self.assertEqual(self.client.get('/api/isp').status_code,401)
        user = SimpleNamespace(is_active=True,is_superuser=False,get_modules=lambda:[])
        with patch('app.middleware.verify_token',return_value='test'), patch('app.middleware.get_user_by_username',return_value=user):
            self.assertEqual(self.client.get('/api/isp').status_code,403)
            user.get_modules = lambda:['isp']
            self.assertEqual(self.client.get('/api/isp').status_code,200)

    def test_migration_preserves_existing_records_and_is_repeatable(self):
        path=Path(__file__).resolve().parents[1]/'migrations/versions/20260922_0007_isp_lines.py'
        spec=importlib.util.spec_from_file_location('isp_migration',path)
        migration=importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with tempfile.TemporaryDirectory() as tmp:
            engine=create_engine('sqlite:///'+str(Path(tmp)/'legacy.db'))
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE existing_data (value TEXT)'))
                connection.execute(text("INSERT INTO existing_data VALUES ('keep')"))
                with patch.object(migration,'op',Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    migration.upgrade()
                self.assertEqual(connection.execute(text('SELECT value FROM existing_data')).scalar(),'keep')
                connection.execute(text('SELECT * FROM isp_lines'))
            engine.dispose()

    def test_dashboard_group_migration_preserves_legacy_isp_rows(self):
        root=Path(__file__).resolve().parents[1]
        base_path=root/'migrations/versions/20260922_0007_isp_lines.py'
        next_path=root/'migrations/versions/20260925_0010_isp_dashboard_groups.py'
        def load(path,name):
            spec=importlib.util.spec_from_file_location(name,path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
        base,upgrade=load(base_path,'isp_base_migration'),load(next_path,'isp_dashboard_migration')
        with tempfile.TemporaryDirectory() as tmp:
            engine=create_engine('sqlite:///'+str(Path(tmp)/'legacy-isp.db'))
            with engine.begin() as connection:
                with patch.object(base,'op',Operations(MigrationContext.configure(connection))):
                    base.upgrade()
                connection.execute(text(
                    "INSERT INTO isp_lines(identity_key,provider,circuit_number,networks_json,used_json,reserved_json,location,notes) "
                    "VALUES('key','ISP','OLD-1','[""203.0.113.0/29""]','[]','[]','旧机房 / 原用途','保留')"
                ))
                with patch.object(upgrade,'op',Operations(MigrationContext.configure(connection))):
                    upgrade.upgrade();upgrade.upgrade()
                row=connection.execute(text(
                    'SELECT location,purpose,bandwidth_mbps,company_group,function_group,region_group FROM isp_lines'
                )).one()
                self.assertEqual(tuple(row),('旧机房 / 原用途','',0,'','',''))
                self.assertIn('isp_groups',set(importlib.import_module('sqlalchemy').inspect(connection).get_table_names()))
            engine.dispose()
