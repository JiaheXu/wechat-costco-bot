# Costco membership QR

Fetch a fresh QR code directly from Costco and save it in `images/`:

```bash
python3 fetch_qr.py
```

The script uses Python and Pillow (already installed on this machine); no proxy
or mitmproxy process is needed for direct fetching. Starting the login bridge
requires sudo if the login bridge needs starting. Each run creates a new Costco
login session and requests a fresh QR, then saves a uniquely named PNG under
`images/` beside the script. Image files have owner-only permissions and are
excluded from Git. A 10-pixel white border is added on all four sides without
resizing the QR: a 400×400 image becomes 420×420. Telegram sends this same padded
image. WeChat can stay running in the background.

Every QR request obtains a fresh WeChat login code and new Costco credentials,
saved privately in `state/costco_session.json`. Existing sessions or HAR captures
only supply reusable request headers; their authorization and cookies are
excluded from login requests. No capture is required. Failed login or QR calls
are reported without repeated attempts. The fetcher never prints the membership
number, cookies, or token.
The QR's expected three-minute validity comes from the mini program behavior;
the script does not independently verify its expiry or store acceptance.

## Automatic session collection and renewal

The proxy now observes successful responses from
`/api/em-bkgd/api/pub/v3/wx/user/login` and extracts `data.token`,
`data.sessionId`, and `data.memberInfo.cardNo`. Credentials are atomically saved
with owner-only permissions; failed logins do not replace them. Normal
recordings collect these values automatically. To keep collecting logins
without saving all traffic or pressing Enter, run:

```bash
bash record_https.sh --session-only
```

Keep WeChat running. A normal login when reopening the Costco mini program is
picked up immediately. Ctrl+C stops the collector and restores proxy settings.
This collector observes logins; it does not itself make WeChat log in.

The QR fetcher creates a new Costco session for every request, including the
server's startup check, without waiting for the previous session to expire.
It requests a fresh login code from your signed-in WeChat account using
`wx.login()`, or the native `WeixinJSBridge` on Linux page targets where `wx`
is unavailable. The native path attaches only to the exact Costco app path on
`https://servicewechat.com`. It exchanges the code for the Costco token,
session ID and membership number, and saves
the credentials privately in `state/costco_session.json`.
Costco may reuse `JSESSIONID` across logins; each request still obtains a fresh
authorization token before fetching its QR.

Start the QR server normally:

```bash
python3 qr_server.py
```

If login is needed and the bridge is unavailable, the code starts it for you.
Keep WeChat running and approve the sudo prompt in the terminal. After the
bridge reports attachment, close and reopen **only the Costco mini program**
so it connects to the debugger, then keep it open. If the startup check runs
before the mini program connects, send another QR request or restart the server.
Existing bridge processes are reused. A bridge started by this process is
stopped on normal exit when sudo authorization is still available; otherwise
a warning asks you to stop it manually.

Headless runs use cached sudo authorization and fail with an actionable error
instead of waiting for a password. For a separately managed bridge, run:

```bash
bash start_login_bridge.sh
```

WeChat must be running. Enter your local sudo password when prompted; this
machine's `ptrace_scope=1` policy requires privileged attachment. After the
bridge reports that it attached, close and reopen **only the Costco mini
program** once so it connects to the debugger. Keep it open in the background.
The QR server can then request a fresh `wx.login()` code in the context whose
app ID matches Costco, exchange it at Costco's login endpoint, and retry the
QR. The passive proxy collector is optional when using this bridge.

Restart the QR server to load these changes. Restart the Telegram monitor too;
its reply timeout is now 90 seconds to allow login renewal and the QR retry.
The Telegram and QR server remain the two application nodes; the debugger is
an additional local helper needed for unattended WeChat login.

This machine's WMPF runtime is **14910**, supported by the installed
[WMPFDebugger](https://github.com/evi0s/WMPFDebugger) source at commit
`9be0b2d0bd9875bd21a104a2279ad4304b46388b`. Its dependencies are installed in
the ignored `.tools/WMPFDebugger` directory. The launcher restricts both
debugging ports (`9421` and `62000`) to `127.0.0.1`. Node.js 22+ is required.
Live attachment, native page login, and QR retrieval have been verified with
this runtime and an already running bridge. Automatic privileged bridge startup
and cleanup still require verification in an interactive terminal.

If the bridge directory is missing on another checkout, install it as your
normal desktop user before running the launcher:

```bash
git clone https://github.com/evi0s/WMPFDebugger.git .tools/WMPFDebugger
git -C .tools/WMPFDebugger checkout 9be0b2d0bd9875bd21a104a2279ad4304b46388b
cd .tools/WMPFDebugger
npm install --no-audit --no-fund
```

The launcher applies the localhost binding restriction. Hooks are specific to
runtime versions; a WeChat update may require an updated debugger. Without a
working bridge, reopen the mini program while the collector runs to update
the session, then retry the Telegram request.

## Fetch every 10 seconds

```bash
python3 watch_qr.py
```

The watcher fetches immediately, then schedules successive attempts 10 seconds
apart without overlapping requests. It saves each successful padded image in
`images/`, logs the attempt number, duration, output path or safe error reason,
and keeps running after failures. Press **Ctrl+C** to stop.

Logs appear in the terminal and `logs/qr_watch.log`, rotating at 1 MB with three
backups. Credentials and raw server responses are not logged. The watcher creates
a new Costco session on every attempt using the same login bridge. Failures are
logged and the next scheduled attempt tries again. Saved images
accumulate until you remove them. This watcher does not send Telegram messages.

## Telegram Signal trigger

`~/crypto_policy/src/monitors/tele_monitor.py` now reads `tool_monitor_chats`
from `telegram_targets.yaml`. With `Signal` configured there, a message in the
exact channel **Signal** containing `costco` in any letter case fetches a new
QR and replies to that message with the image. Every sender is accepted,
including your own account. The handler runs before trading/sender filters and
performs the fetch in a worker thread so Telegram handling stays responsive.

Run these two processes:

1. Start the QR fetch server in this repository:

   ```bash
   python3 qr_server.py
   ```

2. Restart your existing Telegram monitor using its usual launcher, for example:

   ```bash
   bash ~/crypto_policy/src/scripts/tele.sh
   ```

Keep both processes running. Telegram sends a JSON UDP request to
`127.0.0.1:18888`; the QR server creates a new Costco session, fetches a fresh
image, saves the padded PNG in `images/`, and returns the image bytes encoded
in base64 with a matching request ID. Telegram uploads that image to Signal.
The Telegram process no longer imports this repository or reads its image and
session files. Costco credentials remain in the fetch server's process.

On startup, the QR server fetches and saves one fresh QR image and logs whether
the check succeeded. If it fails, the server logs a safe error and continues
listening so later requests can succeed after the session is refreshed.

The port can be changed with `COSTCO_QR_SERVER_PORT` in both processes. The server
binds only to loopback; this setup expects both nodes on the same Ubuntu host.
The client waits up to 90 seconds and reports missing-server or fetch errors.
Login and QR retrieval are serialized within the server so concurrent requests
cannot replace each other's credentials. Every request logs in and fetches again
without using a cached QR. The watcher is an optional diagnostic tool and does not need
to run alongside these two processes.

Server logs go to the terminal and `logs/qr_server.log`, rotating at 1 MB with
three backups. Known safe errors such as application code `501` are returned
to Telegram's logs; credentials and raw server response bodies are excluded.
UDP replies are capped at 60 KB; unusually large QR images return an error.

The request format is:

```json
{"type":"costco_qr","request_id":"0123456789abcdef0123456789abcdef"}
```

Success replies contain `request_id`, `ok: true`, and `image` (base64 PNG).
Failure replies contain `request_id`, `ok: false`, and a safe `error` string.
No Telegram messages are sent by the QR server itself.

QR replies use a caption without the trigger word, and repeated processing of
the same Telegram message is deduplicated. Fetch or upload failures produce a
generic text reply without exposing credentials. The monitor account needs
permission to post images in Signal. Session renewal needs the local login
bridge and an open Costco mini program, or a successful login observed by the
collector.

## Packet capture investigation

Record the traffic around opening **Costco开市客 → 首页 → 会员码** on
this Ubuntu desktop. The packet recorder requires Python 3 and `tcpdump`
(already installed on this machine).

```bash
sudo python3 record_traffic.py
```

1. Open the mini program and leave it on 首页.
2. Run the command in an interactive terminal. Press **Enter** to start.
3. Wait for tcpdump's **listening on** message. Open **会员码** in WeChat.
4. Once the QR appears, return to the terminal and press **q**. No Enter needed.
5. The script stops tcpdump, flushes its output, and prints the `.pcap` path.
   Ctrl+C also stops recording and saves the capture.

Files go into `captures/` in the current directory, with unique UTC timestamps.
Files are readable only by their owner. When run through sudo, ownership is
returned to the invoking user. Captures are ignored by Git.

Keep recordings short and close unrelated network apps where practical: this
records **all traffic on all interfaces**, not just WeChat. It does not change
proxy settings or install certificates. Captures can contain private data;
keep them local.

Read a capture without installing another tool:

```bash
tcpdump -nn -tttt -r captures/traffic-REPLACE-WITH-YOUR-FILE.pcap
```

The file can also be opened in Wireshark. It can help identify server IPs,
timing, DNS names, and connection patterns. HTTPS encrypts URLs, headers, tokens,
and bodies, so **this capture alone usually cannot reconstruct an API call**.
Depending on what we find, inspecting those contents will need a separate
application-level capture or an HTTPS proxy the client trusts.

Run the automated checks:

```bash
python3 -m unittest -v
```

Tests exercise terminal start/stop and subprocess cleanup using a fake tcpdump;
they do not require root or record live traffic.

## Decrypted HTTPS capture

Mitmproxy 12.3.0 is installed in `.venv/` using the existing Python 3.13.
To reproduce that installation, create a Python 3.13 virtual environment and
install `requirements.txt`, for example with the installed `uv`:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Run setup as your normal desktop user. It prompts for sudo, installs
`libnss3-tools` if missing, and trusts this project's unique capture CA in both
Ubuntu's system certificate store and your user's NSS database. This trust
applies to other applications using those stores too. The CA private key stays
in `.mitmproxy/`; keep that directory private and out of Git.

```bash
bash setup_https.sh
```

Start the recorder (WeChat can already be open):

```bash
bash record_https.sh
```

Keep WeChat running in the background. Press **Enter** and wait for
**CAPTURE READY**, then open the Costco mini program and **会员码**. If it is
already open, close and reopen only the mini program to trigger fresh requests.
Press **q** to save and stop. Ctrl+C also stops and restores proxy settings.

The recorder uses a regular HTTP proxy on `127.0.0.1:18080`, replacing the
unsuccessful local/eBPF capture mode. It temporarily sets GNOME's automatic
proxy configuration to a PAC file served on a free loopback port selected for
each recording (listed in the capture directory's `pac.port`). The rule sends
only `www.costco.com.cn` and `ltm.costco.com.cn` through the proxy; other hosts
go directly. This rule applies to desktop applications using system proxy
settings, including other apps accessing those Costco hosts. Any previous
desktop proxy configuration is temporarily replaced and restored on exit.
Recording requires no sudo and does not stop or relaunch WeChat.

This desktop has successfully captured the running WeChat mini program using
GNOME's live proxy changes. Applications that ignore system proxies and
previously established connections can bypass this capture. An empty capture
is reported as a failure, not a successful recording. Other Costco hosts are
outside this initial capture scope.

The previous desktop proxy settings are also saved as `restore-proxy.sh` in
each capture directory. If the terminal is forcibly killed or the machine
crashes, restore them with `bash captures/https-.../restore-proxy.sh`.

Each run saves `flows.mitm`, `flows.har`, and `proxy.log` under a private
`captures/https-.../` directory. The flow files include full requests, responses,
and potentially login tokens and personal data. Keep them local. An empty flow
file or TLS errors do not prove certificate pinning: check capture routing and
certificate trust first. Other API hosts, if used, are outside this initial scope.

Remove the added certificate trust after the investigation:

```bash
bash remove_https_trust.sh
```

Restart WeChat after removal. Stopping the recorder restores proxy settings;
certificate trust remains until explicitly removed.

Proxy setup references: [regular proxy](https://docs.mitmproxy.org/stable/concepts/modes/#regular-proxy),
[certificate trust](https://docs.mitmproxy.org/stable/concepts/certificates/).
