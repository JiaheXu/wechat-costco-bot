#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your desktop user, without sudo.' >&2; exit 1; }
if command -v certutil >/dev/null && [[ -f "$HOME/.pki/nssdb/cert9.db" ]]; then
    if certutil -L -d "sql:$HOME/.pki/nssdb" -n costco-qr-capture >/dev/null 2>&1; then
        certutil -D -d "sql:$HOME/.pki/nssdb" -n costco-qr-capture
    fi
fi
sudo rm -f /usr/local/share/ca-certificates/costco-qr-capture.crt
sudo update-ca-certificates
echo 'Capture certificate trust removed. Restart WeChat to apply.'
