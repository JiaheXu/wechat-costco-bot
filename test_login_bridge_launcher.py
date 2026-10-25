import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class BridgeLauncherTest(unittest.TestCase):
    def test_launcher_limits_debug_ports_to_localhost_before_sudo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(Path(__file__).with_name('start_login_bridge.sh'), root)
            bridge = root / '.tools/WMPFDebugger'
            (bridge / 'src').mkdir(parents=True)
            (bridge / 'node_modules/tsx/dist').mkdir(parents=True)
            (bridge / 'node_modules/tsx/dist/cli.mjs').touch()
            source = bridge / 'src/index.ts'
            source.write_text('new WebSocketServer({ port: options.debugPort });\nnew WebSocketServer({ port: options.cdpPort });\n')
            tools = root / 'bin'
            tools.mkdir()
            for name, code in [('node', 'print(24)'), ('sudo', 'import json, pathlib, sys\npathlib.Path("invocation.json").write_text(json.dumps(sys.argv[1:]))')]:
                file = tools / name
                file.write_text(f'#!{sys.executable}\n{code}\n')
                file.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'start_login_bridge.sh')],
                                    env={**os.environ, 'PATH': f'{tools}:{os.environ["PATH"]}'},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(source.read_text().count('host: "127.0.0.1"'), 2)
            invocation = json.loads((root / 'invocation.json').read_text())
            self.assertEqual(invocation[0], str(tools / 'node'))
            self.assertEqual(invocation[-1], str(source))
