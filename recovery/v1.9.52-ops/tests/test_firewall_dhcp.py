import json
import unittest
from unittest.mock import patch
import test_security
from app.services.firewall_dhcp import fortinet, paloalto
from app.services.dhcp_credentials import merge, public
from app.routers.dhcp import SourceRequest
from fastapi import HTTPException


class FirewallDHCPTests(unittest.TestCase):
    def test_http_transport_keeps_token_out_of_url_and_refuses_redirect(self):
        import httpx
        from app.services.firewall_dhcp import collect
        original=httpx.Client
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(302,headers={'location':'https://other.invalid/secret'},request=request)
        def client(**kwargs):
            self.assertFalse(kwargs['trust_env']);self.assertTrue(kwargs['verify']);self.assertFalse(kwargs['follow_redirects'])
            return original(transport=httpx.MockTransport(handler),**kwargs)
        with patch('app.services.firewall_dhcp.httpx.Client',side_effect=client):
            with self.assertRaises(ValueError):
                collect({'provider':'fortinet','server':'fw.invalid','vdom':'office','interface':'port5','scope':'192.0.2.0'},'secret-token')
        self.assertEqual(len(seen),1)
        self.assertNotIn('secret-token',str(seen[0].url));self.assertEqual(seen[0].headers['Authorization'],'Bearer secret-token')

    def test_fortinet_reads_selected_vdom_and_deduplicates_leases(self):
        config={'provider':'fortinet','vdom':'office','interface':'port5','scope':'192.0.2.0'}
        lease={'interface':'port5','type':'ipv4','status':'leased','ip':'192.0.2.10'}
        def query(path,params):
            self.assertEqual(params,{'vdom':'office'})
            return json.dumps({'status':'success','vdom':'office','version':'v7.x','results':
                [{'interface':'port5','netmask':'255.255.255.0','ip-range':[{'start-ip':'192.0.2.10','end-ip':'192.0.2.19'}]}]
                if 'cmdb' in path else [lease,lease,{**lease,'interface':'port6','ip':'10.0.0.1'}]})
        data=fortinet(config,query)
        self.assertEqual((data['used'],data['free'],data['percent']),(1,9,10))
        self.assertEqual(data['vdom'],'office')

    def test_fortinet_wrong_vdom_partial_unknown_response_fail_closed(self):
        config={'provider':'fortinet','vdom':'office','interface':'port5','scope':'192.0.2.0'}
        for value in [{'status':'success','vdom':'root','results':[]},{'status':'success','vdom':'office','limit_reached':True,'results':[]},{}]:
            with self.assertRaises(ValueError):fortinet(config,lambda *args:json.dumps(value))

    def test_pa_reads_running_config_and_crosschecks_reported_capacity(self):
        config={'provider':'paloalto','scope':'192.0.2.0','netmask':'255.255.255.0','interface':'ethernet1/2'}
        def query(path,params):
            if params.get('type')=='config':
                self.assertEqual(params['action'],'show')
                body='<dhcp><interface><entry name="ethernet1/2"><server><ip-pool><member>192.0.2.10-192.0.2.19</member></ip-pool></server></entry></interface></dhcp>'
            elif 'system' in params['cmd']:body='<system><sw-version>future-version</sw-version></system>'
            else:body='interface: "ethernet1/2"\nAllocated IPs: 2, Total number of IPs in pool: 10. 20% used'
            return '<response status="success"><result>'+body+'</result></response>'
        data=paloalto(config,query)
        self.assertEqual(data['used'],2);self.assertEqual(data['percent'],20);self.assertIsNone(data['reserved'])
        self.assertEqual(data['version'],'future-version')
        with self.assertRaises(ValueError):
            paloalto(config,lambda p,q:query(p,q).replace('pool: 10','pool: 100'))

    def test_token_encrypted_redacted_and_endpoint_change_requires_new_key(self):
        payload=SourceRequest(provider='fortinet',server='fw.local',scope='192.0.2.0',interface='port5',vdom='root',api_token='secret-token').model_dump()
        saved=merge({},payload)
        self.assertNotIn('secret-token',json.dumps(saved));self.assertNotIn('api_token_enc',public(saved))
        self.assertTrue(public(saved)['api_token_configured'])
        with self.assertRaises(HTTPException):merge(saved,{**payload,'server':'other.local','api_token':''})
        self.assertEqual(merge(saved,{**payload,'api_token':''})['api_token_enc'],saved['api_token_enc'])

    def test_provider_input_rejects_xml_vdom_injection_and_invalid_masks(self):
        for data in [{'provider':'fortinet','vdom':'root;bad','interface':'port5'}, {'provider':'paloalto','interface':'<all>','netmask':'255.255.255.0'}, {'provider':'paloalto','interface':'ethernet1/2','netmask':'bad'}]:
            with self.assertRaises(ValueError):SourceRequest(server='fw.local',scope='192.0.2.0',**data)


if __name__=='__main__':unittest.main()
