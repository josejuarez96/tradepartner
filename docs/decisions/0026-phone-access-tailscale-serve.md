# 0026. Phone access: Tailscale Serve to a read-only phone instance of the Streamlit dashboard

**Status:** Proposed  ·  **Date:** 2026-10-27  ·  **Issue:** #2389  ·  **Answers:** [ADR 0018](0018-end-user-web-app.md)'s 2026-10-10 amendment, part D, open question 5 ("How does the phone reach the app?")  ·  **Amends:** [ADR 0011](0011-dashboard-technology.md) points 2 and 3, its Consequences and its Revisit-if (the [amendment at its end](0011-dashboard-technology.md#amendment-2026-10-27-a-read-only-phone-instance-behind-tailscale-serve-adr-0026))  ·  **Related:** [charter](../charter.md) (Interface, Constraints), [ADR 0018](0018-end-user-web-app.md) sections 2 and 4 (the withdrawn Tailscale rules this record restates), [paper plan](../plans/paper-trading.md) T277 (the Stop button), T278 (the phone layout, in PR #2385), T289 to T296 (the Lab page)

**Numbering note.** 0019 stays reserved for the Phase 6 ADR (ADR 0018's numbering note); 0025 is taken on `main` by reconcile-by-breaks. This record takes 0026, the next free number.

## Context

In plain words: the owner wants to look at his books from his phone. On 2026-10-27, asked "how should your phone reach the app?" with four options (1) Tailscale, (2) a self-run WireGuard VPN, (3) a Cloudflare tunnel with sign-in, (4) no phone access, he answered **1, Tailscale**. ADR 0018's 2026-10-10 amendment requires the path to be decided in its own record, with its security boundary and tests, **before any listener but loopback exists**. This is that record. It enables nothing: no listener, no Serve configuration and no code change comes with it.

Three facts shape what the phone can reach.

- **What exists is Streamlit, not the web app.** ADR 0018's FastAPI and React app is still Proposed and unbuilt. The owner chose Streamlit "now" for the daily screens (#2006, #2096, the 2026-10-26 Home and Book amendment #2170) and said Streamlit stays unless a plan argues otherwise. The Home and Books pages are on it and the Lab page is being built on it. T278 (a bottom nav at 640 px or less and card spacing at 390 px) is in review in PR #2385, not merged; its plan line was parked until the owner chose the phone path, which this record records.
- **That Streamlit now writes.** Besides the override form (ADR 0011 point 2), the Book page has "Stop this book now" (T277, `dashboard/book_actions.kill`, which runs `paper kill` as a child process); the Lab modules (T289 to T296) run `sweep run` and `backtest` children (`dashboard/lab_child.spawn`), will register sweeps and hypotheses, and keep a run slot in files (`dashboard/lab_run.take`, `record`, `release`, `abandon`). The charter's Interface line was amended on 2026-10-27 (#2304) to say so.
- **ADR 0011 closed the network for a reason.** Point 3 binds Streamlit to `localhost` because Streamlit listens on every interface by default and has no authentication, so "an `exclude_name` from another host on the LAN would lead to a sell". Its Consequences accept one more risk while only a paper account exists: DNS rebinding against Streamlit's websocket from a hostile page in the owner's own browser. ADR 0018's Options item 1 rejected a phone path to Streamlit for the same reason ("no identity check on it").

The question this record answers besides the path: **may the current Streamlit dashboard be reached from the phone now, or does phone access wait for the web app?**

## Options considered

The path (the owner's choice, recorded with what it means):

1. **Tailscale Serve (chosen by the owner).** `tailscaled` on the Mac terminates HTTPS with a tailnet certificate and proxies to a loopback port. Only devices signed in to the owner's tailnet can reach it. For a request from a tailnet user it adds `Tailscale-User-Login`, `Tailscale-User-Name` and `Tailscale-User-Profile-Pic`, and it removes any copies the client sent ("to avoid header spoofing"); a tagged device's request carries none ([Tailscale docs, kb 1312](https://tailscale.com/kb/1312/serve)). Serve requires HTTPS certificates to be enabled for the tailnet. With `--bg` it persists across reboots until turned off ([kb 1242](https://tailscale.com/kb/1242/tailscale-serve)). Pro: the Mac's processes stay on loopback; no port opens on the LAN or the internet; the tailnet proves the device and the user; nothing secret lives in the phone's browser; a lost phone is a device removed in the admin console. Con: a third party (Tailscale's coordination server and the owner's sign-in provider) decides who is on the tailnet; enabling HTTPS certificates puts the Mac's tailnet name in the public certificate-transparency logs (a name, not a way in); Serve proxies to a **port**, not to a process, so whatever listens on that port is what the phone reaches (Decision 2).
2. A self-run WireGuard VPN: no third party, but a UDP port forwarded on the home router, keys to rotate by hand, and no identity header (only "this device has the key"). Not chosen.
3. A Cloudflare tunnel with sign-in: an internet-facing hostname whose only gate is Cloudflare Access's policy, so a policy mistake is public. Not chosen.
4. No phone access: nothing to secure; the morning check stays at the desk. Not chosen.

**Funnel**, Tailscale's public variant of Serve, is never used and is removed from the tailnet's policy (Decision 1).

What the phone reaches (the open call):

- **(A) The current Streamlit dashboard, through a second, read-only Streamlit process on its own loopback port that only Serve proxies to, with the owner's tailnet login required on it (recommended).** Pro: the phone gets the screens the owner already uses, now; no second toolchain; the same pure readers draw the same numbers. Con, weighed against ADR 0011's reasons below: Streamlit has no request-level authentication, so the identity check is application code in the shell (`dashboard/app.render_app`), run on every script run from the headers of the websocket's opening request (`st.context.headers`), not by a server on every HTTP request; Streamlit's data-free endpoints answer before that check (Decision 4); a write on the phone would rest on that check, so this option excludes every write.
- **(B) Wait for the ADR 0018 web app.** Pro: a server built for it, with the identity rule enforced on every request before any handler and two listeners by design (ADR 0018's withdrawn section 4). Con: the app is Proposed with no plan task claimed and needs a spike, an API, a second toolchain and page-by-page parity first, so the phone waits months for screens Streamlit already draws at phone width.
- **(C) Serve the existing desk Streamlit process itself** (one process for the Mac and the phone). Rejected: the desk process carries every write, so a phone session on it would rest every write on the per-session check that (A) refuses to rest any write on.

**ADR 0011's reasons, weighed honestly against (A).**

1. *"An `exclude_name` from another host on the LAN would lead to a sell with no authentication."* Under (A) the LAN still reaches nothing: both processes stay bound to loopback and `render_app`'s refusal of any other address stands. Only tailnet devices reach the phone instance; it requires the owner's login, its one tailnet `Host` and its own `Origin`; and it has **no write of any kind** (Decision 6), so a session that passed the checks could not reach the override, a kill, a run, a registration or the Lab slot. This reason is answered, not set aside.
2. *DNS rebinding against the websocket, accepted for the desk.* Both instances gain a server-level `server.allowedHosts` list and an app-level `Host` and `Origin` check (Decisions 3 and 5), so a rebound name and a cross-site page are refused on both. The desk's accepted risk is closed for every Streamlit app the repo launches.
3. *"No client other than a browser on the same machine."* This sentence changes, for the phone instance only (the 0011 amendment). The charter's "runs locally, for personal use only" holds: every process, the store and every key stay on the Mac; the phone is a viewer of one person's system.
4. *Streamlit has no identity check (ADR 0018, Options item 1).* True of Streamlit's server. The check (A) adds runs in the shell before any widget and any store connection, which is where every page's data comes from (ADR 0011 point 1: one read-only connection per render, opened by the shell). What it cannot cover is listed in Decision 4 and accepted there. It is not strong enough to carry a write, which is why (A) has none.

**Recommendation: (A).** The owner's daily use is reading; the reasons ADR 0011 closed the network are about writes and unauthenticated reach, and (A) removes the writes and requires identity. (B) stays the path for writes from the phone.

## Decision

We will let the owner's phone reach a **read-only phone instance** of the Streamlit dashboard through **Tailscale Serve** (never Funnel), proxying HTTPS on the tailnet to a loopback port that only that instance listens on, with the owner's tailnet login, the instance's tailnet `Host` and its own `Origin` required on every script run, and no write reachable from it. The desk instance keeps its writes and gains the `Host`, `Origin` and proxied-header refusals. Nothing is enabled by this record; the code and the runbook are plan tasks (Next), and Serve is turned on by the owner after the verification sitting passes (Decision 8).

### 1. The network path and the tailnet

- Serve proxies `https://<the Mac's MagicDNS name>` (port 443) to `http://127.0.0.1:<dashboard.phone_port>` and to nothing else. It never proxies to the desk port.
- **Funnel is made impossible, not only avoided:** the owner removes the `funnel` node attribute from the tailnet policy file (new tailnets grant it to members by default), so no device on the tailnet can turn Funnel on. The runbook also says `tailscale funnel` is never run. A Funnel request would carry no identity header and be refused by Decision 3 in any case.
- Both Streamlit processes stay bound to loopback: `render_app`'s existing refusal (ADR 0011 point 3: `server.address` must be `localhost` or `127.0.0.1`, usage stats off) applies to both, unchanged. No process binds the LAN or the Mac's tailnet address.
- The tailnet holds only the owner's own devices: one user, no shared-in nodes, no tagged devices. Its sign-in is the owner's identity-provider account, with two-factor sign-in on. Device key expiry stays on (Tailscale's default). The verification sitting checks each of these on the admin console (Decision 8, item 6).

### 2. The phone instance and its port

- It is started by `tradepartner dashboard --phone`, which runs the same `dashboard/app.py` with `--server.address localhost`, `--server.port <dashboard.phone_port>`, `--server.allowedHosts <dashboard.phone_host>`, `--browser.gatherUsageStats false`, `--client.showErrorDetails none`, `--client.toolbarMode viewer`, `--server.headless true`, and the environment variable `TRADEPARTNER_DASHBOARD_ROLE=phone`. Plain `tradepartner dashboard` stays the desk instance.
- **The role is phone if any signal says so** (fail closed): `TRADEPARTNER_DASHBOARD_ROLE` is set to any non-empty value; or the running `server.port` equals `dashboard.phone_port`; or `dashboard.phone_port` is set and the running port cannot be read. A desk launch on the phone port by mistake is a phone instance, and so is a phone launch on another port. The role is one function, `dashboard.app.dashboard_role(settings) -> "desk" | "phone"`, which reads `os.environ` and `st.get_option("server.port")` and works outside a script run (in a callback, or in a test), and every check and guard below calls it.
- **The port cannot be taken by another Streamlit app by accident.** Streamlit picks the next free port, up to 100 above the requested one, when the port was not set by hand, so with the desk on 8501 the labeling review page (`research/labeling/review_page.py`, launched by `cli.py`, which writes gold labels and has no identity check) could land on 8502. So: `dashboard.phone_port` must lie outside 8501 to 8601, refused at settings load otherwise (proposed value 8700); every Streamlit launch in `cli.py` (the desk, the review page, the phone) passes an explicit `--server.port` so none can drift; and the review page gets the desk refusals of Decision 5. Any other local process listening on the phone port is inside the charter's trust boundary (a process on the Mac) but would be unauthenticated to the phone: the runbook turns Serve off whenever the phone instance is not running, and the sitting checks which process holds the port (item 4). That residual risk is accepted in so many words.
- `render_app` refuses to render the phone instance unless these running options hold, each pinned by a test: `client.showErrorDetails` `none`, `client.toolbarMode` `viewer`, `server.allowedHosts` exactly `[dashboard.phone_host]`, `server.enableCORS` true, `server.corsAllowedOrigins` empty, `server.enableXsrfProtection` true, `server.scriptHealthCheckEnabled` false, `server.enableStaticServing` false, `server.trustedUserHeaders` empty, `server.unsafeMetricsUserAttributes` empty, `server.baseUrlPath` empty, `global.developmentMode` false, and no `[auth]` section configured.
- `dashboard.phone_port`, `dashboard.phone_owner_login` and `dashboard.phone_host` are unset by default and set only in `.env` (Decision 7). **`tradepartner dashboard --phone` refuses to start when any of the three is unset**, so nothing listens for Serve until the owner sets them.

### 3. Identity, Host and Origin, checked first on every phone-instance run

The checks are one pure function beside `_server_options_ok`: `phone_gate(headers: list[tuple[str, str]], settings: Settings) -> str | None` (a refusal reason for the log, or `None`). The shell reads the headers through one seam, `_request_headers()`, which returns every header of the websocket's opening request as name and value pairs (`st.context.headers` with `get_all`, never `__getitem__`, whose first-or-last behaviour is not what "exactly once" needs). In the phone role, `render_app` calls `phone_gate` **immediately after `_server_options_ok`, before `st.navigation` and before any widget or store connection**, on every script run. A refused run draws one fixed sentence that names no value ("This page is not available here."), registers no widget (so no callback can fire on a later message) and opens no connection. It refuses unless all of these hold:

- `dashboard.phone_owner_login` is set; **unset is a refusal of every session** (fail closed, never "check it if set").
- `Tailscale-User-Login` appears **exactly once** and equals `dashboard.phone_owner_login` exactly (case-sensitive, no trimming). Missing, empty, repeated or different is a refusal. A tagged device or a Funnel request has no such header and is refused.
- `Host` appears exactly once and equals `dashboard.phone_host` exactly (the full MagicDNS name, no port). An unset `dashboard.phone_host` is a refusal. Streamlit's `server.allowedHosts` (Decision 2) refuses a foreign `Host` at the websocket handshake, before any session exists; this is the second layer, for a misconfigured option.
- `Origin` appears exactly once and equals `https://<dashboard.phone_host>` exactly; missing is a refusal (a browser always sends `Origin` on a websocket). This is the check against **cross-site websocket hijacking**: a hostile page open in the phone's browser could open `wss://<the Mac's name>/_stcore/stream` itself, with the right `Host` and, because the request comes from the owner's device, the owner's `Tailscale-User-Login`; websockets are not bound by the same-origin policy, and Streamlit's own `Origin` check runs only while `server.enableCORS` is true, which Decision 2 pins and this check backs up. ADR 0018's withdrawn section 4 covered the same threat with `Sec-Fetch-Site`; an exact `Origin` is the equivalent for a websocket.

The rule rests on `tailscaled` setting the identity header on what it proxies, websocket upgrades included, and removing any copy a client sent, and on which `Host` and `Origin` arrive. That is what the docs say; the verification sitting proves it on the owner's devices before Serve stays on (Decision 8). If the header does not reach the websocket request, the phone instance refuses everything: it fails closed, and this record is revisited. Pages use no `st.fragment` in the phone role (a fragment rerun skips the shell), pinned by a test over `dashboard/`; a page that needs one repeats `phone_gate` inside it.

The log line for a run records only booleans (`login_matched`, `host_matched`, `origin_matched`, the header counts), never a value. Streamlit itself logs a refused websocket's `Host` at WARNING in the Mac's local log; that is local and accepted.

### 4. What the check does not cover (accepted)

Streamlit 1.64 answers these before the shell runs, on the phone instance as on the desk: the built front-end assets; `/_stcore/health`; `/_stcore/host-config`; `/_stcore/metrics` (process counters; `server.unsafeMetricsUserAttributes` is pinned empty, so no login is published there); `/_stcore/upload_file` (no page has a file uploader); `/media/...` (files a session produced, under unguessable names); `/component/...` and the bidi-component routes (component files; no page in the phone role uses a custom component); the websocket `/_stcore/stream` itself, whose sessions the shell gates. `/_stcore/script-health-check`, `/app/static/...` and the auth routes are pinned off (Decision 2). None reads the store. A test pins the route set Streamlit registers for the phone instance's options, so an upgrade that adds a route fails it and is reviewed. These are reachable only from the owner's tailnet devices; accepted while that holds; revisit if a page adds an uploader, a download of store data, `components.html` or a custom component.

Also accepted: any process on the Mac can connect to the phone port with a forged `Tailscale-User-Login`, `Host` and `Origin` and pass the checks. That process could already read the store; the Mac is the charter's trust boundary.

### 5. The desk instance (and every other Streamlit app the repo launches)

- `server.allowedHosts` is `localhost` and `127.0.0.1`, set in `.streamlit/config.toml` and passed in the launch argv of the desk and the review page; `render_app` (and the review page's `server_options_ok`) refuses to render when the running value differs.
- In the desk role, immediately after `_server_options_ok` and before any widget, the shell refuses a run unless `Host` is `localhost` or `127.0.0.1` (with or without its port) and `Origin` is `http://localhost:<port>` or `http://127.0.0.1:<port>`, each present exactly once. Together with the server list, this **closes ADR 0011's accepted DNS-rebinding risk** and the cross-site websocket path to the desk's writes.
- It refuses any run whose opening request carries a header of the `Tailscale-User-*` or `X-Forwarded-*` families: no browser on the Mac sends one, and a request that has one was proxied, so Serve pointed at the desk port by mistake is refused. This is defence in depth beside Decision 1, not a replacement for it.
- The review page (`research/labeling/review_page.py`) gets the same refusals. Everything else on the desk (its writes, its pages) is unchanged.

### 6. No write from the phone

**A write** is any of: starting a child process that runs a `tradepartner` command; opening a store write connection or calling a store writer (`override` included); creating, replacing or unlinking a file. The read-only child processes the Lab's read views need are an explicit allow-list, each with its argv pinned by a test: `lab_files._git` (`git` reads of the checkout) and `lab_run.process_start_time` (`ps`). Nothing else is exempt.

- The phone instance's navigation omits the Override page and the Lab page's Run, Register and other actions (the Lab's read views stay, once the Lab page exists); the Book page draws its safety card without the "Stop this book now" button and says, in one line, that a book is stopped from the Mac (`tradepartner paper kill --book <book> --reason "..."`). Buttons that start a long read (the Data health page's "Run coverage check") are left out of the phone role too, so a phone tap cannot hold a read connection a run's write must wait behind.
- **Each write function refuses when `dashboard_role` is phone, inside the function itself**: `book_actions.kill`, `override_page.submit`, `lab_child.spawn`, `lab_run.take`, `record`, `release`, `abandon` and `_write_atomic`, the Lab's register spawn when it exists, and every later write the dashboard gains. The check lives in the function, not only in the page body, because Streamlit runs an `on_click` callback **before** the script body (ADR 0011 point 2). A refused write starts no child and writes nothing.
- An AST test over `dashboard/` checks exactly this definition: every function that calls `subprocess.Popen` or `subprocess.run` (outside the allow-list), a store writer or `open_for_write`, or a file create, replace or unlink calls `dashboard_role` and refuses in the phone role first.
- A later write from the phone (the Stop button is the first candidate; open question 1) needs an amendment to this record with a `safety-reviewer` pass, and `phone_gate` is re-run inside its callback.

### 7. No secret served

- `dashboard.phone_owner_login` and `dashboard.phone_host` are `SecretStr` and set **only in `.env`** (`DASHBOARD__PHONE_OWNER_LOGIN`, `DASHBOARD__PHONE_HOST`, with `DASHBOARD__PHONE_PORT` beside them; the repo is public). `config.secret_values` today walks only top-level `Settings` fields and the book pairs; the code task makes it walk the `dashboard` section too (a test: a sentinel in either key is in `secret_values`), so every scrubber that uses it (`cli_record._configured_secrets`, `book_actions._scrub`, `lab_child.scrub`, `lab_files.scrub`) covers both. `.env.example` lists the three with empty values. No refusal, page or log line echoes them or any `Tailscale-*` value.
- In the phone role, every string a page renders from an exception or a lower layer (the store-unreadable and store-busy panels in `app.py`, the backtest, performance, operations and override pages' error lines) passes `cli_record.scrub_text` with `_configured_secrets(settings)`, and the unavailable panels omit the store path and the process holding the lock. `client.showErrorDetails none` covers uncaught exceptions only; this covers the rest.
- The phone instance has no page or widget that shows `Settings`, an environment variable or a key. The phone process loads `Settings` like every command does (the paper keys included) but never constructs a broker; nothing on its pages reads a key.
- Telemetry stays off on both instances (ADR 0011 point 3).
- The phone's browser holds no token, key or password. A lost phone is removed from the tailnet in the admin console; nothing in `.env` rotates.

### 8. Tests and the verification sitting

**Tests** (the code tasks, `tests/dashboard/` and `tests/test_config.py`, headless, no network). The gate and role functions are pure and tested directly; the shell is tested through `AppTest` with `_request_headers` and `dashboard_role` monkeypatched, since `AppTest` cannot set request headers. Those tests prove the logic, not Streamlit's wiring; sitting item 1 proves the wiring.

- `phone_gate`: login missing, empty, repeated or different; `Host` missing, repeated or different; `Origin` missing, repeated, `http://` or a foreign host; either key unset: each refuses. The matching set passes.
- The shell in the phone role: a refused run opens no store connection (a counting fake around `open_store_connection`), registers no widget and draws only the fixed sentence; a passing run renders Home.
- `dashboard_role`: the environment flag alone (any non-empty value), the phone port alone, and an unreadable port with `phone_port` set each make the phone role; neither makes the desk role.
- Settings: `phone_port` inside 8501 to 8601 refuses at load; `--phone` with any of the three keys unset refuses to start; every Streamlit argv in `cli.py` carries `--server.port` and `--server.allowedHosts`.
- Options: each running option of Decision 2 that differs refuses the phone instance; `server.allowedHosts` other than `localhost`, `127.0.0.1` refuses the desk and the review page.
- Routes: the set Streamlit registers under the phone instance's options equals Decision 4's list.
- No write: the phone navigation has no Override page and no Lab action; the Book page in the phone role draws no Stop button; each guarded function in Decision 6, called directly in the phone role, starts no child (a fake `subprocess.Popen` that fails the test if called) and writes nothing (store bytes, row counts and the Lab slot files unchanged); the AST test of Decision 6; no `st.fragment` in `dashboard/`.
- Desk role: a foreign `Host`, a foreign or missing `Origin`, and a header from each of the `Tailscale-User-*` and `X-Forwarded-*` families (parametrized over at least `Tailscale-User-Login`, `Tailscale-User-Name`, `X-Forwarded-For`, `X-Forwarded-Host`) each refuse before any widget; `localhost:<port>` and `127.0.0.1:<port>` with matching `Origin` render. The same for the review page.
- No secret: with sentinel values for the two keys and the paper keys, and a store-unreadable fixture whose exception text carries a sentinel, no refusal text, rendered element or captured log line in the phone role contains a sentinel, the store path or a `Tailscale-*` value.

**The verification sitting** (the owner with a builder, about 30 minutes, after the code merges and before Serve is left on). It must show, in a comment on the code task's issue **with every login, tailnet name, MagicDNS name, IP and account id redacted** (the repo is public):

1. From the phone on the tailnet, the phone instance renders Home, and the run's log line shows `login_matched`, `host_matched` and `origin_matched` true: the identity header reached the websocket's opening request.
2. With `DASHBOARD__PHONE_OWNER_LOGIN` changed to another value and the phone instance restarted, the phone sees only the refusal sentence. Restored afterwards.
3. With Tailscale switched off on the phone (mobile data, or the same Wi-Fi), neither the tailnet name nor the Mac's LAN address and port reaches anything.
4. `tailscale serve status` shows exactly one HTTPS handler, to `127.0.0.1:<dashboard.phone_port>`; `lsof -nP -iTCP:<dashboard.phone_port> -sTCP:LISTEN` shows the phone instance's process and no other.
5. The tailnet policy file has no `funnel` node attribute, and `tailscale funnel status` shows no public access.
6. The admin console shows one user, only the owner's devices, no shared-in or tagged devices, key expiry on; the identity provider shows two-factor sign-in on.
7. The phone instance shows no Override page, no Stop button, no Lab Run or Register and no "Run coverage check".

If item 1 fails (the header does not reach the websocket request), Serve is turned off and this record is revisited; nothing is relaxed to make it pass.

## Owner steps (after the code tasks merge; nothing in this PR turns any of it on)

1. **Mac:** install Tailscale, the standalone download from tailscale.com (it carries the `tailscale` command; the Mac App Store build is not covered by the sitting). Sign in with the account that will own the tailnet, and make sure that account has two-factor sign-in on.
2. **Phone:** install the Tailscale app, sign in with **the same account**, and allow its VPN profile.
3. **Admin console** (login.tailscale.com): on the Machines page, rename the Mac to something neutral **first**, since its name becomes public in certificate logs once certificates are on. Then on the DNS page, turn MagicDNS on and enable **HTTPS Certificates**. On the Access controls page, delete the `funnel` entry from `nodeAttrs` and save.
4. **`.env`** on the Mac:
   - `DASHBOARD__PHONE_OWNER_LOGIN`: your login, as the admin console's Users page shows it.
   - `DASHBOARD__PHONE_HOST`: the Mac's full MagicDNS name, the `Self.DNSName` value in `tailscale status --json` without its trailing dot.
   - `DASHBOARD__PHONE_PORT=8700`.
5. **Start the phone instance:** `uv run tradepartner dashboard --phone` (leave it running; the desk dashboard is separate).
6. **Turn Serve on**, once:
   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8700
   tailscale serve status        # one handler, to 127.0.0.1:8700
   tailscale funnel status       # must show no public access
   ```
   Then open `https://<the Mac's MagicDNS name>` on the phone. `--bg` keeps Serve on across reboots, so the phone instance must be running whenever Serve is on.
7. **Turn it off** whenever the phone instance is not running, and at any other time: `tailscale serve --https=443 off` (or `tailscale serve reset` to clear every Serve setting). **Never run `tailscale funnel`.**
8. **Lost phone:** remove the device on the admin console's Machines page. Nothing in `.env` changes.

## Consequences

- Good: the phone gets the screens the owner already uses, now rather than after the web app (with T278's layout once PR #2385 merges); the LAN still reaches nothing; the phone instance has no write path at all, so the order path, the kill switch, the registry, the holdout and the Lab slot are exactly as reachable as today; the desk's accepted DNS-rebinding risk (ADR 0011) and the cross-site websocket path to its writes are closed for every Streamlit app the repo launches; the identity rule fails closed on every unset key and missing header; Funnel is off by policy; no secret is added to the phone.
- Bad / accepted risks: the identity check is application code run on every script run from the websocket's opening headers, not a server rule on every HTTP request; Streamlit's data-free endpoints answer before it (Decision 4); a local process on the phone port is unauthenticated to the phone (Decision 2), handled by turning Serve off with the instance and by the sitting; a third party (Tailscale and the owner's sign-in provider) decides tailnet membership, so a compromise of that account reaches the read-only pages; the Mac's tailnet name is in public certificate logs; the phone instance is a second Streamlit process whose reads hold read connections like the desk's, so `paper run`'s writes wait behind phone renders as they do behind desk renders (bounded by the existing short-hold rule, #2178); the Mac must be awake and the phone instance running for the phone to see anything; no stop from the phone in this record (open question 1).
- Reversibility: cheap. `tailscale serve --https=443 off` closes the path in one command; the code is a role function, one gate, a few option checks and the write guards; the web app, when built, takes these same rules per request instead of the Streamlit ones.
- Revisit if: item 1 of the verification sitting fails, or a Tailscale release stops setting or stripping the identity header on proxied websocket requests; the owner wants any write from the phone (an amendment with `safety-reviewer`, open question 1); a second person, a shared node or a tagged device joins the tailnet; a page adds a file uploader, a download of store data, `components.html` or a custom component (Decision 4); a Streamlit upgrade changes the route set or the websocket's `Host`, `Origin` or header handling; the ADR 0018 web app replaces the Streamlit pages (its tailnet listener then follows Decisions 1, 3 and 7 on every request, and its desk listener Decision 5); Phase 6's live account starts (the Phase 6 ADR re-reviews this path before live keys exist on the Mac); or Tailscale's terms or free personal plan change.

## Open questions for the owner (one decision each, a recommendation beside it)

1. **May the phone stop a book ("Stop this book now")?** **Recommend not yet.** Stopping is the one write that makes things safer, and the most useful from a phone, but it would rest on the per-run check of Decision 3. Recommend a one-paragraph amendment after the verification sitting passes and the phone has been used read-only for two weeks, with `phone_gate` re-run inside the callback and a `safety-reviewer` pass. Until then a book is stopped at the Mac.

## Next

After the owner accepts this record (he merges it; class B), a plan amendment to the paper plan:

- **Rewrites T278's line** (no longer parked: the phone path is chosen) and orders the code tasks **after T278 (PR #2385) and after T294** (the Lab page's nav entry), because all three edit `dashboard/app.py`.
- **Code task 1, the role and the write guards** (size M, class A): `dashboard/app.py` (`dashboard_role` only), `dashboard/book_actions.py`, `dashboard/override_page.py`, `dashboard/lab_child.py`, `dashboard/lab_run.py`, `dashboard/lab_files.py` (allow-list), the Lab page module once it exists, and their tests, including the AST test. Review: `safety-reviewer`, two passes.
- **Code task 2, the phone shell** (size M, class B for `config.py`, `.env.example` and `.streamlit/config.toml`): `cli.py` (`dashboard --phone`, an explicit `--server.port` and `--server.allowedHosts` on every Streamlit launch), `config.py` (the three keys, the port range, `secret_values` over `dashboard`), `.env.example`, `.streamlit/config.toml` (`allowedHosts`), `dashboard/app.py` (`phone_gate`, `_request_headers`, the option checks, the desk refusals, the phone navigation, scrubbed panels), `dashboard/book_page.py` and `dashboard/health_page.py` (phone-role omissions), `research/labeling/review_page.py` (the desk refusals), and their tests. Review: `safety-reviewer`, two passes.
- **A runbook task** (`docs/runbooks/phone-access.md`): the owner steps above, the verification sitting, turning Serve off with the instance, a lost phone, and "never `tailscale funnel`".
- The verification sitting (Decision 8), then the owner turns Serve on.
