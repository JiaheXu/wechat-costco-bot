# Automatic Costco login implementation plan

**Goal:** Use the existing signed-in WeChat account to create a new Costco session for every QR request, including startup.

**Design:** Reuse the local WMPF bridge and login exchange. Start the bridge on demand, obtaining sudo through the terminal when needed. Bootstrap without a HAR capture; save validated credentials with the existing private atomic storage. Keep WeChat sign-in and opening the mini program under the user's control. Do not log credentials or change system attachment policy.

**Scope:** `refresh_session.py`, `fetch_qr.py`, their tests, and `README.md`. Existing UDP clients and response format stay compatible. No dependencies added.

- [x] Add tests for session bootstrap without captures, login headers without stale credentials, bridge startup/reuse, unavailable sudo, and startup failure.
- [x] Run tests to establish failures, then implement the smallest shared bootstrap and bridge lifecycle changes. User steering: always log in before QR download; hold the renewal lock across both operations.
- [x] Run the suite, check the diff, and document startup prerequisites and any live verification limits.

**Verification:** `python3 -m unittest discover`: 51 tests passed with local UDP access. `git diff --check`: passed. Focused reviewer found no blocking bridge/login security issues. After the user started the bridge, live diagnosis showed that the Linux page target exposes `WeixinJSBridge` without `wx`. Added an exact-host/app-path native login fallback and tested spoofed-target rejection and cleanup. A live CLI fetch and two consecutive live UDP requests succeeded; each UDP request returned a matching request ID, valid PNG, and new auth token. Costco reused JSESSIONID across fresh logins. Automatic privileged bridge startup and cleanup remain unverified interactively.

**Review focus:** Concurrent requests must not start multiple bridges or replace credentials during a QR fetch; existing bridges must not be stopped by this process; a failed login must not overwrite saved credentials; subprocess errors must not reveal secrets; headless startup must not hang on sudo.
