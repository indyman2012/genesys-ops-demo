# Site Picker — Required Permissions & Roaming Agent Role

**Date:** 2026-09-15  
**Role ID:** `d84ea170-4869-417d-9ece-c5d8b0a1c1d5`  
**Build:** `2026-09-15-phone-lookup-fallback-v5` (`app/site_picker/telephony.py`)

## What Site Picker Does

Site Picker is a small Flask SPA (`static/site_picker/`, mounted at `/site-picker`) that lets a signed-in agent:

1. **OAuth sign-in** via the agent OAuth client (`GENESYS_AGENT_CLIENT_ID` / `GENESYS_AGENT_CLIENT_SECRET`), reusing `/agent/oauth/callback`.
2. **View** their WebRTC phone name and current telephony site.
3. **Change** their WebRTC phone's telephony site assignment (site = outbound caller ID / routing context for WebRTC).

It does **not** change queue membership, user profile, station provisioning, or division assignment. The only mutation is updating the phone record's `site` field.

---

## Genesys API Endpoints (User Token)

All telephony calls use the **agent's OAuth access token** unless admin fallback is triggered (see below).

| Method | Endpoint | Purpose | Required permission |
|--------|----------|---------|---------------------|
| GET | `/api/v2/users/me` | OAuth identity after sign-in | Any authenticated user |
| GET | `/api/v2/telephony/providers/edges/sites` | Populate site dropdown | `telephony:sites:view` |
| GET | `/api/v2/telephony/providers/edges/sites/{siteId}` | Resolve target site before change | `telephony:sites:view` |
| GET | `/api/v2/telephony/providers/edges/phones?webRtcUser.id={userId}&expand=site` | Find agent's WebRTC phone + current site | `telephony:phone:assign` (scoped to site) |
| GET | `/api/v2/telephony/providers/edges/phones/{phoneId}?expand=site` | Phone detail for site enrichment | `telephony:phone:assign` (scoped) |
| GET | `/api/v2/telephony/providers/edges/phones?name={name}&expand=site` | Fallback phone lookup by name | `telephony:phone:assign` (scoped) |
| GET | `/api/v2/users/stations/me` | Fallback: station → phone association | `telephony:selfStationAssociation:view` |
| GET | `/api/v2/stations?webRtcUserId={userId}` | Fallback: WebRTC station lookup | `telephony:selfStationAssociation:view` |
| PUT | `/api/v2/telephony/providers/edges/phones/{phoneId}` | Change phone site (full phone payload) | `telephony:phone:assign` on **target site** |

### Admin fallback (server client credentials)

When the user token cannot read or update the phone, the server uses `create_client()` (`client.env`) as a read/write fallback:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v2/telephony/providers/edges/phones?webRtcUser.id={userId}&expand=site` | Read-only phone/site when user token lacks phone access |
| GET | `/api/v2/telephony/providers/edges/phones/{phoneId}` | Full phone for PUT payload |
| PUT | `/api/v2/telephony/providers/edges/phones/{phoneId}` | Site change when user PUT fails |

Admin fallback requires `GENESYS_CLIENT_ID` / `GENESYS_CLIENT_SECRET` on the hosting service (e.g. Render). Prefer fixing agent permissions so changes happen under the user's own token.

---

## Minimum Role Permissions

Genesys telephony permission catalog (2026-09-15) exposes **no separate `phone:view`** — the only phone entity action is **`assign`**, which covers reading and updating phones on named sites.

| Domain | Entity | Actions | Scope | Why |
|--------|--------|---------|-------|-----|
| telephony | sites | view | org-wide | List sites in dropdown; resolve site by ID |
| telephony | selfStationAssociation | view, edit | self | Station/phone discovery via `/users/stations/me` |
| telephony | station | disassociateSelf | self | Optional station disassociation |
| telephony | phone | assign | **per site ID** | Read WebRTC phone + PUT site change on each roaming site |

### OAuth (not a role permission)

OAuth authorization code + PKCE is configured on a **separate OAuth client** in Genesys Admin:

- Env: `GENESYS_AGENT_CLIENT_ID`, `GENESYS_AGENT_CLIENT_SECRET`
- Redirect: `{service}/agent/oauth/callback` (Site Picker reuses agent callback)
- Allowed roles on the OAuth client must include the user's base agent role **and** Roaming Agent

No special `users:` permission is required for `/api/v2/users/me`.

### Division / queue constraints

- **Roaming Agent** must be **assigned to the user** in Admin → People → Roles, in the division(s) where they work. Role permissions apply org-wide for telephony site scope, but the role must be on the user record.
- **Queue membership is unaffected.** Site change only updates telephony site on the WebRTC phone.
- **Site dropdown vs phone assign:** The UI lists every site returned by `telephony:sites:view`. The change-site API only checks that the chosen site appears in that list. If `phone:assign` is missing on the target site, the PUT fails even though the site appears in the dropdown. Keep `phone:assign` aligned with sites agents should roam to.

---

## Roaming Agent Role — Before / After

### Before (2026-09-15 investigation)

| Policy | Actions | Scope |
|--------|---------|-------|
| telephony:sites | view | org-wide |
| telephony:selfStationAssociation | view, edit | self |
| telephony:station | disassociateSelf | self |
| telephony:phone | assign | **107 sites** |

**Gap:** `CLEMKT` (`f7850fcf-30c8-4023-88ce-a91509cf87e2`) was missing from `phone:assign` named resources. Org had **108** telephony sites; role covered 107. Agents roaming to CLEMKT could sign in and see the site in the list but could not change their phone to that site.

### After (2026-09-15 update)

Applied via:

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python tools/users_roles/create_roaming_agent_role.py --apply --all-sites
```

| Policy | Actions | Scope |
|--------|---------|-------|
| telephony:sites | view | org-wide |
| telephony:selfStationAssociation | view, edit | self |
| telephony:station | disassociateSelf | self |
| telephony:phone | assign | **108 sites** (all telephony sites) |

**Change:** Added 1 site to `telephony:phone:assign` named resources — **CLEMKT** (`f7850fcf-30c8-4023-88ce-a91509cf87e2`). No other policy actions were added or removed.

Result JSON: `outputs/roaming_agent_role_result.json`

### Re-sync after new market sites

When IT creates a new telephony site (e.g. market setup), re-run:

```powershell
python tools/users_roles/create_roaming_agent_role.py --apply --all-sites
```

Or scope to specific sites:

```powershell
python tools/users_roles/create_roaming_agent_role.py --apply --site-names "CLEMKT,Headquarters"
```

Preview only (no API writes):

```powershell
python tools/users_roles/create_roaming_agent_role.py --all-sites
```

---

## Manual Admin UI Steps (if API role update unavailable)

1. Admin → **People** → **Roles** → **Roaming Agent** → **Permissions**
2. Ensure these telephony permissions:
   - **Sites** → View
   - **Self Station Association** → View, Edit
   - **Station** → Disassociate Self
   - **Phone** → Assign → add every telephony site the agent may select (or all market sites)
3. Admin → **Integrations** → **OAuth** → agent OAuth client → **Roles** tab → include Roaming Agent
4. Admin → **People** → user → **Roles** → assign **Roaming Agent** (in addition to Employee/Agent license role)

---

## Test Checklist (Roaming Agent User)

Prerequisites: User has Employee/Agent license role **plus** Roaming Agent; WebRTC phone exists (`{Name} Web`); OAuth env vars set on the Site Picker host.

- [ ] **Sign-in:** Open `/site-picker/` → Sign in with Genesys → popup completes, name/email shown
- [ ] **Phone load:** Current WebRTC phone name and site display (not "No WebRTC phone found")
- [ ] **Site list:** Dropdown shows telephony sites (not "No sites available")
- [ ] **Change site (happy path):** Select a different allowed site → Change location → success message; current site updates on refresh
- [ ] **CLEMKT specifically:** Select CLEMKT → change succeeds (regression for 2026-09-15 fix)
- [ ] **Permission boundary:** If testing a site *not* in `phone:assign`, expect PUT failure / 502 with telephony permission message (unless admin fallback succeeds)
- [ ] **Genesys desktop:** After site change, confirm outbound caller ID / site context in Genesys matches selected site
- [ ] **Sign-out / re-sign-in:** Session clears; state returns to connect panel

Local run:

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python site_picker_app.py
# Browser: http://127.0.0.1:8765/
```

---

## Related Files

| File | Role |
|------|------|
| `app/site_picker/routes.py` | Flask routes, OAuth bridge, `/api/change-site` |
| `app/site_picker/telephony.py` | Genesys API calls (user token + admin fallback) |
| `app/agent_workspace/oauth.py` | OAuth PKCE, `users/me`, session |
| `tools/users_roles/create_roaming_agent_role.py` | Create/update Roaming Agent role |
| `static/site_picker/site-picker.js` | Front-end UI |

---

## Blockers / Notes

- **New sites:** Roaming Agent `phone:assign` is a static named-resource list. New telephony sites are **not** auto-included; re-run `--apply --all-sites` after market setup.
- **Admin fallback masks permission gaps:** If server admin creds are present, site changes may succeed even when the agent token lacks `phone:assign`. Prefer correct role assignment over relying on fallback.
- **OAuth client roles:** Roaming Agent on the user record is not enough if the OAuth client does not allow that role for authorization code flow.

---

## Troubleshooting — Phone data does not load (2026-09-15)

### Symptom

Agent signs in successfully (name/email shown), site dropdown may populate, but **WebRTC phone** shows empty or "WebRTC phone not loaded". **Change Location** is disabled or returns *No WebRTC phone found* / HTTP 404 on `/api/change-site`.

OAuth client permissions and Roaming Agent site sync (108 sites) are **not** the root cause when this specific pattern appears.

### Root cause (fixed in v5)

Two bugs in `app/site_picker/telephony.py`:

1. **Station ID mistaken for phone ID.** When the agent token could not read phones by `webRtcUser.id` (403) but could read `/users/stations/me`, the code returned the **station record** as if it were a phone — using the station UUID as `phone.id`. The UI sometimes showed a station name, but `PUT .../phones/{stationId}` failed because that ID is not an edge phone.

2. **Admin fallback too narrow.** `fetch_phone_site_admin_fallback()` ran only when a phone was found but **missing `site_id`**. If user-token discovery failed entirely, admin creds were never used to locate the phone — even though the same admin query (`phones?webRtcUser.id=`) is safe for self-only lookup.

### Fix (build `2026-09-15-phone-lookup-fallback-v5`)

| Change | File |
|--------|------|
| Removed `_phone_from_station` fake phone records | `telephony.py` |
| Station fallbacks resolve via phone GET by id, then name search (`{Name} Web`) | `telephony.py` |
| Added `resolve_user_web_phone()` — user token paths, then admin fallback | `telephony.py` |
| `/api/state` returns `phone_error` with actionable text when phone missing | `routes.py` |
| UI shows `phone_error` and info banner when phone loaded via admin | `site-picker.js` |

### Token usage after fix

| Step | Token |
|------|-------|
| Sign-in, sites list, site resolve | Agent OAuth token |
| Phone discovery (primary) | Agent OAuth token — `phones?webRtcUser.id`, stations/me, name filter |
| Phone discovery (fallback) | Server `client.env` — same `webRtcUser.id` filter, **read-only**, scoped to session user id |
| Site change PUT (primary) | Agent token with full phone payload |
| Site change PUT (fallback) | Server admin client when agent PUT fails or lacks lines |

### Verify on Render / local

1. Confirm build in `/site-picker/api/setup` → `"build": "2026-09-15-phone-lookup-fallback-v5"`.
2. Sign in as Roaming Agent user with a `{Name} Web` phone.
3. **Phone load:** WebRTC phone name + current site display; no `phone_error` in `/site-picker/api/state`.
4. **Change site:** Select another allowed site → success; refresh shows new site.
5. **Regression:** User whose agent token cannot read phones but admin creds exist — phone loads (may show admin info banner); change still works via admin PUT fallback.

Local:

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python site_picker_app.py
# http://127.0.0.1:8765/ — requires GENESYS_AGENT_* and optionally client.env for admin fallback
```

Check state JSON while signed in:

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/state" -WebSession $session
# Expect: phone.id, phone.name, phone.site_name; no phone_error
```
