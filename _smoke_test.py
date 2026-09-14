"""Frozen-exe smoke test: launch the built exe, probe Web login + multi-user gate
+ confirm the user-management modal fix is bundled (modal-overlay, not modal-backdrop)."""
import os
import sys
import subprocess
import time
import shutil
import urllib.request
import urllib.error
import urllib.parse
import json
import re
import sqlite3
import textwrap
import tempfile
import socket

try:
    import sitecustomize
except ImportError:
    sitecustomize = None

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_EXE = os.path.join(ROOT, "dist_onefile", "CiscoNetworkManager.exe")
SMOKE_ROOT = os.path.join(ROOT, "_smoke")
os.makedirs(SMOKE_ROOT, exist_ok=True)
SMOKE = tempfile.mkdtemp(prefix="run-", dir=SMOKE_ROOT)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    PORT = probe.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
with open(os.path.join(ROOT, "app", "config.py"), encoding="utf-8") as _config_file:
    _version_match = re.search(
        r'APP_VERSION:\s*str\s*=\s*"([\d.]+)"', _config_file.read()
    )
EXPECTED_VERSION = _version_match.group(1) if _version_match else ""


def _force_rmtree(p):
    target = os.path.realpath(p)
    allowed = os.path.realpath(SMOKE_ROOT)
    if os.path.commonpath([target, allowed]) != allowed or target == allowed:
        raise ValueError("Refusing to remove a path outside this isolated smoke run")
    if os.path.isdir(p):
        shutil.rmtree(p)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def clean_cookie(cookie):
    """Normalise a Set-Cookie value into a clean 'wb_session=<token>' header."""
    if not cookie:
        return cookie
    val = cookie.split(";")[0]
    if "=" in val:
        val = val.split("=", 1)[1]
    return "wb_session=" + val.strip().strip('"')


def req(method, path, data=None, cookie=None):
    url = BASE + path
    hdr = {"Content-Type": "application/json", "Connection": "close"}
    if cookie:
        hdr["Cookie"] = clean_cookie(cookie)
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=5)
        return resp.status, resp.read().decode(), resp.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("Set-Cookie")


def log(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    return 1 if ok else 0


def main():
    for attempt in range(15):
        try:
            _force_rmtree(SMOKE)
            break
        except (PermissionError, OSError):
            if attempt == 14:
                raise
            time.sleep(1)
    if not os.path.exists(SRC_EXE):
        print("ERROR: frozen exe not found"); sys.exit(1)
    os.makedirs(SMOKE, exist_ok=True)
    dst = os.path.join(SMOKE, "CiscoNetworkManager.exe")
    if os.path.exists(dst):
        os.remove(dst)
    shutil.copy(SRC_EXE, dst)

    opener = urllib.request.build_opener(NoRedirect)
    urllib.request.install_opener(opener)

    data_dir = os.path.join(SMOKE, "data")
    # Seed a v1.9.36-era database. The packaged executable must migrate it in
    # place and retain the marker, proving a real old-version upgrade path.
    seed_env = os.environ.copy()
    seed_env["CISCO_NM_DATA_DIR"] = data_dir
    seed_env.pop("NETMGR_SECRET_KEY", None)
    seed_script = textwrap.dedent("""
        import sqlite3
        from pathlib import Path
        from alembic import command
        from alembic.config import Config
        from app.config import DATA_DIR
        cfg = Config('alembic.ini')
        command.upgrade(cfg, '20260902_0002')
        connection = sqlite3.connect(Path(DATA_DIR) / 'netmgr.db')
        connection.execute("INSERT INTO system_settings (key,value) VALUES ('smoke_upgrade_marker','preserved-from-v1.9.36')")
        connection.commit()
        connection.close()
    """)
    subprocess.run(
        [sys.executable, "-c", seed_script], cwd=ROOT, env=seed_env,
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc = subprocess.Popen([dst, "--port", str(PORT), "--data-dir", data_dir],
                            cwd=SMOKE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            try:
                urllib.request.urlopen(f"{BASE}/health", timeout=2)
                up = True
                break
            except Exception:
                time.sleep(0.5)
        if not up:
            print("ERROR: exe did not start"); sys.exit(1)

        passed = failed = 0
        st, body, _ = req("GET", "/health")
        ok = st == 200 and EXPECTED_VERSION in body
        passed += log(f"health = {EXPECTED_VERSION}", ok, f"code={st}")
        failed += (0 if ok else 1)

        connection = sqlite3.connect(os.path.join(data_dir, "netmgr.db"))
        try:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            marker = connection.execute(
                "SELECT value FROM system_settings WHERE key='smoke_upgrade_marker'"
            ).fetchone()[0]
        finally:
            connection.close()
        status_path = os.path.join(data_dir, "backups", "upgrade_status.json")
        with open(status_path, encoding="utf-8") as handle:
            upgrade_status = json.load(handle)
        ok = (revision == "20260910_0006" and marker == "preserved-from-v1.9.36"
              and upgrade_status.get("status") == "success")
        passed += log("legacy v1.9.36 data upgraded and preserved", ok, f"revision={revision}")
        failed += (0 if ok else 1)

        try:
            urllib.request.urlopen(f"{BASE}/", timeout=5)
            passed += log("GET / unauth -> 307", False, "got 200 (no redirect)")
            failed += 1
        except urllib.error.HTTPError as e:
            ok = e.code == 307 and "/login" in e.headers.get("Location", "")
            passed += log("GET / unauth -> 307 /login", ok, f"code={e.code}")
            failed += (0 if ok else 1)

        st, _, _ = req("GET", "/api/devices")
        passed += log("GET /api/devices unauth -> 401", st == 401, f"code={st}")
        failed += (0 if st == 401 else 1)

        password_file = os.path.join(data_dir, "initial_admin_password.txt")
        with open(password_file, encoding="utf-8") as handle:
            initial_password = handle.read().strip()
        st, body, ck = req(
            "POST", "/api/auth/login",
            {"username": "admin", "password": initial_password},
        )
        try:
            j = json.loads(body)
        except Exception:
            j = {}
        ok = st == 200 and j.get("ok") is True and ck
        passed += log("login generated admin password -> 200 + cookie", ok, f"code={st} cookie={bool(ck)}")
        failed += (0 if ok else 1)
        admin_cookie = ck.split(";")[0] if ck else None

        st, catalog_body, _ = req("GET", "/api/devices/catalog/types", cookie=admin_cookie)
        ok = st == 200 and bool(json.loads(catalog_body).get("device_types"))
        passed += log("device type catalog endpoint", ok)
        failed += int(not ok)
        st, driver_body, _ = req("GET", "/api/commands/types/drivers", cookie=admin_cookie)
        ok = st == 200 and 'cisco_ios' in json.loads(driver_body).get('drivers', [])
        passed += log("frozen Netmiko driver catalog", ok)
        failed += int(not ok)
        st, js_body, _ = req("GET", "/static/js/device_inventory.js", cookie=admin_cookie)
        ok = st == 200 and 'showDeviceNeighbors' in js_body and 'deleteSelectedDevices' in js_body
        passed += log("inventory workflows bundled", ok)
        failed += int(not ok)
        ok = st == 200 and 'class="modal-overlay active"' in js_body and '进入设备继续发现' in js_body
        passed += log("visible inventory modals and continuation links bundled", ok)
        failed += int(not ok)
        create_st, created_body, _ = req("POST", "/api/devices", {
            "name": "smoke-detail-only", "ip_address": "192.0.2.123",
            "device_type": "cisco_ios", "is_active": False,
        }, cookie=admin_cookie)
        if create_st == 200:
            detail_id = json.loads(created_body)['id']
            page_st, detail_body, _ = req("GET", f"/devices/{detail_id}", cookie=admin_cookie)
            ok = page_st == 200 and 'addDetailNeighbor' in detail_body and '进入设备继续发现' in detail_body
        else:
            ok = False
        passed += log("single-device manual neighbor workflow bundled", ok)
        failed += int(not ok)
        ip_st, ip_body, _ = req("POST", "/api/ip-inventory", {
            "ip_segment": "192.0.2.1-192.0.2.254", "remarks": "中文备注" * 2000,
        }, cookie=admin_cookie)
        ip_ok = ip_st == 200
        if ip_ok:
            ip_id = json.loads(ip_body)['id']
            edit_st, _, _ = req("PUT", f"/api/ip-inventory/{ip_id}", {
                "device_id": None, "sort_order": None,
            }, cookie=admin_cookie)
            list_st, list_body, _ = req("GET", "/api/ip-inventory", cookie=admin_cookie)
            ip_ok = edit_st == 200 and list_st == 200 and any(
                row['id'] == ip_id and row['remarks'] == "中文备注" * 2000 and row['device_id'] is None
                for row in json.loads(list_body)
            )
        passed += log("IP inventory long text create/edit/reload", ip_ok)
        failed += int(not ip_ok)
        vm_st, vm_body, _ = req("POST", "/api/vms", {
            "name": "smoke-relation-vm", "management_ip": "192.0.2.20",
            "additional_ips": ["2001:db8::20"],
        }, cookie=admin_cookie)
        relation_ok = vm_st == 200
        if relation_ok:
            vm_id = json.loads(vm_body)['id']
            rel_st, rel_body, _ = req("GET", f"/api/vms/{vm_id}/relations", cookie=admin_cookie)
            js_st, rel_js, _ = req("GET", "/static/js/asset_relations.js", cookie=admin_cookie)
            relation_ok = rel_st == 200 and json.loads(rel_body)['root']['id'] == vm_id and js_st == 200 and 'modal-overlay active' in rel_js
        passed += log("cross-module relations service and visible modal bundled", relation_ok)
        failed += int(not relation_ok)
        p_st, p_body, _ = req('POST', '/api/ipam/prefixes', {'prefix':'192.0.2.0/24'}, cookie=admin_cookie)
        claim_ok = p_st == 200 and vm_st == 200
        if claim_ok:
            prefix_id = json.loads(p_body)['id']
            allocation = {'prefix_id':prefix_id, 'vm_id':vm_id, 'address':'192.0.2.20'}
            c_st, c_body, _ = req('POST', '/api/ipam/vm-allocation', allocation, cookie=admin_cookie)
            if c_st == 200:
                claimed_id = json.loads(c_body)['id']
                read_st, read_body, _ = req('GET', f'/api/ipam/ips/{claimed_id}', cookie=admin_cookie)
                delete_st, _, _ = req('DELETE', f'/api/ipam/ips/{claimed_id}', cookie=admin_cookie)
                release_st, _, _ = req('POST', '/api/ipam/vm-allocation', {**allocation,'release':True}, cookie=admin_cookie)
                claim_ok = read_st == 200 and json.loads(read_body)['assigned_vm_id'] == vm_id and delete_st == 409 and release_st == 200
            else:
                claim_ok = False
        passed += log('manual VM occupation persisted, protected and released', claim_ok)
        failed += int(not claim_ok)
        dhcp_st, dhcp_body, _ = req('PUT', f'/api/ip-inventory/{ip_id}/dhcp', {'mode':'DHCP','server':'dhcp.example.invalid','scope':'192.0.2.0','threshold':80,'interval':0}, cookie=admin_cookie)
        dhcp_get, _, _ = req('GET', f'/api/ip-inventory/{ip_id}/dhcp', cookie=admin_cookie)
        dhcp_ok = dhcp_st == 200 and dhcp_get == 200 and json.loads(dhcp_body)['interval'] == 0
        passed += log('DHCP config persisted (automatic sync disabled; no network calls)', dhcp_ok)
        failed += int(not dhcp_ok)
        central_st, central_body, _ = req('GET', '/api/integrations/dhcp', cookie=admin_cookie)
        integration_page_st, integration_page, _ = req('GET', '/integrations', cookie=admin_cookie)
        planning_st, planning_page, _ = req('GET', '/ipam', cookie=admin_cookie)
        shared_ok = central_st == 200 and any(r['id']==f'inventory-{ip_id}' and r['legacy'] for r in json.loads(central_body))
        shared_ok = shared_ok and integration_page_st == 200 and 'dhcp-integration-list' in integration_page and planning_st == 200 and 'showPrefixDhcp' in planning_page
        passed += log('DHCP integration UI, IPAM UI and legacy shared source bundled', shared_ok)
        failed += int(not shared_ok)
        pool_st, pool_body, _ = req('POST', '/api/integrations/dhcp', {'mode':'DHCP','name':'smoke pool','server':'dhcp2.example.invalid','scope':'192.0.2.0','threshold':85,'interval':0}, cookie=admin_cookie)
        pool_ok = pool_st == 200
        if pool_ok:
            source_id=json.loads(pool_body)['id']
            bind_st, _, _ = req('PUT', f'/api/ipam/prefixes/{prefix_id}/dhcp', {'source_id':source_id}, cookie=admin_cookie)
            pool_ok = bind_st == 409  # unsynchronized sources cannot be bound
        passed += log('DHCP central config persists and unsynchronized IPAM binding is rejected', pool_ok)
        failed += int(not pool_ok)
        credential_st, credential_body, _ = req('PUT', f'/api/integrations/dhcp/{source_id}', {'mode':'DHCP','name':'smoke pool','server':'dhcp2.example.invalid','scope':'192.0.2.0','threshold':85,'interval':0,'auth_mode':'manual','username':'SMOKE\\reader','password':'offline-test-only-password'}, cookie=admin_cookie)
        credentials_ok = credential_st == 200 and json.loads(credential_body).get('password_configured') and 'password_enc' not in credential_body and 'offline-test-only-password' not in credential_body
        passed += log('DHCP manual credentials saved encrypted and redacted (no network calls)', credentials_ok)
        failed += int(not credentials_ok)
        csv_body = '网段,描述\n' + '\n'.join(f'198.19.{i}.1/24,'+'smoke-import-'*8 for i in range(60))
        import_st, import_body, _ = req('POST', '/api/ipam/prefixes/import', {'csv':csv_body}, cookie=admin_cookie)
        check_st, check_body, _ = req('GET', '/api/ipam/relations-check', cookie=admin_cookie)
        import_ok = import_st == 200 and json.loads(import_body).get('created') == 60 and check_st == 200 and json.loads(check_body).get('modified') is False
        passed += log('IPAM large CSV import and read-only relationship audit bundled', import_ok)
        failed += int(not import_ok)
        retention_st, _, _ = req('PUT', f'/api/backups/retention/devices/{detail_id}', {'keep':5}, cookie=admin_cookie)
        retention_get, retention_body, _ = req('GET', '/api/backups/retention/devices', cookie=admin_cookie)
        retention_ok = retention_st == 200 and retention_get == 200 and any(r['id']==detail_id and r['keep']==5 for r in json.loads(retention_body))
        passed += log('per-device backup retention setting persisted', retention_ok)
        failed += int(not retention_ok)
        st, _, _ = req("PUT", "/api/devices/companies", {"names": ["Smoke Company"]}, cookie=admin_cookie)
        read_st, companies_body, _ = req("GET", "/api/devices/companies", cookie=admin_cookie)
        ok = st == 200 and read_st == 200 and any(c['company'] == 'Smoke Company' for c in json.loads(companies_body))
        passed += log("company catalog persists in upgraded database", ok)
        failed += int(not ok)
        firewall_config={'name':'offline-firewall','provider':'fortinet','server':'fw.example.invalid','scope':'198.18.251.0','interface':'port5','vdom':'office','api_token':'smoke-fake-token','mode':'DHCP','interval':0,'threshold':80}
        fw_status,fw_body,_=req('POST','/api/integrations/dhcp',firewall_config,cookie=admin_cookie)
        fw_data=json.loads(fw_body)
        duplicate_status,_,_=req('POST','/api/integrations/dhcp',firewall_config,cookie=admin_cookie)
        second_status,_,_=req('POST','/api/integrations/dhcp',{**firewall_config,'vdom':'guest'},cookie=admin_cookie)
        ok=fw_status==200 and fw_data.get('api_token_configured') and 'smoke-fake-token' not in fw_body and 'api_token_enc' not in fw_body and duplicate_status==409 and second_status==200
        passed+=log('Firewall sources: VDOM isolation, encrypted key redaction and dedup (no network)',ok)
        failed+=int(not ok)
        static_status,provider_body,_=req('GET','/static/js/dhcp_provider.js',cookie=admin_cookie)
        ok=static_status==200 and 'fortinet' in provider_body and 'paloalto' in provider_body
        passed+=log('DHCP provider selector is bundled',ok)
        failed+=int(not ok)

        duplicate_csv = {'csv':'subnet,mask,remarks\n198.18.250.0,255.255.255.0,keep\n198.18.250.0/24,,do-not-overwrite'}
        st1, body1, _ = req('POST', '/api/ip-inventory/import', duplicate_csv, cookie=admin_cookie)
        st2, body2, _ = req('POST', '/api/ip-inventory/import', duplicate_csv, cookie=admin_cookie)
        ok = st1 == st2 == 200 and json.loads(body1).get('created') == 1 and json.loads(body1).get('skipped') == 1 and json.loads(body2).get('created') == 0 and json.loads(body2).get('skipped') == 2
        passed += log('CSV repeated import is idempotent in frozen build', ok)
        failed += int(not ok)
        st, rack_body, _ = req('GET', '/static/js/rack_view.js', cookie=admin_cookie)
        ok = st == 200 and 'data-rack-u' in rack_body and 'grid-template-columns' in rack_body
        passed += log('Shared aligned rack renderer is bundled', ok)
        failed += int(not ok)

        for endpoint in ('devices', 'vms'):
            st, _, _ = req("POST", f"/api/{endpoint}/batch-delete", {"ids": []}, cookie=admin_cookie)
            ok = st == 422
            passed += log(f"{endpoint} rejects empty bulk deletion", ok)
            failed += int(not ok)

        st, _, _ = req("GET", "/", cookie=admin_cookie)
        passed += log("GET / with admin cookie -> 200", st == 200, f"code={st}")
        failed += (0 if st == 200 else 1)

        # confirm the user-management modal fix is bundled in the build
        st, sbody, _ = req("GET", "/settings", cookie=admin_cookie)
        ok = st == 200 and "新增账号" in sbody and "modal-overlay active" in sbody and 'class="modal-backdrop"' not in sbody
        passed += log("settings page: 新增账号 + modal-overlay (no modal-backdrop class)", ok, f"code={st}")
        failed += (0 if ok else 1)

        st, changes_body, _ = req("GET", "/changes", cookie=admin_cookie)
        ok = st == 200 and "配置变更中心" in changes_body and "/static/js/changes.js" in changes_body
        passed += log("configuration change center is bundled", ok, f"code={st}")
        failed += (0 if ok else 1)

        st, integrations_body, _ = req("GET", "/integrations", cookie=admin_cookie)
        ok = (st == 200 and "平台集成" in integrations_body and "差异预演" in integrations_body
              and "同步历史" in integrations_body and "zabbix-source-ip" in integrations_body
              and "/static/js/integrations.js" in integrations_body)
        passed += log("Zabbix/vCenter integration page is bundled", ok, f"code={st}")
        failed += (0 if ok else 1)

        nic_status, nic_body, _ = req(
            "GET", "/api/integrations/network-interfaces", cookie=admin_cookie,
        )
        try:
            nic_data = json.loads(nic_body)
            nic_ok = nic_status == 200 and nic_data.get("count", 0) >= 1 and nic_data.get("interfaces")
        except Exception:
            nic_ok = False
        passed += log("bundled exe detects selectable Windows NICs", bool(nic_ok), f"code={nic_status}")
        failed += (0 if nic_ok else 1)
        selectable = next((row for row in (nic_data.get("interfaces") or [])
                           if row.get("is_up") and not row.get("is_loopback")), None) if nic_ok else None
        selected_ok = False
        if selectable:
            select_status, _, _ = req(
                "PUT", "/api/nics/selection", {"source_ip": selectable["ip_address"]},
                cookie=admin_cookie,
            )
            read_status, read_body, _ = req("GET", "/api/nics", cookie=admin_cookie)
            try:
                selected_ok = (select_status == 200 and read_status == 200
                               and json.loads(read_body).get("selected_ip") == selectable["ip_address"])
            except Exception:
                selected_ok = False
            req("PUT", "/api/nics/selection", {"source_ip": ""}, cookie=admin_cookie)
        passed += log("default NIC can be selected and persisted", selected_ok)
        failed += (0 if selected_ok else 1)

        st, schedule_body, _ = req("GET", "/schedule", cookie=admin_cookie)
        ok = st == 200 and "Zabbix 资产同步" in schedule_body and "vCenter 资产同步" in schedule_body
        passed += log("scheduled integration sync options are bundled", ok, f"code={st}")
        failed += (0 if ok else 1)

        runs_status, runs_body, _ = req("GET", "/api/integrations/sync-runs", cookie=admin_cookie)
        storage_status, storage_body, _ = req("GET", "/api/integrations/storage", cookie=admin_cookie)
        try:
            schema_ok = isinstance(json.loads(runs_body), list) and isinstance(json.loads(storage_body), list)
        except Exception:
            schema_ok = False
        ok = runs_status == 200 and storage_status == 200 and schema_ok
        passed += log("sync history and storage schema migrated in bundled exe", ok, f"runs={runs_status} storage={storage_status}")
        failed += (0 if ok else 1)

        # Exercise the bundled pyVmomi import path without requiring a live
        # vCenter: a closed local port must produce a normal connection error,
        # never a missing-module/package error.
        st, _, _ = req(
            "PUT", "/api/integrations/vcenter/config",
            {"host": "127.0.0.1", "port": 1, "username": "smoke",
             "password": "smoke-only-secret", "verify_ssl": False, "timeout": 3},
            cookie=admin_cookie,
        )
        test_status, test_body, _ = req(
            "POST", "/api/integrations/vcenter/test", cookie=admin_cookie,
        )
        lowered_error = test_body.lower()
        ok = st == 200 and test_status == 502 and "no module named" not in lowered_error and "未安装" not in test_body
        passed += log("bundled pyVmomi reaches vCenter connection layer", ok, f"save={st} test={test_status}")
        failed += (0 if ok else 1)

        # admin creates a limited user (devices only)
        operator_password = "P@ssword-For-Smoke-2026"
        r = req("POST", "/api/users", {"username": "op1", "password": operator_password,
                                       "is_superuser": False, "is_active": True,
                                       "modules": ["devices"]}, cookie=admin_cookie)
        passed += log("admin create op1 -> 200", r[0] == 200 and json.loads(r[1]).get("ok"), f"code={r[0]}")
        failed += (0 if r[0] == 200 else 1)

        st, body, ck2 = req(
            "POST", "/api/auth/login",
            {"username": "op1", "password": operator_password},
        )
        ok = st == 200 and ck2
        passed += log("op1 login -> 200", ok, f"code={st}")
        failed += (0 if ok else 1)
        op_cookie = ck2.split(";")[0] if ck2 else None

        st, _, _ = req("GET", "/devices", cookie=op_cookie)
        passed += log("op1 GET /devices -> 200", st == 200, f"code={st}")
        failed += (0 if st == 200 else 1)

        try:
            acc_hdr = {"Cookie": clean_cookie(op_cookie), "Connection": "close"}
            acc_req = urllib.request.Request(BASE + "/accounts", headers=acc_hdr)
            urllib.request.urlopen(acc_req, timeout=5)
            passed += log("op1 GET /accounts -> 307", False, "got 200")
            failed += 1
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
            ok = e.code == 307 and "/forbidden" in loc
            passed += log("op1 GET /accounts -> 307 /forbidden", ok, f"code={e.code} loc={loc}")
            failed += (0 if ok else 1)

        st, _, _ = req("GET", "/api/accounts", cookie=op_cookie)
        passed += log("op1 GET /api/accounts -> 403", st == 403, f"code={st}")
        failed += (0 if st == 403 else 1)

        st, _, _ = req("GET", "/api/users", cookie=op_cookie)
        passed += log("op1 GET /api/users -> 403 (superuser only)", st == 403, f"code={st}")
        failed += (0 if st == 403 else 1)

        # cleanup op1
        req("DELETE", "/api/users/2", cookie=admin_cookie)

        print(f"\n=== smoke: {passed} passed, {failed} failed ===")
    finally:
        # Kill the exact PyInstaller bootloader process tree while the parent
        # still exists. Killing only the parent first can orphan its child.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        time.sleep(2)
        for _ in range(15):
            try:
                _force_rmtree(SMOKE)
                break
            except (PermissionError, OSError):
                time.sleep(1)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
