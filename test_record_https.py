import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).parent


class HTTPSRecorderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy(ROOT / 'record_https.sh', self.root)
        if (ROOT / 'proxy').exists():
            shutil.copytree(ROOT / 'proxy', self.root / 'proxy')
        (self.root / '.mitmproxy').mkdir()
        (self.root / '.mitmproxy/mitmproxy-ca-cert.pem').touch()
        self.bin = self.root / '.venv/bin'
        self.bin.mkdir(parents=True)
        self.initial = {'mode': "'auto'", 'autoconfig-url': "'http://original.test/proxy.pac?a=1&b=2'"}
        self.state = self.root / 'settings.json'
        self.state.write_text(json.dumps(self.initial))
        # Replace external services; exercise real shell, terminal IO, and signals.
        self.fake('gsettings', """
import json, os, pathlib, sys
p=pathlib.Path(os.environ['SETTINGS_STATE'])
state=json.loads(p.read_text()); args=sys.argv[1:]
if args[0]=='get': print(state[args[2]])
elif args[0]=='writable': print('true')
elif args[0]=='set':
 state[args[2]]=args[3]; p.write_text(json.dumps(state))
""")
        self.fake('python3', """
import os, pathlib, signal, sys, time
if os.environ.get('OLD_PAC_PORT_OCCUPIED') and '-m' in sys.argv:
 print('OSError: [Errno 98] Address already in use', flush=True)
 sys.exit(1)
if '-m' not in sys.argv:
 pathlib.Path(sys.argv[-1]).write_text('36123')
signal.signal(signal.SIGTERM, lambda *_: exit(0))
print('Serving HTTP on 127.0.0.1 port 36123', flush=True)
while True: time.sleep(0.1)
""")
        self.fake('mitmdump', """
import os, pathlib, signal, sys, time
if os.environ.get('PROXY_FAIL'): sys.exit(1)
args=sys.argv[1:]; out=pathlib.Path(args[args.index('-w')+1]) if '-w' in args else None
def stop(*_):
 if out is not None: out.write_bytes(b'flushed')
 sys.exit(0)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
print('HTTP(S) proxy listening at http://127.0.0.1:18080.')
while True: time.sleep(0.1)
""")
        self.process = None

    def fake(self, name, code):
        p = self.bin / name
        p.write_text(f'#!{sys.executable}\n' + code)
        p.chmod(0o755)

    def start(self, fail=False, pac_collision=False, session_only=False):
        self.master, slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
               'SETTINGS_STATE': str(self.state)}
        if fail:
            env['PROXY_FAIL'] = '1'
        if pac_collision:
            env['OLD_PAC_PORT_OCCUPIED'] = '1'
        self.process = subprocess.Popen(
            ['bash', str(self.root / 'record_https.sh')] + (['--session-only'] if session_only else []),
            stdin=slave, stdout=slave, stderr=slave, env=env,
        )
        os.close(slave)
        self.addCleanup(self.stop)
        if not session_only:
            self.read_until(b'Press Enter')
            self.assertFalse((self.root / 'captures').exists())
            self.assertEqual(json.loads(self.state.read_text()), self.initial)
            os.write(self.master, b'\n')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def read_until(self, expected):
        output = b''
        deadline = time.monotonic() + 5
        while expected not in output and time.monotonic() < deadline:
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    output += os.read(self.master, 65536)
                except OSError:
                    break
        self.assertIn(expected, output)
        return output

    def test_q_flushes_capture_and_restores_existing_proxy(self):
        self.start()
        self.read_until(b'Press q')
        active = json.loads(self.state.read_text())
        self.assertEqual(active['mode'], "'auto'")
        self.assertIn('127.0.0.1:36123', active['autoconfig-url'])
        os.write(self.master, b'q')
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(json.loads(self.state.read_text()), self.initial)
        capture, = self.root.glob('captures/https-*/flows.mitm')
        self.assertEqual(capture.read_bytes(), b'flushed')
        self.assertEqual(capture.parent.stat().st_mode & 0o777, 0o700)

    def test_interrupt_restores_existing_proxy(self):
        self.start()
        self.read_until(b'Press q')
        self.process.send_signal(signal.SIGINT)
        self.assertEqual(self.process.wait(timeout=5), 130)
        self.assertEqual(json.loads(self.state.read_text()), self.initial)

    def test_failed_startup_does_not_change_proxy(self):
        self.start(fail=True)
        self.assertNotEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(json.loads(self.state.read_text()), self.initial)

    def test_old_pac_port_collision_does_not_block_capture(self):
        self.start(pac_collision=True)
        self.read_until(b'Press q')
        self.assertIn('127.0.0.1:36123', json.loads(self.state.read_text())['autoconfig-url'])
        os.write(self.master, b'q')
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(json.loads(self.state.read_text()), self.initial)

    def test_session_only_mode_needs_no_enter_and_restores_proxy(self):
        self.start(session_only=True)
        self.read_until(b'Live login collection is active')
        self.assertIn('127.0.0.1:36123', json.loads(self.state.read_text())['autoconfig-url'])
        self.process.terminate()
        self.assertEqual(self.process.wait(timeout=5), 130)
        self.assertEqual(json.loads(self.state.read_text()), self.initial)
        self.assertEqual(list(self.root.glob('captures/https-*/flows.har')), [])
        self.assertEqual(list(self.root.glob('captures/https-*/flows.mitm')), [])
