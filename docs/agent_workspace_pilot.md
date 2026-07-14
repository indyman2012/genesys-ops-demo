# Agent Workspace — Pilot Plan

**Status:** Approved to build (2026-07-13)  
**Org:** Archwell Health — Genesys Cloud (`usw2`)  
**Demo shell:** `/agent` on Render (`agent_app_demo.py` — simulation only)  
**Pilot path:** Local dev → **Render** (live Genesys) → Azure App Service if pilot proceeds

---

## Goal

Replace the dense Genesys Agent UI for a **voice-first pilot cohort** with the ArchWell Agent Workspace:

- Queue-context outbound only (`callFromQueueId` — no free dial)
- Clear agent states (on queue, on call, away, DND)
- Real WebRTC media (mic/speaker pickers already in UI)
- Supervisor reports in-shell (Genesys Native + Custom AWH tabs)
- Ops console for admins (existing `/`)

**Not in pilot v1:** Full analytics rebuild, org-wide rollout, chat alert monitor.

---

## What exists today

| Layer | State |
|-------|--------|
| UI shell | Done — layout, branding, media pickers, waiting calls, listen (demo), role nav |
| Demo backend | `demo_state.py` — in-memory simulation |
| Supervisor reports | Tabbed Genesys Native + Custom AWH |
| Ops console | Live tools on `ops_app.py` |
| Production agent entry | `agent_app.py` — scaffold (no WebRTC yet) |
| Genesys OAuth POC | `_probe_toast_test.py` — authorization-code flow pattern |

---

## Pilot phases

### Phase 0 — Foundation (week 1)

- [ ] Create **dedicated OAuth client** in Genesys (authorization code + PKCE, agent-scoped roles)
- [ ] **Local dev:** run `agent_app.py` on `127.0.0.1:8083` with live OAuth callback (`http://localhost:8083/agent/oauth/callback`)
- [ ] **Render pilot:** deploy `agent_app.py` (not demo) with `AGENT_DEMO_MODE=0` + agent OAuth secrets
- [ ] Register Render redirect URI on OAuth client: `https://genesys-ops-demo-3.onrender.com/agent/oauth/callback` (or dedicated pilot service)
- [ ] Secrets: `GENESYS_AGENT_CLIENT_ID`, `GENESYS_AGENT_CLIENT_SECRET`, `FLASK_SECRET_KEY`
- [ ] Pilot queue allowlist (2–4 Patient Services centers)
- [ ] Azure AD SSO — **deferred** until App Service if pilot proceeds

### Phase 1 — Identity + queue membership (week 2)

- [x] Agent login session (server-side token storage)
- [x] Load agent's queues from `GET /api/v2/users/{id}/queues`
- [x] **Go On Queue / Off Queue** → `PATCH .../queues/{queueId}` `joined: true/false`
- [ ] Presence: On Queue, Away, DND (routing status APIs)
- [x] Swap `/agent/api/state` from `demo_state` to live service behind feature flag

### Phase 2 — WebRTC telephony (weeks 3–4)

- [ ] Platform SDK in browser (Genesys Cloud JavaScript SDK)
- [ ] Inbound: ring → answer → hold → end → wrap-up
- [ ] Outbound: dial-on-behalf with `callFromQueueId` enforced server-side
- [ ] Media: `enumerateDevices()` (UI exists) wired to SDK
- [ ] Remove simulate-call buttons in production mode

### Phase 3 — Real-time panels (week 5)

- [ ] Queue membership panel — live members + joined state
- [ ] Waiting calls — queue conversations API / notifications
- [ ] Queue activity — observations (`oWaiting`, service level, ASA)
- [ ] Agent stats — start with Genesys Analytics aggregates (or defer to supervisor reports)

### Phase 4 — Pilot polish (week 6)

- [ ] Per-queue join/leave (v1.1 backlog)
- [ ] Ops console ← Agent workspace link
- [ ] Supervisor queue member add/remove (role-gated)
- [ ] Pilot feedback survey + rollback path to native Genesys UI

---

## Hosting strategy

| Stage | Host | Entry | Purpose |
|-------|------|-------|---------|
| **Now** | Local PC | `agent_app.py` :8083 | Wire OAuth, live APIs, WebRTC |
| **Pilot** | Render | `agent_app.py` via gunicorn | Small cohort access via URL |
| **Later** | Azure App Service | TBD internal URL | SSO, VNet, production scale — only if pilot succeeds |

Keep **`agent_app_demo.py`** on Render for stakeholder demos (no credentials).  
Pilot uses **`agent_app.py`** on a separate Render service or the same repo with a different start command and env vars.

**Render start command (pilot):**
```
gunicorn agent_app:app --bind 0.0.0.0:$PORT --timeout 120
```

---

## Architecture

```
Browser (Chrome/Edge)
  └── Agent Workspace UI (templates + agent.js)
        ├── Flask /agent/api/*  (session, queue ops, validation)
        └── Genesys Platform SDK (WebRTC, conversations, presence)
              └── Genesys Cloud APIs (usw2)
```

**Key rule:** Outbound calls must include `callFromQueueId` on the server — the UI never exposes a free-dial path.

---

## Genesys SDK reference (guidance, not a rewrite)

ArchWell Agent Workspace is a **standalone** web app — not an iframe panel inside the native Genesys desktop. Official Genesys JS libraries are **reference material** as we wire telephony and real-time panels; they do not replace our Flask shell, OAuth session, or UI.

### Two libraries — do not mix them up

| Library | Repo / package | Use for ArchWell pilot? |
|---------|----------------|-------------------------|
| **Platform API JS Client** | [platform-client-sdk-javascript](https://github.com/MyPureCloud/platform-client-sdk-javascript) · `purecloud-platform-client-v2` | **Yes** — headless agent workspace: REST, notification WebSocket, conversations, presence, WebRTC |
| **Client Apps SDK** | [client-app-sdk](https://github.com/MyPureCloud/client-app-sdk) · `purecloud-client-app-sdk` | **Reference only** — apps embedded *inside* Genesys Cloud UI (iframe lifecycle, toasts, parent-frame navigation) |

Genesys documents the split explicitly: Client Apps SDK handles **UI integration with the Genesys desktop**; **data and telephony** go through the Platform API client.

### What we keep vs what we borrow

| Ours (keep) | Borrow from Genesys docs/SDKs |
|-------------|-------------------------------|
| ArchWell UI, queue layout, supervisor reports | Conversation/notification API shapes |
| Flask OAuth + server-side token storage | WebRTC + media patterns in Platform SDK |
| Server-gated dial-on-behalf (`callFromQueueId`) | Presence / routing status APIs |
| Pilot queue allowlist, session `joined` PATCH | Real-time panel data (waiting, active, membership) |

### Phase → likely SDK touchpoints

| Phase | Primary reference |
|-------|-------------------|
| **1** (done) | REST via Flask + bearer token — same APIs Platform SDK wraps |
| **2** WebRTC | Platform SDK in browser; token from Flask session (or SDK auth aligned with existing OAuth client) |
| **3** Live panels | Platform SDK notifications channel + Conversations / Analytics APIs |
| **4** Polish | Client Apps docs only if we add a **hybrid** embedded widget in native Genesys later |

### Helpful Genesys links

- [Client Apps developer guides](https://developer.mypurecloud.com/api/client-apps/index.html) — integration patterns, app registration (`embedded-client-app`)
- [Client Apps SDK API](https://developer.mypurecloud.com/api/client-apps/sdk.html) — lifecycle, alerting (embedded apps)
- [Platform API JS Client docs](https://mypurecloud.github.io/platform-client-sdk-javascript/) — API surface we will call from `agent.js`
- CDN (Platform): `https://sdk-cdn.mypurecloud.com/javascript/latest/purecloud-platform-client-v2.min.js`

**Region:** ArchWell org is `usw2` — Platform SDK environment must match (`us_west_2` / `usw2.pure.cloud`).

---

## Agent sign-in (Microsoft SSO)

Agents authenticate through **Genesys OAuth** in a **popup** — not a custom login form in ArchWell.

| Step | What happens |
|------|----------------|
| 1 | Agent clicks **Sign in** in the workspace |
| 2 | Popup opens the **native Genesys login** (same as today) |
| 3 | Agent clicks **Microsoft SSO** — no username/password in normal use |
| 4 | Popup closes; Flask session holds tokens; workspace loads |

**Org name:** Required only rarely (~1% of sign-ins). Browsers remember the org; the org field usually appears only after a **browser cache clear**. OAuth client and redirect URI are tied to `usw2` — we do not ask agents to type the org on every login.

**Browsers:** Agents have **Chrome** or **Edge** only. **Chrome preferred**; both are supported for OAuth popups, WebRTC, and media device pickers. Stay in one browser per shift (SSO cookies and mic permissions are per browser).

**Do not:** iframe Genesys or Microsoft login (Microsoft blocks iframe SSO).

---

## Pilot cohort (fill in)

| Item | Decision |
|------|----------|
| Centers | _TBD — suggest 2–4 PS queues_ |
| Agents per center | _TBD — 3–8 each_ |
| Duration | 4–6 weeks |
| Success metrics | Masking compliance, AHT stability, agent preference vs native UI |
| Rollback | Agents keep native Genesys bookmark; pilot URL disabled per user |

---

## Environment variables (pilot)

**Local:**
```env
AGENT_DEMO_MODE=0
GENESYS_CLOUD_REGION=usw2.pure.cloud
GENESYS_AGENT_CLIENT_ID=...
GENESYS_AGENT_CLIENT_SECRET=...
GENESYS_AGENT_REDIRECT_URI=http://127.0.0.1:8083/agent/oauth/callback
FLASK_SECRET_KEY=...
AGENT_PILOT_QUEUE_IDS=uuid1,uuid2   # optional allowlist
```

**Render (add redirect URI to OAuth client in Genesys):**
```env
AGENT_DEMO_MODE=0
GENESYS_AGENT_REDIRECT_URI=https://YOUR-PILOT-SERVICE.onrender.com/agent/oauth/callback
# ... same secrets as local (Render env vars, not in git)
```

---

## Security

1. **Never** deploy `client.env` / admin credentials to the agent host.
2. Use a **separate OAuth client** with agent + supervisor permissions only (not the toolkit admin client).
3. **Render pilot:** public URL is acceptable for a small cohort — use pilot allowlist + dedicated OAuth client; rotate secrets if moving to Azure.
4. **Azure (later):** internal VNet, Azure AD SSO, no public internet requirement.
5. Audit log for queue membership changes (supervisor actions).

---

## Related docs

- [README-AGENT-DEMO.md](../README-AGENT-DEMO.md) — demo deploy (Render)
- [README-DEMO.md](../README-DEMO.md) — ops console demo
- [Genesys Client Apps SDK](https://github.com/MyPureCloud/client-app-sdk) — embedded-app patterns (reference)
- [Genesys Platform API JS Client](https://github.com/MyPureCloud/platform-client-sdk-javascript) — telephony + real-time (primary for Phase 2–3)
- v1.1 backlog in README-AGENT-DEMO.md (per-queue join, ops back link)
