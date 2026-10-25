#!/usr/bin/env python3
"""Record a short packet capture: Enter to start, q to stop."""

from datetime import datetime, timezone
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import tty


def main():
    if not sys.stdin.isatty():
        print("Run this script in an interactive terminal.", file=sys.stderr)
        return 1
    tcpdump = shutil.which("tcpdump")
    if tcpdump is None:
        print("tcpdump is missing. Install it with: sudo apt install tcpdump", file=sys.stderr)
        return 1

    print("Records ALL interfaces, including other apps. HTTPS contents stay encrypted.")
    input("Press Enter to start recording (Ctrl+C to cancel): ")

    directory = Path("captures")
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fd, name = tempfile.mkstemp(prefix=f"traffic-{stamp}-", suffix=".pcap", dir=directory)
    capture = Path(name)
    with os.fdopen(fd, "wb") as output:
        if os.geteuid() == 0 and "SUDO_UID" in os.environ:
            owner = (int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))
            os.chown(directory, *owner)
            os.fchown(output.fileno(), *owner)
        settings = termios.tcgetattr(sys.stdin)
        # Write through an inherited descriptor so tcpdump can drop privileges.
        process = subprocess.Popen(
            [tcpdump, "-i", "any", "-n", "-s", "0", "-U", "-w", "-"],
            stdin=subprocess.DEVNULL, stdout=output,
        )
        try:
            tty.setcbreak(sys.stdin.fileno())
            print(f"Starting capture: {capture.resolve()}", flush=True)
            print("Wait for tcpdump's 'listening on' message, then open 会员码.", flush=True)
            print("Press q to stop (no Enter needed); Ctrl+C also stops.", flush=True)
            while process.poll() is None:
                if select.select([sys.stdin], [], [], 0.2)[0]:
                    key = os.read(sys.stdin.fileno(), 1)
                    if key in (b"q", b"Q", b""):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
            if process.poll() is None:
                try:
                    process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                print("tcpdump did not stop cleanly; capture may be incomplete.", file=sys.stderr)
                return 1

    if process.returncode != 0 or capture.stat().st_size < 24:
        print("Capture failed. Check tcpdump's error above; try sudo python3 record_traffic.py.",
              file=sys.stderr)
        return 1
    print(f"Saved {capture.stat().st_size:,} bytes: {capture.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, EOFError) as error:
        print(f"Recorder error: {error}", file=sys.stderr)
        sys.exit(1)
