from pathlib import Path
import difflib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'recovery/v1.9.52-enhanced'
if DEST.exists():
    raise SystemExit('Destination exists; refusing overwrite')
paths = '''app/services/ssh_service.py app/services/command_config.py
app/services/device_capabilities.py app/services/lldp_parser.py
app/services/discovery_service.py app/services/firewall_parser.py
app/services/asset_relations.py app/services/device_ip_link.py
app/routers/commands.py app/routers/devices.py app/routers/ipam.py
app/static/js/asset_relations.js app/static/js/device_inventory.js
app/templates/devices.html app/templates/servers.html
tests/test_physical_relations.py tests/test_lldp_safety.py
tests/test_firewall_ssh.py tests/test_server_columns_ui.cjs
docs/device-support-current.md'''.split()
assert all((ROOT/p).is_file() for p in paths)
shutil.copytree(ROOT / 'recovery/v1.9.52-candidate', DEST,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', 'data', '_smoke', 'build', 'dist_onefile'))
patcher = r'C:\Users\Administrator\AppData\Local\OpenAI\Codex\bin\eab8377aebac6c07\codex.exe'
for path in paths:
    content = (ROOT/path).read_text(encoding='utf-8').splitlines(True)
    target = DEST/path
    if target.exists():
        old = target.read_text(encoding='utf-8').splitlines(True)
        lines = list(difflib.unified_diff(old, content))[2:]
        if not lines: continue
        patch = '*** Begin Patch\n*** Update File: '+path+'\n'
        patch += ''.join('@@\n' if line.startswith('@@') else line for line in lines)
    else:
        patch = '*** Begin Patch\n*** Add File: '+path+'\n'+''.join('+'+line for line in content)
    patch = patch.rstrip('\n')+'\n*** End Patch'
    subprocess.run([patcher, '--codex-run-as-apply-patch', patch], cwd=DEST, check=True)
