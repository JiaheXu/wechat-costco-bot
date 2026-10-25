#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ "$EUID" != 0 ]] || { echo 'Run as your desktop user, without sudo; this script prompts when needed.' >&2; exit 1; }
[[ -x .venv/bin/mitmdump ]] || { echo 'Install requirements.txt into .venv first; see README.md.' >&2; exit 1; }

mkdir -p .mitmproxy
chmod 700 .mitmproxy
.venv/bin/python -c 'from mitmproxy.certs import CertStore; CertStore.from_store(".mitmproxy", "mitmproxy", 2048)'
sudo -v
if ! command -v certutil >/dev/null; then
    sudo apt-get install -y libnss3-tools
fi
# Chromium-based mini program runtimes may use NSS rather than system trust.
mkdir -p "$HOME/.pki/nssdb"
if [[ ! -f "$HOME/.pki/nssdb/cert9.db" ]]; then
    certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password
fi
certutil -A -d "sql:$HOME/.pki/nssdb" -n costco-qr-capture \
    -t 'C,,' -i .mitmproxy/mitmproxy-ca-cert.pem
sudo install -m 644 .mitmproxy/mitmproxy-ca-cert.pem \
    /usr/local/share/ca-certificates/costco-qr-capture.crt
sudo update-ca-certificates
echo 'Certificate trust installed. Fully quit and reopen WeChat before recording.'
echo 'Start: bash record_https.sh'
echo 'Remove certificate trust afterward: bash remove_https_trust.sh'
