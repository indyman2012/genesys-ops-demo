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

- [ ] Agent login session (server-side token storage)
- [ ] Load agent's queues from `GET /api/v2/users/{id}/queues`
- [ ] **Go On Queue / Off Queue** → `PATCH .../queues/{queueId}` `joined: true/false`
- [ ] Presence: On Queue, Away, DND (routing status APIs)
- [ ] Swap `/agent/api/state` from `demo_state` to live service behind feature flag

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

```
Browser (Chrome/Edge)
  └── Agent Workspace UI (templates + agent.js)
        ├── Flask /agent/api/*  (session, queue ops, validation)
        └── Genesys Platform SDK (WebRTC, conversations, presence)
              └── Genesys Cloud APIs (usw2)
```

**Key rule:** Outbound calls must include `callFromQueueId` on the server — the UI never exposes a free-dial path.

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
- v1.1 backlog in README-AGENT-DEMO.md (per-queue join, ops back link)
