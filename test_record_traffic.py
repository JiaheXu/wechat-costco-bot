import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("record_traffic.py")


class RecorderTest(unittest.TestCase):
    def run_recorder(self, fail=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        fake = root / "tcpdump"
        # Only replace the privileged network boundary; use real terminal IO and signals.
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import signal, struct, sys, time\n"
            + ("sys.exit(1)\n" if fail else
               "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
               "sys.stdout.buffer.write(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))\n"
               "sys.stdout.buffer.flush()\n"
               "print('capture-ready', file=sys.stderr, flush=True)\n"
               "while True: time.sleep(0.1)\n")
        )
        fake.chmod(0o755)
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT)], cwd=root,
            stdin=slave, stdout=slave, stderr=slave,
            env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}"},
        )
        os.close(slave)
        self.addCleanup(self.stop_process, process)
        return root, master, process

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.kill()
        process.wait()

    def read_until(self, master, process, expected):
        output = b""
        deadline = time.monotonic() + 5
        while expected not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
            elif process.poll() is not None:
                break
        self.assertIn(expected, output, output.decode(errors="replace"))

    def test_enter_starts_and_q_stops_without_another_enter(self):
        root, master, process = self.run_recorder()
        self.read_until(master, process, b"Press Enter")
        self.assertFalse((root / "captures").exists())
        os.write(master, b"\n")
        self.read_until(master, process, b"capture-ready")
        os.write(master, b"q")
        self.assertEqual(process.wait(timeout=5), 0)
        capture, = (root / "captures").glob("*.pcap")
        self.assertEqual(capture.read_bytes(), struct.pack(
            "<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
        self.assertEqual(capture.stat().st_mode & 0o777, 0o600)

    def test_tcpdump_failure_exits_without_waiting_for_q(self):
        _, master, process = self.run_recorder(fail=True)
        self.read_until(master, process, b"Press Enter")
        os.write(master, b"\n")
        self.assertNotEqual(process.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
