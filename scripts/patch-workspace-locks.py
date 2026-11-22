import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
package = root / '.runtime/openclaw/node_modules/openclaw'
assert json.loads((package / 'package.json').read_text())['version'] == '2026.9.9'
old = 'process.platform === "win32" ? path.join(os.homedir(), "AppData", "Local", "OpenClaw", "locks", uid === void 0 ? "openclaw-state-owners" : `openclaw-state-owners-${uid}`) : resolveGatewayLockDirForCanonicalStateDir(resolveOpenClawStateDirForDatabasePath(canonical))'
new = 'resolveGatewayLockDirForCanonicalStateDir(resolveOpenClawStateDirForDatabasePath(canonical))'
files = list((package / 'dist').glob('gateway-state-owner-*.mjs'))
files += list((package / 'dist/native-hook-relay').glob('openclaw-state-db-cache-*.mjs'))
for file in files:
    source = file.read_text(encoding='utf-8')
    if old in source:
        assert source.count(old) == 1
        backup = file.with_suffix('.mjs.original')
        if not backup.exists():
            backup.write_text(source, encoding='utf-8')
        file.write_text(source.replace(old, new), encoding='utf-8')
        print('Updated lock directory:', file.name)
    else:
        assert 'const directory = ' + new in source, 'Unexpected OpenClaw lock implementation'
        print('Lock directory already updated:', file.name)

for file in (package / 'dist').glob('tmp-openclaw-dir-*.mjs'):
    source = file.read_text(encoding='utf-8')
    old_temp = '\t\tpreferredDir: options.preferredDir ?? "/tmp/openclaw",'
    new_temp = '\t\ttmpdir: options.tmpdir ?? (process.env.JARVIS_TMP_DIR ? () => process.env.JARVIS_TMP_DIR : undefined),\n' + old_temp
    if 'process.env.JARVIS_TMP_DIR' not in source:
        assert source.count(old_temp) == 1
        backup = file.with_suffix('.mjs.original')
        if not backup.exists():
            backup.write_text(source, encoding='utf-8')
        file.write_text(source.replace(old_temp, new_temp), encoding='utf-8')
        print('Added workspace temp-directory option:', file.name)
