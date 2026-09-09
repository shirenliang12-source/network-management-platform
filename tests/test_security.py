"""Focused regression tests for the security-sensitive fixes."""
import base64
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("NETMGR_SECRET_KEY", "test-only-secret-key-1234567890-abcdef")
_TEST_DATA = tempfile.TemporaryDirectory(prefix="netmgr-tests-")
os.environ.setdefault("CISCO_NM_DATA_DIR", _TEST_DATA.name)

from app import auth
from app.api_models import CommandPayload, LoginRequest
from app.models import decrypt_password, encrypt_password
from app.services import data_backup


def _token_for(username: str, issued_at: int) -> str:
    payload = f"{username}|{issued_at}"
    raw = f"{payload}|{auth._sign(payload)}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _zip_bytes(entries) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


class AuthenticationSecurityTests(unittest.TestCase):
    def test_request_models_do_not_transform_passwords_or_commands(self):
        login = LoginRequest(username="  admin  ", password="  significant spaces  ")
        command = CommandPayload(command="  show running-config | include foo  ")
        self.assertEqual(login.username, "admin")
        self.assertEqual(login.password, "  significant spaces  ")
        self.assertEqual(command.command, "  show running-config | include foo  ")

    def test_token_expiry_and_future_timestamp_are_rejected(self):
        now = int(time.time())
        self.assertEqual(auth.verify_token(_token_for("admin", now)), "admin")
        self.assertIsNone(
            auth.verify_token(_token_for("admin", now - auth.SESSION_MAX_AGE - 1))
        )
        self.assertIsNone(auth.verify_token(_token_for("admin", now + 301)))

    def test_tampered_token_is_rejected(self):
        token = auth.make_token("admin")
        raw = bytearray(base64.urlsafe_b64decode(token))
        raw[-1] = ord("a") if raw[-1] != ord("a") else ord("b")
        self.assertIsNone(auth.verify_token(base64.urlsafe_b64encode(raw).decode()))

    def test_password_hash_does_not_accept_unknown_algorithm(self):
        stored = auth.hash_password("long-enough-test-password")
        self.assertTrue(auth.verify_password("long-enough-test-password", stored))
        self.assertFalse(auth.verify_password("wrong", stored))
        self.assertFalse(auth.verify_password("long-enough-test-password", stored.replace("pbkdf2$", "sha256$", 1)))

    def test_password_change_revokes_new_format_session(self):
        from app import auth
        from app.database import SessionLocal, init_db
        from app.models import User

        init_db()
        with SessionLocal() as db:
            existing = db.query(User).filter_by(username="session-revoke-test").first()
            if existing:
                db.delete(existing)
                db.commit()
        user = auth.create_user("session-revoke-test", "first-password-123", False, True, [])
        token = auth.make_token(user.username)
        self.assertEqual(auth.verify_token(token), user.username)
        auth.update_user(user.id, password="second-password-456")
        self.assertIsNone(auth.verify_token(token))
        auth.delete_user(user.id)

    def test_permission_mapping_is_segment_aware_and_specific(self):
        self.assertEqual(auth.module_for_path("/api/assets/servers/1"), "servers")
        self.assertEqual(auth.module_for_path("/api/logs"), "logs")
        self.assertIsNone(auth.module_for_path("/api/users-export"))


class CredentialEncryptionTests(unittest.TestCase):
    def test_round_trip_uses_versioned_authenticated_encryption(self):
        encrypted = encrypt_password("S3cret-password!")
        self.assertTrue(encrypted.startswith("v2:"))
        self.assertNotIn("S3cret-password!", encrypted)
        self.assertEqual(decrypt_password(encrypted), "S3cret-password!")

    def test_ciphertext_tampering_is_rejected(self):
        encrypted = encrypt_password("S3cret-password!")
        payload = bytearray(base64.urlsafe_b64decode(encrypted[3:]))
        payload[-1] ^= 1
        tampered = "v2:" + base64.urlsafe_b64encode(payload).decode("ascii")
        self.assertEqual(decrypt_password(tampered), "")


class BackupArchiveSecurityTests(unittest.TestCase):
    def test_restore_rejects_backup_from_different_encryption_key(self):
        with tempfile.TemporaryDirectory() as source:
            db_path = Path(source) / "netmgr.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE devices (id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            archive = _zip_bytes(
                [
                    ("netmgr.db", db_path.read_bytes()),
                    (
                        "manifest.json",
                        json.dumps(
                            {
                                "app_version": "test",
                                "secret_key_fingerprint": "0" * 64,
                            }
                        ),
                    ),
                ]
            )
        result = data_backup.restore_data_backup(archive)
        self.assertFalse(result["ok"])
        self.assertIn("安装密钥", result["error"])

    def test_path_traversal_is_rejected(self):
        archive = _zip_bytes([("../../netmgr.db", b"not-a-db")])
        with tempfile.TemporaryDirectory() as target:
            with self.assertRaisesRegex(ValueError, "不安全路径"):
                data_backup._extract_validated_archive(archive, target)

    def test_unexpected_and_duplicate_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as target:
            with self.assertRaisesRegex(ValueError, "不允许"):
                data_backup._extract_validated_archive(
                    _zip_bytes([("backup/secrets.txt", b"secret")]), target
                )
            with self.assertRaisesRegex(ValueError, "重复"):
                data_backup._extract_validated_archive(
                    _zip_bytes(
                        [("one/manifest.json", b"{}"), ("two/manifest.json", b"{}")]
                    ),
                    target,
                )

    def test_expected_nested_files_are_flattened_safely(self):
        archive = _zip_bytes(
            [("snapshot/manifest.json", b"{}"), ("snapshot/commands.json", b"{}")]
        )
        with tempfile.TemporaryDirectory() as target:
            extracted = data_backup._extract_validated_archive(archive, target)
            self.assertEqual(set(extracted), {"manifest.json", "commands.json"})
            self.assertEqual(Path(extracted["manifest.json"]).parent, Path(target))


class UpgradePersistenceTests(unittest.TestCase):
    def test_single_instance_lock_rejects_same_data_directory(self):
        import run

        with tempfile.TemporaryDirectory(prefix="netmgr-instance-lock-") as data_dir:
            run._acquire_instance_lock(data_dir)
            try:
                with self.assertRaises(RuntimeError):
                    run._acquire_instance_lock(data_dir)
            finally:
                run._release_instance_lock()

    def test_duplicate_external_ids_are_preserved_and_isolated_during_upgrade(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="netmgr-duplicate-upgrade-") as data_dir:
            env = os.environ.copy()
            env["CISCO_NM_DATA_DIR"] = data_dir
            env.pop("NETMGR_SECRET_KEY", None)
            script = textwrap.dedent(
                """
                import sqlite3
                from pathlib import Path
                from alembic import command
                from alembic.config import Config
                from app.config import DATA_DIR

                cfg = Config("alembic.ini")
                command.upgrade(cfg, "20260903_0003")
                db_path = Path(DATA_DIR) / "netmgr.db"
                connection = sqlite3.connect(db_path)
                connection.execute("INSERT INTO vm_instances (id,name,source_type,external_id,source_endpoint) VALUES (1,'vm-a','vcenter','vm-101','vc:443')")
                connection.execute("INSERT INTO vm_instances (id,name,source_type,external_id,source_endpoint) VALUES (2,'vm-b','vcenter','vm-101','vc:443')")
                connection.commit()
                connection.close()
                command.upgrade(cfg, "head")
                connection = sqlite3.connect(db_path)
                rows = connection.execute("SELECT name,external_id,sync_state FROM vm_instances ORDER BY id").fetchall()
                assert rows[0] == ('vm-a', 'vm-101', 'active')
                assert rows[1] == ('vm-b', 'vm-101#duplicate-2', 'stale_confirmed')
                assert connection.execute("SELECT COUNT(*) FROM vm_instances").fetchone()[0] == 2
                connection.close()
                """
            )
            subprocess.run(
                [sys.executable, "-c", script], cwd=project_root, env=env,
                text=True, capture_output=True, check=True,
            )

    def test_repeated_start_preserves_database_commands_and_encryption_key(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="netmgr-upgrade-") as data_dir:
            env = os.environ.copy()
            env["CISCO_NM_DATA_DIR"] = data_dir
            env.pop("NETMGR_SECRET_KEY", None)
            first_start = textwrap.dedent(
                """
                from pathlib import Path
                from app.config import DATA_DIR
                from app.database import SessionLocal, init_db, engine
                from app.models import CredentialProfile, SystemSetting
                from app.services.command_config import load_commands, save_commands

                init_db()
                with SessionLocal() as db:
                    db.add(SystemSetting(key="upgrade_marker", value="keep-me"))
                    profile = CredentialProfile(name="upgrade-profile", username="operator")
                    profile.set_password("credential-survives-upgrade")
                    db.add(profile)
                    db.commit()
                commands = load_commands()
                commands["custom_platform"] = {
                    "probe": {"command": " show version ", "description": "custom", "delay_factor": 1.0}
                }
                save_commands(commands)
                print((Path(DATA_DIR) / ".secret_key").read_text(encoding="utf-8").strip())
                engine.dispose()
                """
            )
            first = subprocess.run(
                [sys.executable, "-c", first_start],
                cwd=project_root,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            original_key = first.stdout.strip().splitlines()[-1]

            second_start = textwrap.dedent(
                """
                from pathlib import Path
                from sqlalchemy import text
                from app.config import DATA_DIR
                from app.database import SessionLocal, init_db, engine
                from app.models import CredentialProfile, SystemSetting
                from app.services.command_config import load_commands

                init_db()
                with SessionLocal() as db:
                    assert db.get(SystemSetting, "upgrade_marker").value == "keep-me"
                    profile = db.query(CredentialProfile).filter_by(name="upgrade-profile").one()
                    assert profile.get_password() == "credential-survives-upgrade"
                    revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    assert revision == "20260908_0005"
                commands = load_commands()
                assert commands["custom_platform"]["probe"]["command"] == " show version "
                print((Path(DATA_DIR) / ".secret_key").read_text(encoding="utf-8").strip())
                engine.dispose()
                """
            )
            second = subprocess.run(
                [sys.executable, "-c", second_start],
                cwd=project_root,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertGreaterEqual(len(original_key), 32)
            self.assertEqual(second.stdout.strip().splitlines()[-1], original_key)
            status = json.loads(
                (Path(data_dir) / "backups" / "upgrade_status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "success")
            self.assertEqual(status["revision_after"], "20260908_0005")
            self.assertTrue(Path(status["backup_path"]).is_file())

    def test_installers_do_not_overwrite_or_delete_persistent_configuration(self):
        root = Path(__file__).resolve().parents[1]
        nsi = (root / "installer" / "install.nsi").read_text(encoding="utf-8")
        install_ps1 = (root / "installer" / "install.ps1").read_text(encoding="utf-8")
        uninstall_ps1 = (root / "installer" / "uninstall.ps1").read_text(encoding="utf-8")

        self.assertNotIn('File "payload\\data\\commands.json"', nsi)
        self.assertNotIn("Copy-Item $cmd", install_ps1)
        self.assertIn('"DataDir" "$DataDir"', nsi)
        self.assertIn("CiscoNetworkManager.previous.exe", nsi)
        self.assertIn("Invoke-RestMethod", nsi)
        self.assertIn("Health check passed", nsi)
        self.assertNotIn("cmd /c copy /Y", nsi)
        self.assertIn("New-ItemProperty -Path $StateKey -Name DataDir", install_ps1)
        self.assertIn(".secret_key", nsi)
        self.assertIn("/E /COPY:DAT", install_ps1)
        self.assertNotIn('RMDir /r "$INSTDIR"', nsi)
        self.assertNotIn("Remove-Item $InstallDir -Recurse", uninstall_ps1)

    def test_existing_baseline_upgrades_without_losing_backup_history(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="netmgr-migration-") as data_dir:
            env = os.environ.copy()
            env["CISCO_NM_DATA_DIR"] = data_dir
            env.pop("NETMGR_SECRET_KEY", None)
            script = textwrap.dedent(
                """
                import sqlite3
                from pathlib import Path
                from alembic import command
                from alembic.config import Config
                from app.config import DATA_DIR

                cfg = Config("alembic.ini")
                command.upgrade(cfg, "20260902_0001")
                db_path = Path(DATA_DIR) / "netmgr.db"
                connection = sqlite3.connect(db_path)
                connection.execute(
                    "INSERT INTO devices (id,name,ip_address,device_type,username) VALUES (1,'core','10.0.0.1','cisco_ios','admin')"
                )
                connection.execute(
                    "INSERT INTO config_backups (id,device_id,config_text,config_hash,backup_time,is_changed) VALUES (1,1,'old','hash-old','2026-01-01 00:00:00',1)"
                )
                connection.execute(
                    "INSERT INTO config_backups (id,device_id,config_text,config_hash,backup_time,is_changed) VALUES (2,1,'new','hash-new','2026-01-02 00:00:00',1)"
                )
                connection.execute(
                    "INSERT INTO vm_instances (id,name) VALUES (1,'legacy-vm')"
                )
                connection.commit()
                connection.close()

                command.upgrade(cfg, "head")
                connection = sqlite3.connect(db_path)
                rows = connection.execute(
                    "SELECT id, config_text, review_status, is_baseline FROM config_backups ORDER BY id"
                ).fetchall()
                revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                assert rows == [(1, 'old', 'ignored', 1), (2, 'new', 'pending', 0)]
                vm_columns = {row[1] for row in connection.execute("PRAGMA table_info(vm_instances)")}
                assert {"source_type", "external_id", "source_endpoint", "last_synced_at", "sync_state", "stale_since", "sync_locked_fields"} <= vm_columns
                legacy_vm = connection.execute(
                    "SELECT name, source_type, external_id FROM vm_instances WHERE id=1"
                ).fetchone()
                assert legacy_vm == ('legacy-vm', 'manual', None)
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                assert {"integration_storage_assets", "vm_storage_links", "integration_sync_runs"} <= tables
                assert revision == "20260908_0005"
                connection.close()
                """
            )
            subprocess.run(
                [sys.executable, "-c", script], cwd=project_root, env=env,
                text=True, capture_output=True, check=True,
            )


class IntegrationServiceTests(unittest.TestCase):
    def test_scheduled_sync_retries_and_records_success(self):
        from app.database import SessionLocal, init_db
        from app.services import integration_sync_service

        init_db()
        inventory = {
            "source": "vcenter", "version": "8.0", "fetched_at": "2026-09-07T14:00:00",
            "summary": {"vms": 0, "storage": 0}, "vms": [], "storage": [],
        }
        with SessionLocal() as db, patch(
            "app.services.integration_sync_service.load_stored_config", return_value={"host": "vc", "port": 443}
        ), patch(
            "app.services.integration_sync_service.discover",
            side_effect=[RuntimeError("temporary failure"), inventory],
        ):
            run = integration_sync_service.run_scheduled_sync("vcenter", db, max_attempts=3)
            self.assertEqual(run.status, "success")
            self.assertEqual(run.attempt, 2)
            self.assertEqual(run.max_attempts, 3)

    def test_zabbix_inventory_is_normalized_for_vm_import(self):
        from app.services import integration_service

        class FakeZabbixClient:
            def __init__(self, _config):
                self.logged_out = False

            def login(self, _username, _password):
                return "token"

            def logout(self):
                self.logged_out = True

            def call(self, method, params, authenticated=True):
                if method == "apiinfo.version":
                    return "7.4.0"
                if method == "host.get":
                    self.host_limit = params["limit"]
                    return [{
                        "hostid": "101",
                        "host": "vm-01.internal",
                        "name": "vm-01",
                        "status": "0",
                        "interfaces": [
                            {"ip": "10.0.0.10"},
                            {"ip": "10.0.0.11"},
                            {"ip": "127.0.0.1"},
                        ],
                        "inventory": {
                            "os_full": "Rocky Linux 9.4",
                            "type_full": "Application server",
                            "host_networks": "esxi-01",
                        },
                    }]
                if method == "item.get":
                    key = params["search"]["key_"]
                    if key == "system.cpu.num":
                        return [{"hostid": "101", "name": "CPU count", "key_": "system.cpu.num", "lastvalue": "4"}]
                    if key == "memory.size":
                        return [{"hostid": "101", "name": "Memory", "key_": "vm.memory.size[total]", "lastvalue": str(8 * 1024**3)}]
                    if key == "vfs.fs.size":
                        return [{"hostid": "101", "name": "/ data", "key_": "vfs.fs.size[/,total]", "lastvalue": str(120 * 1024**3)}]
                    return []
                raise AssertionError(f"unexpected method: {method}")

        fake = FakeZabbixClient({})
        with patch("app.services.integration_service._ZabbixClient", return_value=fake):
            result = integration_service.discover_zabbix(
                {"username": "api", "password": "secret"}, limit=9999,
            )

        self.assertTrue(fake.logged_out)
        self.assertEqual(fake.host_limit, 2001)
        self.assertEqual(result["summary"], {"vms": 1, "storage": 1})
        vm = result["vms"][0]
        self.assertEqual(vm["external_id"], "101")
        self.assertEqual(vm["os_type"], "Linux")
        self.assertEqual(vm["cpu"], "4 vCPU")
        self.assertEqual(vm["memory"], "8.0 GB")
        self.assertEqual(vm["disk_size"], "120 GB")
        self.assertEqual(vm["management_ip"], "10.0.0.10")
        self.assertEqual(vm["additional_ips"], ["10.0.0.11"])
        self.assertEqual(vm["storage_external_ids"], ["101:/"])
        self.assertEqual(result["storage"][0]["capacity"], "120 GB")

    def test_zabbix_os_falls_back_to_agent_items(self):
        from app.services.integration_service import _os_type, _zabbix_os_description

        windows = _zabbix_os_description({}, [{
            "key_": "system.sw.os[full]",
            "lastvalue": "Microsoft Windows Server 2022 Datacenter",
        }])
        linux = _zabbix_os_description({}, [{
            "key_": "system.sw.os.get",
            "lastvalue": '{"name":"Rocky Linux","version":"9.4"}',
        }])
        self.assertEqual(_os_type(windows), "Windows")
        self.assertEqual(linux, "Rocky Linux 9.4")
        self.assertEqual(_os_type(linux), "Linux")

    def test_zabbix_http_client_binds_selected_source_ip(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from app.services.integration_service import _ZabbixClient

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                body = b'{"jsonrpc":"2.0","result":"7.4.0","id":1}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = _ZabbixClient({
                "url": f"http://127.0.0.1:{server.server_port}",
                "source_ip": "127.0.0.1", "timeout": 3, "verify_ssl": False,
            })
            self.assertEqual(client.call("apiinfo.version", [], authenticated=False), "7.4.0")
            self.assertEqual(client.source_ip, "127.0.0.1")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_windows_nic_fallback_extracts_adapter_and_ipv4(self):
        from app.services import nic_service

        output = (
            "Ethernet adapter Production LAN:\n\n"
            "   Connection-specific DNS Suffix  . : corp.local\n"
            "   IPv4 Address. . . . . . . . . . . : 10.20.30.40(Preferred)\n"
        )
        completed = subprocess.CompletedProcess(["ipconfig"], 0, stdout=output, stderr="")
        with patch("platform.system", return_value="Windows"), patch(
            "subprocess.run", return_value=completed,
        ):
            rows = nic_service._get_interfaces_fallback()
        self.assertEqual(rows, [{
            "name": "Production LAN", "ip_address": "10.20.30.40",
            "is_up": True, "is_loopback": False,
        }])


class ApplicationSecurityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
        except ImportError as exc:
            raise unittest.SkipTest(f"httpx/TestClient is not installed: {exc}")
        from app.main import app

        cls._context = TestClient(app)
        cls.client = cls._context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._context.__exit__(None, None, None)

    def test_public_health_has_security_headers(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_unauthenticated_api_and_cross_site_login_are_rejected(self):
        self.client.cookies.clear()
        response = self.client.get("/api/devices")
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "irrelevant"},
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_login_cookie_and_fail_closed_permissions(self):
        from app.config import INITIAL_ADMIN_PASSWORD_FILE

        initial_password = INITIAL_ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": initial_password},
        )
        self.assertEqual(response.status_code, 200)
        cookie = response.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)

        response = self.client.post(
            "/api/users",
            json={
                "username": "dashboard-only",
                "password": "dashboard-only-password",
                "is_superuser": False,
                "is_active": True,
                "modules": ["dashboard"],
            },
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/api/users",
            json={
                "username": "scheduler-only",
                "password": "scheduler-only-password",
                "is_superuser": False,
                "is_active": True,
                "modules": ["schedule"],
            },
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/api/users",
            json={
                "username": "invalid-extra-field",
                "password": "valid-password-123",
                "is_superuser": False,
                "is_active": True,
                "modules": [],
                "unexpected": "must be rejected",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("valid-password-123", response.text)
        users = self.client.get("/api/users").json()["users"]
        admin_id = next(user["id"] for user in users if user["username"] == "admin")
        response = self.client.put(
            f"/api/users/{admin_id}",
            json={
                "username": "admin",
                "password": "",
                "is_superuser": True,
                "is_active": False,
                "modules": [],
            },
        )
        self.assertEqual(response.status_code, 400)

        account = self.client.post(
            "/api/accounts",
            json={
                "name": "audit-vault-entry",
                "username": "vault-user",
                "password": "vault-secret-must-not-be-logged",
                "category": "测试",
            },
        )
        self.assertEqual(account.status_code, 200)
        reveal = self.client.post(f"/api/accounts/{account.json()['id']}/reveal")
        self.assertEqual(reveal.status_code, 200)
        self.assertEqual(reveal.json()["password"], "vault-secret-must-not-be-logged")
        audit_response = self.client.get("/api/logs/audit?since_hours=1")
        self.assertEqual(audit_response.status_code, 200)
        audit_items = audit_response.json()["items"]
        self.assertTrue(any(item["action"] == "auth.login" for item in audit_items))
        self.assertTrue(any(item["path"] == "/api/users" for item in audit_items))
        self.assertTrue(any(item["action"] == "vault.reveal_password" for item in audit_items))
        self.assertNotIn("dashboard-only-password", str(audit_items))
        self.assertNotIn("vault-secret-must-not-be-logged", str(audit_items))

        settings_page = self.client.get("/settings")
        self.assertEqual(settings_page.status_code, 200)
        self.assertIn('/static/js/settings.js', settings_page.text)
        self.assertIn('AD / LDAP', settings_page.text)
        settings_js = self.client.get("/static/js/settings.js")
        self.assertEqual(settings_js.status_code, 200)
        self.assertIn("saveDefaultNIC", settings_js.text)
        logs_page = self.client.get("/system/logs")
        self.assertEqual(logs_page.status_code, 200)
        self.assertIn("管理操作审计", logs_page.text)
        self.assertEqual(self.client.get("/api/logs/stats").status_code, 200)
        self.assertEqual(self.client.get("/api/logs/settings").status_code, 200)
        health = self.client.get("/api/system/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertTrue(health.json()["foreign_keys_enabled"])

        from app.database import SessionLocal
        from app.models import ConfigBackup, Device
        with SessionLocal() as db:
            device = Device(
                name="change-center-device", ip_address="10.250.0.1",
                device_type="cisco_ios", username="admin",
            )
            db.add(device)
            db.flush()
            baseline = ConfigBackup(
                device_id=device.id, config_text="hostname old\n", config_hash="old-hash",
                is_changed=False, change_summary="Initial configuration baseline",
                review_status="ignored", is_baseline=True,
            )
            change = ConfigBackup(
                device_id=device.id, config_text="hostname new\n", config_hash="new-hash",
                is_changed=True, change_summary="hostname changed",
                review_status="pending", is_baseline=False,
            )
            db.add_all([baseline, change])
            db.commit()
            baseline_id, change_id, device_id = baseline.id, change.id, device.id

        changes = self.client.get("/api/backups/changes").json()
        item = next(row for row in changes["items"] if row["id"] == change_id)
        self.assertEqual(item["previous_backup_id"], baseline_id)
        self.assertTrue(item["device_drifted"])
        self.assertEqual(item["review_status"], "pending")
        response = self.client.post(
            f"/api/backups/{change_id}/review",
            json={"status": "unexpected", "note": "未经审批的配置变化"},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/backups/{change_id}/baseline")
        self.assertEqual(response.status_code, 200)
        with SessionLocal() as db:
            self.assertFalse(db.get(ConfigBackup, baseline_id).is_baseline)
            reviewed = db.get(ConfigBackup, change_id)
            self.assertTrue(reviewed.is_baseline)
            self.assertEqual(reviewed.review_status, "unexpected")
            self.assertEqual(reviewed.reviewed_by, "admin")
        self.assertEqual(self.client.get("/changes").status_code, 200)
        self.assertEqual(self.client.get("/static/js/changes.js").status_code, 200)

        zabbix_config = {
            "url": "https://zabbix.internal/zabbix",
            "username": "api-reader",
            "password": "zabbix-secret-value",
            "verify_ssl": True,
            "timeout": 20,
            "source_ip": "10.20.30.5",
        }
        response = self.client.put("/api/integrations/zabbix/config", json=zabbix_config)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["password"], "")
        self.assertTrue(response.json()["password_configured"])
        zabbix_config["password"] = ""
        response = self.client.put("/api/integrations/zabbix/config", json=zabbix_config)
        self.assertEqual(response.status_code, 200)
        with SessionLocal() as db:
            from app.models import SystemSetting
            saved = json.loads(db.get(SystemSetting, "integration_zabbix").value)
            self.assertNotIn("zabbix-secret-value", saved["password"])
            self.assertEqual(decrypt_password(saved["password"]), "zabbix-secret-value")
            self.assertEqual(saved["source_ip"], "10.20.30.5")

        with patch(
            "app.routers.integrations.get_network_interfaces",
            return_value=[{"name": "Production LAN", "ip_address": "10.20.30.5", "is_up": True, "is_loopback": False}],
        ), patch("app.routers.integrations.get_default_source_ip", return_value="10.20.30.5"):
            nic_response = self.client.get("/api/integrations/network-interfaces")
        self.assertEqual(nic_response.status_code, 200)
        self.assertEqual(nic_response.json()["interfaces"][0]["ip_address"], "10.20.30.5")

        selectable_nics = [{
            "name": "Production LAN", "ip_address": "10.20.30.5",
            "is_up": True, "is_loopback": False,
        }]
        with patch("app.routers.nics.get_network_interfaces", return_value=selectable_nics):
            selected = self.client.put("/api/nics/selection", json={"source_ip": "10.20.30.5"})
            nic_list = self.client.get("/api/nics")
            rejected = self.client.put("/api/nics/selection", json={"source_ip": "10.99.99.99"})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(nic_list.json()["selected_ip"], "10.20.30.5")
        self.assertTrue(nic_list.json()["selected_available"])
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.put("/api/nics/selection", json={"source_ip": ""}).status_code, 200)

        fake_inventory = {
            "source": "zabbix",
            "version": "7.4.0",
            "fetched_at": "2026-09-07T12:00:00",
            "summary": {"vms": 1, "storage": 1},
            "storage": [{
                "external_id": "storage-101", "name": "datastore-01", "type": "文件系统",
                "capacity": "120 GB", "used_space": "80 GB", "free_space": "40 GB",
                "accessible": True, "vm_count": 1,
            }],
            "vms": [{
                "external_id": "host-101",
                "name": "zbx-vm-01",
                "function": "应用服务器",
                "os_type": "Linux",
                "os_version": "Rocky Linux 9",
                "status": "运行中",
                "host_name": "esxi-01",
                "cpu": "4 vCPU",
                "memory": "8 GB",
                "disk_size": "120 GB",
                "disk_count": 2,
                "disks": [
                    {"name": "/", "size": "40 GB"},
                    {"name": "/data", "size": "80 GB"},
                ],
                "storage_lun": "datastore-01",
                "management_ip": "10.20.30.40",
                "additional_ips": ["10.20.30.41"],
                "storage_external_ids": ["storage-101"],
            }],
        }
        with patch(
            "app.services.integration_service.test_zabbix",
            return_value={"ok": True, "message": "Zabbix 7.4.0 连接成功", "version": "7.4.0"},
        ), patch(
            "app.services.integration_service.discover_zabbix",
            return_value=fake_inventory,
        ):
            self.assertEqual(self.client.post("/api/integrations/zabbix/test").status_code, 200)
            discovered = self.client.post("/api/integrations/zabbix/discover").json()
            self.assertFalse(discovered["vms"][0]["imported"])
            response = self.client.post(
                "/api/integrations/zabbix/import",
                json={"external_ids": ["host-101"], "update_existing": True},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["created"], 1)
            response = self.client.post(
                "/api/integrations/zabbix/import",
                json={"external_ids": ["host-101"], "update_existing": True},
            )
            self.assertEqual(response.json()["unchanged"], 1)

            preview = self.client.post("/api/integrations/zabbix/preview")
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.json()["diff_summary"]["unchanged"], 1)

        from app.models import IntegrationStorage, IntegrationSyncRun, VMInstance
        with SessionLocal() as db:
            imported_vm = db.query(VMInstance).filter_by(
                source_type="zabbix", external_id="host-101"
            ).one()
            self.assertEqual(imported_vm.name, "zbx-vm-01")
            self.assertEqual(imported_vm.storage_lun, "datastore-01")
            self.assertEqual(len(imported_vm.additional_ips), 1)
            self.assertIsNotNone(imported_vm.last_synced_at)
            self.assertEqual(imported_vm.sync_state, "active")
            self.assertEqual([row.external_id for row in imported_vm.storage_assets], ["storage-101"])
            self.assertEqual(db.query(IntegrationStorage).filter_by(source_type="zabbix").count(), 1)
            self.assertGreaterEqual(db.query(IntegrationSyncRun).count(), 2)

        # Operator-owned fields survive subsequent external synchronization.
        response = self.client.put(
            f"/api/vms/{imported_vm.id}",
            json={"function": "本地人工用途", "sync_locked_fields": ["function"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sync_locked_fields"], ["function"])
        with patch("app.services.integration_service.discover_zabbix", return_value=fake_inventory):
            response = self.client.post(
                "/api/integrations/zabbix/sync",
                json={"external_ids": None, "update_existing": True, "mark_missing": False},
            )
            self.assertEqual(response.status_code, 200)
        with SessionLocal() as db:
            self.assertEqual(db.get(VMInstance, imported_vm.id).function, "本地人工用途")

        empty_inventory = {
            "source": "zabbix", "version": "7.4.0", "fetched_at": "2026-09-07T13:00:00",
            "summary": {"vms": 0, "storage": 0}, "storage": [], "vms": [],
        }
        with patch("app.services.integration_service.discover_zabbix", return_value=empty_inventory):
            preview = self.client.post("/api/integrations/zabbix/preview")
            self.assertEqual(preview.json()["diff_summary"]["stale"], 1)
            synced = self.client.post(
                "/api/integrations/zabbix/sync",
                json={"external_ids": None, "update_existing": True, "mark_missing": True},
            )
            self.assertEqual(synced.status_code, 200)
            self.assertEqual(synced.json()["stale"], 1)
        with SessionLocal() as db:
            imported_vm = db.query(VMInstance).filter_by(external_id="host-101").one()
            stale_vm_id = imported_vm.id
            self.assertEqual(imported_vm.sync_state, "stale")
            stale_storage = db.query(IntegrationStorage).filter_by(external_id="storage-101").one()
            stale_storage_id = stale_storage.id
            self.assertEqual(stale_storage.sync_state, "stale")
        response = self.client.post(
            f"/api/integrations/storage/{stale_storage_id}/stale", json={"action": "confirm"},
        )
        self.assertEqual(response.json()["sync_state"], "stale_confirmed")
        response = self.client.post(
            f"/api/integrations/vms/{stale_vm_id}/stale", json={"action": "confirm"},
        )
        self.assertEqual(response.json()["sync_state"], "stale_confirmed")
        response = self.client.post(
            f"/api/integrations/vms/{stale_vm_id}/stale", json={"action": "detach"},
        )
        self.assertEqual(response.json()["sync_state"], "manual")
        with SessionLocal() as db:
            detached = db.get(VMInstance, stale_vm_id)
            self.assertIsNone(detached.external_id)
            self.assertEqual(detached.source_type, "manual")
        history = self.client.get("/api/integrations/sync-runs")
        self.assertEqual(history.status_code, 200)
        self.assertGreaterEqual(len(history.json()), 3)
        schedules = self.client.get("/api/schedule").json()
        self.assertTrue({"zabbix_sync", "vcenter_sync"} <= {row["task_type"] for row in schedules})
        self.assertEqual(self.client.get("/integrations").status_code, 200)
        self.assertEqual(self.client.get("/static/js/integrations.js").status_code, 200)

        from sqlalchemy import text
        from app.database import engine
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(revision, "20260908_0005")
        self.client.post("/api/auth/logout")
        response = self.client.post(
            "/api/auth/login",
            json={"username": "scheduler-only", "password": "scheduler-only-password"},
        )
        self.assertEqual(response.status_code, 200)
        schedule_rows = self.client.get("/api/schedule").json()
        integration_schedule_id = next(
            row["id"] for row in schedule_rows if row["task_type"] == "zabbix_sync"
        )
        self.assertEqual(
            self.client.post(f"/api/schedule/{integration_schedule_id}/run").status_code, 403
        )
        self.client.post("/api/auth/logout")
        response = self.client.post(
            "/api/auth/login",
            json={"username": "dashboard-only", "password": "dashboard-only-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/devices").status_code, 403)
        self.assertEqual(self.client.get("/api/not-registered").status_code, 403)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/api/integrations/config").status_code, 403)

    def test_login_rate_limit(self):
        for _ in range(5):
            response = self.client.post(
                "/api/auth/login", json={"username": "rate-limit-user", "password": "wrong"}
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/auth/login", json={"username": "rate-limit-user", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)


def tearDownModule():
    from app.database import engine

    engine.dispose()
    _TEST_DATA.cleanup()


if __name__ == "__main__":
    unittest.main()
