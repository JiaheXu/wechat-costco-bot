#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
bridge="$PWD/.tools/WMPFDebugger"
node_binary=$(command -v node)
[[ -f "$bridge/node_modules/tsx/dist/cli.mjs" && -f "$bridge/src/index.ts" ]] || {
    echo 'Install WMPFDebugger dependencies first; see README.md.' >&2; exit 1;
}
[[ "$($node_binary -p 'Number(process.versions.node.split(".")[0])')" -ge 22 ]] || {
    echo 'WMPFDebugger requires Node.js 22 or newer.' >&2; exit 1;
}
# The upstream defaults expose debugging sockets on all interfaces.
python3 - "$bridge/src/index.ts" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
for port in ('debugPort', 'cdpPort'):
    old = 'new WebSocketServer({ port: options.' + port + ' })'
    new = 'new WebSocketServer({ host: "127.0.0.1", port: options.' + port + ' })'
    if text.count(old) == 1:
        text = text.replace(old, new)
    elif new not in text:
        raise SystemExit('Unknown debugger server configuration; refusing an unsafe bind.')
p.write_text(text)
PY
echo 'WeChat must be running. After the bridge attaches, close and reopen only the Costco mini program.'
echo 'Debugger ports 9421 and 62000 are restricted to localhost. Ctrl+C stops the bridge.'
echo 'sudo is required by this machine’s process-attachment policy.'
exec sudo "$node_binary" "$bridge/node_modules/tsx/dist/cli.mjs" "$bridge/src/index.ts"
