"""Repeated uploads, same-file duplicates, canonical IDs and atomic failures."""
import unittest
import test_security
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import IPInventory, Device, VMInstance, SystemSetting
from app.routers import devices, vms, ip_inventory


class ImportDedupTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.db=Session(self.engine)
        app=FastAPI()
        for router in (devices.router,vms.router,ip_inventory.router):app.include_router(router)
        app.dependency_overrides[get_db]=lambda:self.db
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.db.close();self.engine.dispose()

    def upload(self,kind,csv):
        if kind=='ip_inventory':return self.client.post('/api/ip-inventory/import',json={'csv':csv})
        return self.client.post(f'/api/{kind}/import-csv',files={'file':('import.csv',csv.encode(),'text/csv')})

    def test_repeated_upload_and_duplicate_lines_all_three_modules(self):
        for kind,model,header,row in [('devices',Device,'name,ip_address','Switch,192.0.2.10'),('vms',VMInstance,'name,management_ip','VM,192.0.2.11'),('ip_inventory',IPInventory,'subnet,mask','192.0.2.0,255.255.255.0')]:
            raw=header+'\n'+row+'\n'+row
            first=self.upload(kind,raw);self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(first.json()['created'],1);self.assertEqual(first.json()['skipped'],1)
            second=self.upload(kind,raw);self.assertEqual(second.json()['created'],0)
            self.assertEqual(second.json()['skipped'],2);self.assertEqual(self.db.query(model).count(),1)

    def test_casefold_ipv6_and_secondary_ip_conflicts(self):
        for kind,field in [('devices','ip_address'),('vms','management_ip')]:
            a=self.upload(kind,f'name,{field},ip2\nMain,2001:db8::1,192.0.2.5')
            self.assertEqual(a.status_code,200,a.text)
            for row in ['MAIN,198.51.100.2','Other,2001:0db8:0:0:0:0:0:1','Extra,192.0.2.5']:
                b=self.upload(kind,f'name,{field}\n'+row)
                self.assertEqual(b.status_code,200,b.text);self.assertEqual(b.json()['skipped'],1)

    def test_network_normalization_and_isolated_companies(self):
        self.upload('ip_inventory','subnet,mask,company\n192.0.2.10,255.255.255.0,A')
        b=self.upload('ip_inventory','subnet,mask,company\n192.0.2.0/24,,a\n192.0.2.0/24,,B')
        self.assertEqual(b.json()['created'],1);self.assertEqual(b.json()['skipped'],1)

    def test_invalid_row_rolls_back_prior_rows_and_reports_no_password(self):
        for kind,model,raw in [('devices',Device,'name,ip_address,password\nValid,192.0.2.2,SECRET\nInvalid,not-an-ip,SECRET'),('vms',VMInstance,'name,management_ip\nValid,192.0.2.2\nInvalid,not-an-ip')]:
            r=self.upload(kind,raw);self.assertEqual(r.status_code,400,r.text)
            self.assertNotIn('SECRET',r.text);self.assertEqual(self.db.query(model).count(),0)

    def test_duplicate_preserves_configuration_and_manual_fields(self):
        row=IPInventory(subnet='192.0.2.0/24',remarks='keep',company='',firewall='',zone_interface_name='',vlan='',mask='')
        self.db.add(row);self.db.flush();sid=row.id
        self.db.add(SystemSetting(key=f'dhcp_scope:{sid}',value='{"keep":"unchanged"}'));self.db.commit()
        r=self.upload('ip_inventory','subnet,remarks\n192.0.2.0/24,overwrite')
        self.assertEqual(r.json()['skipped'],1);self.db.refresh(row)
        self.assertEqual(row.remarks,'keep');self.assertEqual(self.db.get(SystemSetting,f'dhcp_scope:{sid}').value,'{"keep":"unchanged"}')

    def test_duplicate_headers_and_bad_column_count_rejected(self):
        for raw in ['name,name,ip_address\na,b,192.0.2.1','name,ip_address\na,192.0.2.1,extra']:
            self.assertEqual(self.upload('devices',raw).status_code,400)

    def test_existing_duplicates_are_reported_not_deleted(self):
        self.db.add_all([VMInstance(name='same'),VMInstance(name='SAME')]);self.db.commit()
        r=self.upload('vms','name\nsame')
        self.assertEqual(r.json()['skipped'],1);self.assertEqual(len(r.json()['duplicates'][0]['existing_ids']),2)
        self.assertEqual(self.db.query(VMInstance).count(),2)

    def test_integration_does_not_duplicate_manually_registered_vm(self):
        from app.services.integration_sync_service import apply_inventory
        row=VMInstance(name='Manual',management_ip='192.0.2.8',notes='keep')
        self.db.add(row);self.db.commit()
        result=apply_inventory(self.db,'vcenter',{'host':'vc','port':443},
                               {'vms':[{'external_id':'vm-8','name':'REMOTE','management_ip':'192.0.2.8'}],'storage':[]})
        self.assertEqual(result['created'],0);self.assertEqual(result['skipped'],1)
        self.assertEqual(result['conflicts'][0]['existing_ids'],[row.id])
        self.assertEqual(self.db.query(VMInstance).count(),1);self.assertEqual(row.notes,'keep')

    def test_integration_endpoint_change_cannot_reuse_external_identity(self):
        from app.services.integration_sync_service import apply_inventory
        self.db.add(VMInstance(name='old',source_type='vcenter',external_id='vm-1',source_endpoint='old:443'))
        self.db.commit()
        with self.assertRaises(ValueError):
            apply_inventory(self.db,'vcenter',{'host':'new'}, {'vms':[{'name':'new','external_id':'vm-1'}],'storage':[]})
        self.assertEqual(self.db.query(VMInstance).one().name,'old')

    def test_device_extra_ip_links_are_created_and_reimport_preserves_them(self):
        raw='name,ip_address,ip2\nRouter,192.0.2.1,192.0.2.2'
        self.assertEqual(self.upload('devices',raw).status_code,200)
        count=self.db.query(IPInventory).count()
        self.assertGreater(count,0)
        self.assertEqual(self.upload('devices',raw).json()['skipped'],1)
        self.assertEqual(self.db.query(IPInventory).count(),count)


if __name__=='__main__':unittest.main()
