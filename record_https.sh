#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

session_only=0
if [[ "${1:-}" == --session-only && "$#" == 1 ]]; then
    session_only=1
elif [[ "$#" != 0 ]]; then
    echo 'Usage: bash record_https.sh [--session-only]' >&2; exit 2
fi
[[ "$session_only" == 1 || -t 0 ]] || { echo 'Run in an interactive terminal.' >&2; exit 1; }
[[ -x .venv/bin/mitmdump && -f .mitmproxy/mitmproxy-ca-cert.pem ]] || {
    echo 'Run bash setup_https.sh first.' >&2; exit 1;
}
for key in mode autoconfig-url; do
    [[ "$(gsettings writable org.gnome.system.proxy "$key")" == true ]] || {
        echo 'The GNOME desktop proxy setting is not writable.' >&2; exit 1;
    }
done
if [[ "$session_only" == 0 ]]; then
    read -r -p 'Press Enter to start HTTPS recording (keep WeChat running): '
fi
mkdir -p captures
capture=$(mktemp -d "captures/https-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
old_mode=$(gsettings get org.gnome.system.proxy mode)
old_url=$(gsettings get org.gnome.system.proxy autoconfig-url)
{
    printf '#!/usr/bin/env bash\nset -e\n'
    printf 'gsettings set org.gnome.system.proxy autoconfig-url %q\n' "$old_url"
    printf 'gsettings set org.gnome.system.proxy mode %q\n' "$old_mode"
} >"$capture/restore-proxy.sh"

proxy_pid=''
pac_pid=''
proxy_changed=0
proxy_status=0
restore_failed=0
stop_process() {
    local target=$1 stop_signal=$2
    [[ -n "$target" ]] || return 0
    if kill -0 "$target" 2>/dev/null; then
        kill -"$stop_signal" "$target" 2>/dev/null || true
        for ((i=0; i<50; i++)); do
            kill -0 "$target" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 "$target" 2>/dev/null; then
            kill -KILL "$target" 2>/dev/null || true
            echo 'A capture service needed a forced stop; output may be incomplete.' >&2
        fi
    fi
    wait "$target"
}
cleanup() {
    if [[ "$proxy_changed" == 1 ]]; then
        if ! bash "$capture/restore-proxy.sh"; then
            echo "Proxy restoration failed. Run: bash $PWD/$capture/restore-proxy.sh" >&2
            restore_failed=1
        fi
        proxy_changed=0
    fi
    stop_process "$proxy_pid" TERM || proxy_status=$?
    proxy_pid=''
    stop_process "$pac_pid" TERM || true
    pac_pid=''
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

capture_args=()
if [[ "$session_only" == 0 ]]; then
    capture_args=(--set "hardump=$capture/flows.har" -w "$capture/flows.mitm")
fi
PYTHONUNBUFFERED=1 .venv/bin/mitmdump \
    --mode regular@127.0.0.1:18080 \
    --set "confdir=$PWD/.mitmproxy" \
    -s "$PWD/collect_session.py" \
    --allow-hosts '^(?:www|ltm)\.costco\.com\.cn:443$' \
    --set 'save_stream_filter=~d "^(www|ltm)[.]costco[.]com[.]cn$"' \
    --set flow_detail=0 \
    "${capture_args[@]}" >"$capture/proxy.log" 2>&1 &
proxy_pid=$!
python3 -u - "$capture/pac.port" >"$capture/pac.log" 2>&1 <<'PY' &
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

with ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory='proxy')) as server:
    Path(sys.argv[1]).write_text(str(server.server_port))
    print(f'Serving HTTP on 127.0.0.1 port {server.server_port}', flush=True)
    server.serve_forever()
PY
pac_pid=$!
echo "Starting capture services. Logs: $PWD/$capture"
for ((attempt=0; attempt<100; attempt++)); do
    if grep -q 'HTTP(S) proxy listening' "$capture/proxy.log" && [[ -s "$capture/pac.port" ]]; then
        break
    fi
    if ! kill -0 "$proxy_pid" 2>/dev/null || ! kill -0 "$pac_pid" 2>/dev/null; then
        break
    fi
    sleep 0.1
done
if ! kill -0 "$proxy_pid" 2>/dev/null || ! kill -0 "$pac_pid" 2>/dev/null ||
   ! grep -q 'HTTP(S) proxy listening' "$capture/proxy.log" || [[ ! -s "$capture/pac.port" ]]; then
    echo "Capture services failed to start. Check $capture/proxy.log and pac.log." >&2
    exit 1
fi
# Restore even if the second setting fails after the first has changed.
proxy_changed=1
pac_port=$(<"$capture/pac.port")
gsettings set org.gnome.system.proxy autoconfig-url "'http://127.0.0.1:$pac_port/proxy.pac'"
gsettings set org.gnome.system.proxy mode "'auto'"
echo 'CAPTURE READY. Keep WeChat running; open Costco开市客 → 会员码.'
echo 'If the page is already open, close and reopen only the mini program.'
if [[ "$session_only" == 1 ]]; then
    echo 'Live login collection is active. Ctrl+C stops and restores proxy settings.'
else
    echo 'Press q to stop, save, and restore the previous desktop proxy settings.'
fi
while kill -0 "$proxy_pid" 2>/dev/null && kill -0 "$pac_pid" 2>/dev/null; do
    if [[ "$session_only" == 1 ]]; then
        sleep 0.2
    else
        key=''
        if read -r -s -n 1 -t 0.2 key; then
            [[ "$key" == q || "$key" == Q ]] && break
        fi
    fi
done
cleanup
if [[ "$proxy_status" != 0 || "$restore_failed" != 0 ]]; then
    echo "Recording failed. Check $capture/proxy.log and the restoration message above." >&2
    exit 1
fi
if [[ "$session_only" == 0 && ! -s "$capture/flows.mitm" ]]; then
    echo "No Costco requests captured. Logs: $PWD/$capture" >&2
    echo 'WeChat may not be using the desktop proxy, or may have rejected its certificate.' >&2
    exit 1
fi
if [[ "$session_only" == 1 ]]; then
    echo 'Login collector stopped. Collected credentials remain in state/costco_session.json.'
else
    echo "Saved HTTPS capture: $PWD/$capture"
fi
