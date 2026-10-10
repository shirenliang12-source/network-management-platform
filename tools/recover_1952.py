"""Recover a candidate source tree from a local source archive and patch records.

Never execute historical shell/tool commands. Only replay validated patch text
inside a new directory. This is source reconstruction, not a binary identity claim.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'dist_onefile/network-management-platform-v1.9.51-docker-source.zip'
DEST = ROOT / 'recovery/v1.9.52-candidate'
LOG = Path(r'C:\Users\Administrator\.codex\sessions\2026\09\01\rollout-2026-09-01T16-20-43-01a05c0e-6920-7ec1-bbae-f36e4d795032.jsonl')
PATCHER = r'C:\Users\Administrator\AppData\Local\OpenAI\Codex\bin\bffc5354119c8421\codex.exe'


def patches():
    records = [json.loads(line) for line in LOG.open(encoding='utf-8')]
    outputs = {r['payload'].get('call_id'): r['payload'].get('output', '')
               for r in records if r.get('payload', {}).get('type') in ('custom_tool_call_output', 'function_call_output')}
    for record in records:
        timestamp = record.get('timestamp', '')
        payload = record.get('payload', {})
        if not ('2026-09-15T00:44:40' < timestamp < '2026-09-15T06:39:24'):
            continue
        if payload.get('type') != 'custom_tool_call':
            continue
        source = payload.get('input', '')
        if '*** Begin Patch' not in source:
            continue
        match = re.search(r'const patch = ("(?:\\.|[^"\\])*")', source)
        if match:
            patch = json.loads(match.group(1))
        else:
            match = re.search(r'cmd:("(?:\\.|[^"\\])*")', source)
            if not match:
                raise RuntimeError('Unrecognized patch encoding: ' + timestamp)
            command = json.loads(match.group(1))
            start = command.index('*** Begin Patch')
            end = command.index('*** End Patch', start) + len('*** End Patch')
            patch = command[start:end]
        # Patch metadata must be relative and cannot escape the candidate tree.
        paths = re.findall(r'^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$', patch, re.M)
        if not paths or any(Path(p).is_absolute() or '..' in Path(p).parts or ':' in p for p in paths):
            raise RuntimeError('Unsafe patch path')
        yield timestamp, payload['call_id'], patch, paths, str(outputs.get(payload['call_id'], ''))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    history = list(patches())
    if '--inspect' in sys.argv:
        for timestamp, call, patch, paths, output in history:
            print(timestamp, len(patch), ','.join(paths), 'RESULT', output[:180])
        return
    if DEST.exists():
        raise RuntimeError('Destination already exists; refusing overwrite')
    with zipfile.ZipFile(ARCHIVE) as archive:
        for item in archive.infolist():
            path = Path(item.filename)
            if path.is_absolute() or '..' in path.parts or ':' in item.filename:
                raise RuntimeError('Unsafe archive member')
        DEST.mkdir(parents=True)
        archive.extractall(DEST)
    applied = []
    for timestamp, call, patch, paths, output in history:
        result = subprocess.run([PATCHER, '--codex-run-as-apply-patch', patch], cwd=DEST,
                                capture_output=True, encoding='utf-8', errors='replace')
        print(timestamp, result.returncode, result.stdout[:150], result.stderr[:150])
        if result.returncode:
            raise RuntimeError('Patch failed; partial candidate retained for inspection: ' + call)
        applied.append({'timestamp': timestamp, 'call_id': call, 'files': paths,
                        'patch_sha256': hashlib.sha256(patch.encode()).hexdigest()})
    # A generated provenance artifact; contains no command text or credentials.
    manifest = {'status': 'reconstructed, not binary-verified',
                'archive_sha256': hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
                'patches': applied}
    (DEST.parent / 'v1.9.52-provenance.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Recovered candidate:', DEST)


if __name__ == '__main__':
    main()
