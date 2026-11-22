from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / 'tests'), pattern='test_*.py', top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    tests = sorted((ROOT / 'tests').rglob('test_*.mjs'))
    if not tests:
        return 0
    bundled_node = ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'
    node = str(bundled_node) if bundled_node.is_file() else shutil.which('node')
    if not node:
        raise RuntimeError('Node.js is required to run the JavaScript tests')
    (ROOT / '.cache').mkdir(exist_ok=True)
    return subprocess.run([node, '--test', *map(str, tests)], cwd=ROOT).returncode


if __name__ == '__main__':
    sys.exit(main())
