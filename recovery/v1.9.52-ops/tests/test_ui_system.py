"""Static contract tests for the shared v1.9.52.7 UI system."""
import re
import unittest
from pathlib import Path

from app.auth import module_for_path


ROOT = Path(__file__).resolve().parents[1]


class SharedUiSystemTests(unittest.TestCase):
    def test_cockpit_is_dashboard_authorized_and_routed(self):
        self.assertEqual(module_for_path("/cockpit"), "dashboard")
        pages = (ROOT / "app" / "routers" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('router.add_api_route("/cockpit", _page("cockpit.html")', pages)

    def test_cockpit_has_operational_domains_and_accessible_controls(self):
        source = (ROOT / "app" / "templates" / "cockpit.html").read_text(encoding="utf-8")
        for marker in (
            'class="cockpit-kpis"', 'id="ck-health-ring"', 'id="ck-alerts"',
            'id="ck-ipam-prefixes"', 'id="ck-vm-states"', 'id="ck-isp-util"',
            'id="cockpit-interval"', 'id="cockpit-fullscreen"',
            '/static/js/cockpit.js', '/static/css/cockpit.css',
        ):
            self.assertIn(marker, source)

    def test_all_business_pages_use_shared_shell(self):
        routed = {
            "dashboard.html", "devices.html", "device_detail.html", "topology.html",
            "backups.html", "changes.html", "settings.html", "schedule.html",
            "ip_inventory.html", "ipam.html", "isp.html", "dc.html", "assets.html",
            "servers.html", "accounts.html", "vms.html", "integrations.html",
        }
        for name in routed:
            source = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn('{% extends "base.html" %}', source, name)

    def test_shell_has_navigation_search_mobile_controls_and_live_feedback(self):
        source = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        for marker in (
            'id="appSidebar"', 'id="navSearch"', 'id="sidebarToggle"',
            'id="mobileScrim"', 'id="currentPageTitle"', 'aria-live="polite"',
            'class="nav-section-label"', 'id="mainContent"',
        ):
            self.assertIn(marker, source)

    def test_design_system_covers_core_components_and_responsive_modes(self):
        source = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
        for marker in (
            "Cisco Network Manager design system · v1.9.52.8", ".topbar {",
            "table.data-table th {", ".modal-overlay.active", ".login-shell {",
            ".module-overview {", ".section-jumpbar {", ".table-summary {",
            "@media (max-width: 900px)", "@media (max-width: 720px)",
            "@media (prefers-reduced-motion: reduce)", "@media print",
        ):
            self.assertIn(marker, source)

    def test_shared_script_supports_navigation_keyboard_and_accessibility(self):
        source = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        for marker in (
            "initApplicationShell", "initModuleWorkspace", "MODULE_PRESENTATION",
            "function enhanceUi", "table-summary", "aria-labelledby",
            "event.key === '/'", "event.key === 'Escape'", "nav-logs",
        ):
            self.assertIn(marker, source)
        self.assertRegex(source, re.compile(r"table\.querySelectorAll\('thead th'\)"))


if __name__ == "__main__":
    unittest.main()
