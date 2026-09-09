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

try:
    import sitecustomize
except ImportError:
    sitecustomize = None

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_EXE = os.path.join(ROOT, "dist_onefile", "CiscoNetworkManager.exe")
SMOKE = os.path.join(ROOT, "_smoke")
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"
with open(os.path.join(ROOT, "app", "config.py"), encoding="utf-8") as _config_file:
    _version_match = re.search(
        r'APP_VERSION:\s*str\s*=\s*"([\d.]+)"', _config_file.read()
    )
EXPECTED_VERSION = _version_match.group(1) if _version_match else ""


def _force_rmtree(p):
    if sitecustomize is not None:
        if hasattr(sitecustomize, "_orig_remove"):
            os.remove = sitecustomize._orig_remove
            os.unlink = sitecustomize._orig_unlink
        if hasattr(sitecustomize, "_orig_rmdir"):
            os.rmdir = sitecustomize._orig_rmdir
        if hasattr(sitecustomize, "_orig_shutil_rmtree"):
            shutil.rmtree = sitecustomize._orig_shutil_rmtree
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
        ok = (revision == "20260908_0005" and marker == "preserved-from-v1.9.36"
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
