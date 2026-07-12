# Archwell Agent Workspace — Demo Deployment

Simplified, company-branded agent UI for socializing with stakeholders and training new hires. **Demo mode only** — simulated calls, queue membership, activity metrics, and agent stats. No Genesys OAuth required.

## Run demo locally

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python agent_app_demo.py
```

Open: **http://127.0.0.1:8082/agent**

Health check: **http://127.0.0.1:8082/agent/health**

## Demo vs future production

| File | Port | Genesys connection |
|------|------|--------------------|
| `agent_app_demo.py` | 8082 | No — in-memory simulation |
| `agent_app.py` (future) | TBD | Yes — Platform SDK + WebRTC |

### Layout (v1 demo)

- **Upper left:** On Queue / Off Queue, media status, dial-on-behalf (queue required)
- **Center top:** Current call / wrap-up
- **Center bottom:** Queue collaborate chat
- **Right top:** Queue membership (simulated real-time, 5s refresh)
- **Right bottom:** Queue activity — calls in progress, service level, ASA (5s refresh)
- **Footer:** Agent statistics (30-min refresh; manual refresh in demo)

### Try the demo flow

1. Select a queue from the header dropdown.
2. Click **Go On Queue**.
3. **Simulate inbound ring** → **Answer** → **End call** → wrap-up.
4. Or enter a number and **Dial on behalf** (caller ID stays on the queue).
5. Post a message in queue chat; watch membership and activity jitter every 5 seconds.

---

## Free hosting (Render) — same pattern as ops demo

Best balance for a Flask demo: free tier, easy env vars, custom subdomain.

1. Sign up: https://render.com
2. New **Web Service** → connect your GitHub repo
3. **Start command:**
   ```
   gunicorn agent_app_demo:app --bind 0.0.0.0:$PORT --timeout 120
   ```
4. **Environment variables:**
   - `AGENT_DEMO_MODE=1` (optional — demo entry point sets this)
   - `PYTHON_VERSION=3.13.0`
5. Free tier sleeps after ~15 min idle (cold start ~30s on first visit)

A starter spec is in `render.agent.demo.yaml`. For Heroku-style hosts, use `Procfile.agent.demo`.

**Note:** Demo deploy does **not** need `GENESYS_CLIENT_ID` / `GENESYS_CLIENT_SECRET`.

---

## Azure Web App (internal network — production target)

When you move beyond demo:

1. Deploy the Flask app to an **Azure App Service** on your internal VNet.
2. Use **Genesys Cloud OAuth** (authorization code or PKCE for browser agents).
3. Wire **Platform SDK** for presence, routing, conversations, and **WebRTC** for media.
4. Keep **dial-on-behalf** enforced server-side (`callFromQueueId`) — no free dial.

The demo UI layout is the shell; swap `/agent/api/*` handlers from `demo_state` to live Genesys APIs.

---

## One-time install (local or host)

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
pip install -r requirements.txt
```

`gunicorn` is already in `requirements.txt`.

---

## Push to GitHub

If this folder is not yet on GitHub:

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
git status
git add agent_app_demo.py app/agent_workspace/ templates/agent_workspace/ static/agent_workspace/ README-AGENT-DEMO.md render.agent.demo.yaml Procfile.agent.demo
git commit -m "Add agent workspace Flask demo for socialization"
git remote add origin https://github.com/YOUR_ORG/GenesysCloudToolkit.git
git push -u origin main
```

If the repo already exists on GitHub, commit and push the new files to your usual branch, then connect Render (or Azure) to that repo.

---

## Security tips for public demo

1. Deploy **`agent_app_demo.py`** only — no Genesys credentials on the host.
2. Do not expose production `client.env` on any public URL.
3. When you add live Genesys, use a **separate OAuth client** scoped to agent permissions only.

---

## Related docs

- Ops console demo: [README-DEMO.md](README-DEMO.md) (port **8081**)
- Ops production entry: `ops_app.py` (port **8080**)
