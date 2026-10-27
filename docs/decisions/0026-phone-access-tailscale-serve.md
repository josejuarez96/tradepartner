# 0026. Phone access: Tailscale Serve to a read-only phone instance of the Streamlit dashboard

**Status:** Proposed  ·  **Date:** 2026-10-27  ·  **Issue:** #2389  ·  **Answers:** [ADR 0018](0018-end-user-web-app.md)'s 2026-10-10 amendment, part D, open question 5 ("How does the phone reach the app?")  ·  **Amends:** [ADR 0011](0011-dashboard-technology.md) point 3 (the [amendment at its end](0011-dashboard-technology.md#amendment-2026-10-27-a-read-only-phone-instance-behind-tailscale-serve-adr-0026))  ·  **Related:** [charter](../charter.md) (Interface, Constraints), [ADR 0018](0018-end-user-web-app.md) sections 2 and 4 (the withdrawn Tailscale rules this record restates), [paper plan](../plans/paper-trading.md) T277 (the Stop button), T278 (the phone layout, #2385), T289 to T296 (the Lab page)

**Numbering note.** 0019 stays reserved for the Phase 6 ADR (ADR 0018's numbering note); 0025 is taken on `main` by reconcile-by-breaks. This record takes 0026, the next free number.

## Context

In plain words: the owner wants to look at his books from his phone. On 2026-10-27, asked "how should your phone reach the app?" with four options (1) Tailscale, (2) a self-run WireGuard VPN, (3) a Cloudflare tunnel with sign-in, (4) no phone access, he answered **1, Tailscale**. ADR 0018's 2026-10-10 amendment requires the path to be decided in its own record, with its security boundary and tests, **before any listener but loopback exists**. This is that record. It enables nothing: no listener, no Serve configuration and no code change comes with it.

Three facts shape what the phone can reach.

- **What exists is Streamlit, not the web app.** ADR 0018's FastAPI and React app is still Proposed and unbuilt. The owner chose Streamlit "now" for the daily screens (#2006, #2096, the 2026-10-26 Home and Book amendment #2170) and said Streamlit stays unless a plan argues otherwise. The Home, Books and Lab pages are being built on it; T278 (#2385) has just given it a phone layout (a bottom nav at 640 px or less, card spacing at 390 px).
- **That Streamlit now writes.** Besides the override form (ADR 0011 point 2), the Book page has "Stop this book now" (T277, `dashboard/book_actions.kill`, which runs `paper kill` as a child process), and the Lab page (T289 to T296, `dashboard/lab_child.spawn`) registers sweeps and runs counted trials the same way. The charter's Interface line was amended on 2026-10-27 (#2304) to say so.
- **ADR 0011 closed the network for a reason.** Point 3 binds Streamlit to `localhost` because Streamlit listens on every interface by default and has no authentication, so "an `exclude_name` from another host on the LAN would lead to a sell". Its Consequences accept one more risk while only a paper account exists: DNS rebinding against Streamlit's websocket from a hostile page in the owner's own browser. ADR 0018's Options item 1 rejected a phone path to Streamlit for the same reason ("no identity check on it").

The question this record answers besides the path: **may the current Streamlit dashboard be reached from the phone now, or does phone access wait for the web app?**

## Options considered

The path (the owner's choice, recorded with what it means):

1. **Tailscale Serve (chosen by the owner).** `tailscaled` on the Mac terminates HTTPS with a tailnet certificate and proxies to a loopback port. Only devices signed in to the owner's tailnet can reach it. For a request from a tailnet user it adds `Tailscale-User-Login`, `Tailscale-User-Name` and `Tailscale-User-Profile-Pic`, and it removes any copies the client sent ("to avoid header spoofing"); a tagged device's request carries none ([Tailscale docs, kb 1312](https://tailscale.com/kb/1312/serve)). Serve requires HTTPS certificates to be enabled for the tailnet. With `--bg` it persists across reboots until turned off ([kb 1242](https://tailscale.com/kb/1242/tailscale-serve)). Pro: the Mac's process stays on loopback; no port opens on the LAN or the internet; the tailnet proves the device and the user; nothing secret lives in the phone's browser; a lost phone is a device removed in the admin console. Con: a third party (Tailscale's coordination server and the owner's sign-in provider) decides who is on the tailnet; enabling HTTPS certificates puts the Mac's tailnet name in the public certificate-transparency logs (a name, not a way in).
2. A self-run WireGuard VPN: no third party, but a UDP port forwarded on the home router, keys to rotate by hand, and no identity header (only "this device has the key"). Not chosen.
3. A Cloudflare tunnel with sign-in: an internet-facing hostname whose only gate is Cloudflare Access's policy, so a policy mistake is public. Not chosen.
4. No phone access: nothing to secure; the morning check stays at the desk. Not chosen.

**Funnel**, Tailscale's public variant of Serve, is never used on this Mac (Decision 1).

What the phone reaches (the open call):

- **(A) The current Streamlit dashboard, through a second, read-only Streamlit process on its own loopback port that only Serve proxies to, with the owner's tailnet login required on it (recommended).** Pro: the phone gets the screens the owner already uses, now, including T278's layout; no second toolchain; the same pure readers draw the same numbers. Con, weighed against ADR 0011's reasons below: Streamlit has no request-level authentication, so the identity check is application code in the shell (`dashboard/app.render_app`), run per session from the headers of the websocket's opening request (`st.context.headers`), not per HTTP request by a server; Streamlit's own endpoints that carry no store data answer before that check (Decision 4); a write on the phone would rest on that check, so this option excludes every write.
- **(B) Wait for the ADR 0018 web app.** Pro: a server built for it, with the identity rule enforced on every request before any handler and two listeners by design (ADR 0018's withdrawn section 4). Con: the app is Proposed with no plan task claimed and needs a spike, an API, a second toolchain and page-by-page parity first, so the phone waits months for screens Streamlit already draws at phone width; T278's work goes unused until then.
- **(C) Serve the existing desk Streamlit process itself** (one process for the Mac and the phone). Rejected: a request from Serve and a request from the Mac's browser both arrive from `127.0.0.1`, so one process cannot require identity from the phone without requiring it from the desk, where no header exists; and the desk process carries every write.

**ADR 0011's reasons, weighed honestly against (A).**

1. *"An `exclude_name` from another host on the LAN would lead to a sell with no authentication."* Under (A) the LAN still reaches nothing: both processes stay bound to loopback and `render_app`'s refusal of any other address stands. Only tailnet devices reach the phone instance; it requires the owner's login; and it has **no write of any kind**, so even a request that passed the check could not reach the override, a kill, a run or a registration. This reason is answered, not set aside.
2. *DNS rebinding against the websocket, accepted for the desk.* The phone instance checks `Host` against the one tailnet name it serves, so a rebound name is refused there. This record also closes the desk's accepted risk: the desk instance refuses any `Host` but `localhost` or `127.0.0.1` (Decision 5).
3. *"No client other than a browser on the same machine."* This sentence changes, for the phone instance only (the 0011 amendment). The charter's "runs locally, for personal use only" holds: every process, the store and every key stay on the Mac; the phone is a viewer of one person's system.
4. *Streamlit has no identity check (ADR 0018, Options item 1).* True of Streamlit's server. The check (A) adds is in the shell, before any store connection, which is where every page's data comes from (ADR 0011 point 1: one read-only connection per render, opened by the shell). What it cannot cover is listed in Decision 4 and accepted there. It is not strong enough to carry a write, which is why (A) has none.

**Recommendation: (A).** The owner's daily use is reading; the reasons ADR 0011 closed the network are about writes and unauthenticated reach, and (A) removes the writes and requires identity. (B) stays the path for writes from the phone.

## Decision

We will let the owner's phone reach a **read-only phone instance** of the Streamlit dashboard through **Tailscale Serve** (never Funnel), proxying HTTPS on the tailnet to a loopback port that only that instance listens on, with the owner's tailnet login required on every session and no write reachable from it. The desk instance is unchanged except for a stricter `Host` check. Nothing is enabled by this record; the code and the runbook are plan tasks (Next), and Serve is turned on by the owner after the verification sitting passes (Decision 8).

### 1. The network path

- Serve proxies `https://<the Mac's tailnet name>` (port 443) to `http://127.0.0.1:<dashboard.phone_port>` and to nothing else. It never proxies to the desk port (8501).
- **`tailscale funnel` is never run on this Mac.** The runbook says so and the verification sitting checks `tailscale funnel status` shows no public access. A Funnel request would carry no identity header and be refused by Decision 3 in any case.
- Both Streamlit processes stay bound to loopback: `render_app`'s existing refusal (ADR 0011 point 3: `server.address` must be `localhost` or `127.0.0.1`, usage stats off) applies to both, unchanged. No process binds the LAN or the Mac's tailnet address.
- The tailnet holds only the owner's own devices: no shared-in nodes, no other users. Its sign-in is the owner's identity-provider account, which carries two-factor sign-in. Device key expiry stays on (Tailscale's default).

### 2. The phone instance

- It is started by `tradepartner dashboard --phone`, which runs the same `dashboard/app.py` with `--server.address localhost`, `--server.port <dashboard.phone_port>`, `--browser.gatherUsageStats false`, `--client.showErrorDetails none`, `--client.toolbarMode viewer`, `--server.headless true`, and an environment flag `TRADEPARTNER_DASHBOARD_ROLE=phone`. Plain `tradepartner dashboard` stays the desk instance on its port.
- **The role is phone if either signal says so**: the environment flag is set, or the running `server.port` equals `dashboard.phone_port`. Either one is enough (fail closed): a desk launch on the phone port by mistake is a phone instance, and so is a phone launch on another port.
- `render_app` refuses to render the phone instance unless the running `client.showErrorDetails` is `none` (a traceback or an exception message never reaches the phone) and `client.toolbarMode` is `viewer`.
- `dashboard.phone_port` is unset by default, and **an unset `dashboard.phone_port` makes `tradepartner dashboard --phone` refuse to start**, so nothing listens for Serve until the owner sets it.

### 3. Identity, checked first on every phone-instance run

Before the shell opens any store connection, on every script run of the phone instance, `render_app` reads `st.context.headers` (the headers of the websocket's opening request, which is the request Serve proxied) and refuses to render, with one fixed sentence that names no value ("This page is not available here."), unless all of these hold:

- `dashboard.phone_owner_login` is set; **unset is a refusal of every session** (fail closed, never "check it if set").
- `Tailscale-User-Login` appears **exactly once** and equals `dashboard.phone_owner_login` exactly (case-sensitive, no trimming). Missing, empty, repeated or different is a refusal. A tagged device or a Funnel request has no such header and is refused.
- `Host` equals `dashboard.phone_host` exactly (the tailnet name, with no port, as Serve forwards it; the verification sitting records the value that actually arrives). An unset `dashboard.phone_host` is a refusal. This closes DNS rebinding on the phone instance: Streamlit's own websocket origin check compares `Origin` with `Host`, so a rebound name passes it, and only this check refuses it.

The rule rests on `tailscaled` setting the identity header on what it proxies, websocket upgrades included, and removing any copy a client sent. That is what the docs say; the verification sitting proves it on the owner's devices before Serve stays on (Decision 8). If the header does not reach the websocket request, the phone instance refuses everything: it fails closed, and this record is revisited.

### 4. What the check does not cover (accepted)

Streamlit answers these before the shell runs, on the phone instance as on the desk: the built front-end assets, `/_stcore/health`, `/_stcore/host-config`, `/_stcore/metrics` (process counters; `server.unsafeMetricsUserAttributes` stays empty, so no login is published there), `/_stcore/upload_file` (no page has a file uploader) and `/media/...` (files a session produced, under unguessable names). None reads the store. They are reachable only from the owner's tailnet devices. Accepted while that holds; revisit if a page adds an uploader, a download of store data, or a custom component.

### 5. The desk instance

- It refuses to render any request whose `Host` is not `localhost` or `127.0.0.1` (with or without its port), which **closes ADR 0011's accepted DNS-rebinding risk** for the desk.
- It refuses any request that carries a `Tailscale-User-*` or `X-Forwarded-*` header: no browser on the Mac sends one, and a request that has one was proxied, so Serve pointed at the desk port by mistake is refused. This is defence in depth beside Decision 1, not a replacement for it.
- Everything else (its writes, its pages, its port) is unchanged.

### 6. No write from the phone

- The phone instance's navigation omits the Override page and the Lab page's actions; the Book page draws its safety card without the "Stop this book now" button and says, in one line, that a book is stopped from the Mac (`tradepartner paper kill --book <book> --reason "..."`).
- **Each write function refuses when the role is phone**, inside the function itself: `book_actions.kill`, `override_page.submit`, `lab_child.spawn`, and every later write the dashboard gains. The check lives in the function, not only in the page body, because Streamlit runs an `on_click` callback **before** the script body (ADR 0011 point 2), so a callback would run before the shell's identity check. A refused write starts no child and writes nothing.
- A later write from the phone (the Stop button is the first candidate; open question 1) needs an amendment to this record with a `safety-reviewer` pass, and its identity check is re-run inside the callback.

### 7. No secret served

- The phone instance has no page or widget that shows `Settings`, an environment variable or a key. Every child-process text it might show is already scrubbed (`book_actions._scrub`, `lab_child.scrub`); with writes absent it shows none.
- `dashboard.phone_owner_login` and `dashboard.phone_host` are set **only in `.env`** (`DASHBOARD__PHONE_OWNER_LOGIN`, `DASHBOARD__PHONE_HOST`; the repo is public), join the scrubbers' secret set, and are never echoed by a refusal, a page or a log line, nor is any `Tailscale-*` value. `.env.example` lists both with empty values. `dashboard.phone_port` is not secret and may live in the repo's config.
- Telemetry stays off on both instances (ADR 0011 point 3).
- The phone's browser holds no token, key or password. A lost phone is removed from the tailnet in the admin console; nothing in `.env` rotates.

### 8. Tests and the verification sitting

**Tests** (the code task, `tests/dashboard/`, headless, no network):

- Phone role: header missing, empty, repeated, or a different login refuses and opens no store connection (a counting fake around `open_store_connection`); `dashboard.phone_owner_login` unset refuses every session; `Host` other than `dashboard.phone_host`, or that key unset, refuses; the matching login and host render.
- Role detection: the environment flag alone, and the phone port alone, each make the phone role; `--phone` with `dashboard.phone_port` unset refuses to start.
- The phone instance refuses unless `client.showErrorDetails` is `none` and `client.toolbarMode` is `viewer`.
- No write: the phone navigation has no Override page and no Lab action; the Book page in the phone role draws no Stop button; `book_actions.kill`, `override_page.submit` and `lab_child.spawn` called directly in the phone role start no child process (a fake `subprocess.Popen` that fails the test if called) and write no row (store bytes and row counts unchanged).
- Desk role: a foreign `Host` refuses; a request carrying `Tailscale-User-Login` or `X-Forwarded-For` refuses; `localhost:8501` and `127.0.0.1:8501` render.
- No secret: with sentinel values in the fixture settings for both new keys and the paper keys, no refusal text, rendered element or captured log line contains a sentinel or a `Tailscale-*` value.
- An AST test: every function in `dashboard/` that calls `subprocess` or a store writer calls the phone-role guard first, so a new write cannot forget it.

**The verification sitting** (the owner with a builder, about 30 minutes, after the code merges and before Serve is left on). It must show, in a comment on the code task's issue **with every login, tailnet name, MagicDNS name, IP and account id redacted** (the repo is public):

1. From the phone on the tailnet, the phone instance renders Home; the shell's log line (with the login redacted) shows the identity header arrived on the websocket's opening request, and records the `Host` value that arrived.
2. With `dashboard.phone_owner_login` changed to another value, the phone sees only the refusal sentence. Restored afterwards.
3. With Tailscale switched off on the phone (mobile data or the same Wi-Fi), neither the tailnet name nor the Mac's LAN address and port reaches anything.
4. `tailscale serve status` shows exactly one HTTPS handler, to `127.0.0.1:<dashboard.phone_port>`; `tailscale funnel status` shows no public access.
5. The phone instance shows no Override page, no Stop button and no Lab Run or Register.

If item 1 fails (the header does not reach the websocket request), Serve is turned off and this record is revisited; nothing is relaxed to make it pass.

## Owner steps (after the code task merges; nothing in this PR turns any of it on)

1. **Mac:** install Tailscale (the standalone download from tailscale.com, or the Mac App Store), open it and sign in with the account that will own the tailnet. Turn on its command-line tool (Tailscale menu, Settings, "Install CLI"), so `tailscale` works in Terminal.
2. **Phone:** install the Tailscale app, sign in with **the same account**, and allow its VPN profile.
3. **Admin console** (login.tailscale.com, DNS page): MagicDNS on and **HTTPS Certificates** enabled. Name the Mac something neutral first (Machines page), since its name becomes public in certificate logs.
4. **`.env`** on the Mac: set `DASHBOARD__PHONE_OWNER_LOGIN` to the login Tailscale shows for you, and `DASHBOARD__PHONE_HOST` to the Mac's tailnet name (`tailscale status` shows it; the sitting confirms the exact value). Set `DASHBOARD__PHONE_PORT=8502` (unset by default, so nothing listens until this line exists; 8502 is used below).
5. **Start the phone instance:** `uv run tradepartner dashboard --phone` (leave it running; the desk dashboard is separate).
6. **Turn Serve on**, once:
   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8502
   tailscale serve status        # one handler, to 127.0.0.1:8502
   tailscale funnel status       # must show no public access
   ```
   Then open `https://<the Mac's tailnet name>` on the phone. `--bg` keeps it on across reboots.
7. **Turn it off** at any time: `tailscale serve --https=443 off` (or `tailscale serve reset` to clear every Serve setting). **Never run `tailscale funnel`.**
8. **Lost phone:** remove the device in the admin console (Machines page). Nothing in `.env` changes.

## Consequences

- Good: the phone gets the screens the owner already uses, at T278's phone layout, now rather than after the web app; the LAN still reaches nothing; the phone instance has no write path at all, so the order path, the kill switch, the registry and the holdout are exactly as reachable as today; the desk's accepted DNS-rebinding risk (ADR 0011) is closed; the identity rule fails closed on every unset key and missing header; no secret is added to the phone.
- Bad / accepted risks: the identity check is application code run once per session from the websocket's opening headers, not a server rule on every request; Streamlit's data-free endpoints answer before it (Decision 4); a third party (Tailscale and the owner's sign-in provider) decides tailnet membership, so a compromise of that account reaches the read-only pages; the Mac's tailnet name is in public certificate logs; the phone instance is a second Streamlit process whose reads hold read connections like the desk's, so `paper run`'s writes wait behind phone renders as they do behind desk renders (bounded by the existing short-hold rule, #2178); the Mac must be awake and the phone instance running for the phone to see anything; no stop from the phone in this record (open question 1).
- Reversibility: cheap. `tailscale serve --https=443 off` closes the path in one command; the code is a role flag, one guard and a few checks in the shell; the web app, when built, takes these same rules (a tailnet port with the identity check per request) instead of the Streamlit ones.
- Revisit if: item 1 of the verification sitting fails or a Tailscale release stops setting or stripping the identity header on proxied websocket requests; the owner wants any write from the phone (an amendment with `safety-reviewer`, open question 1); a second person or a shared node joins the tailnet; a page adds a file uploader, a download of store data or a custom component (Decision 4); the ADR 0018 web app replaces the Streamlit pages (its tailnet port then follows Decisions 1, 3, 5 and 7 per request); Phase 6's live account starts (the Phase 6 ADR re-reviews this path before live keys exist on the Mac); or Tailscale's terms or free personal plan change.

## Open questions for the owner (one decision each, a recommendation beside it)

1. **May the phone stop a book ("Stop this book now")?** **Recommend not yet.** Stopping is the one write that makes things safer, and the most useful from a phone, but it would rest on the per-session check of Decision 3. Recommend a one-paragraph amendment after the verification sitting passes and the phone has been used read-only for two weeks, with the identity check re-run inside the callback and a `safety-reviewer` pass. Until then a book is stopped at the Mac.

## Next

After the owner accepts this record (he merges it; class B):

- **A code task** (a plan amendment names it; size M, class B for `config.py` and `.env.example`): `cli.py` (`dashboard --phone`), `config.py` (`dashboard.phone_port`, `phone_owner_login`, `phone_host`), `.env.example`, `dashboard/app.py` (the role, the identity, host and option checks, the phone navigation), `dashboard/book_page.py` (no Stop button in the phone role), `dashboard/book_actions.py`, `dashboard/override_page.py`, `dashboard/lab_child.py` (the guard), and their tests. Review: `safety-reviewer`, two passes.
- **A runbook task** (`docs/runbooks/phone-access.md`): the owner steps above, the verification sitting, turning Serve off, a lost phone, and "never `tailscale funnel`".
- The verification sitting (Decision 8), then the owner turns Serve on.
