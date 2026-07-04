# Portal Agent — headed, read-only Azure Portal navigator (local demo)

Opens a **visible Chromium** window beside CRE Copilot and, during an incident, navigates
**deep-links to our own resources** in the Azure Portal (Overview → Revisions → Log stream →
Metrics). Every step streams into the console's **Portal Agent** worker card + evidence timeline.

**It is read-only.** It only navigates and reads — it never clicks Save / Delete / Create /
Restart / Rollback, and remediation always happens through the backend (`/api/remediate` +
Verifier), never the portal.

## Guarantees
- **Local-only** (headed browser needs a display) and **off by default** (`AZURE_PORTAL_AGENT_ENABLED=false`).
- **No secrets** — reuses a logged-in local browser profile (persistent context in a gitignored dir).
- **Graceful** — if Playwright is missing, you're not signed in, MFA fails, or a blade won't load,
  it emits a `Failed` event and the **normal incident flow continues unaffected**.
- **Least privilege** — sign in with a dedicated **Reader** account (view-only) scoped to the RG.

## One-time setup
```bash
# 1) install Playwright + Chromium
pip install -r portal_agent/requirements.txt
playwright install chromium

# 2) sign in ONCE into the reusable profile (opens a real browser; do MFA here)
python portal_agent/portal_agent.py login
#    → a Chromium window opens at portal.azure.com. Sign in (Reader demo account) + MFA,
#      then press Enter in the terminal. The session is saved to ~/.cre-portal-profile.
```

## Run the two-window demo
```bash
AZURE_PORTAL_AGENT_ENABLED=true ./demo/console.sh      # backend at http://localhost:8000
```
- **Left:** CRE Copilot at `http://localhost:8000` (the timeline).
- **Right:** the headed Chromium the Portal Agent opens on **Run incident response** (it starts at
  window position x=760 so it lands beside the app; drag to taste).

## What it does per incident
The active incident's service selects which Container App to open:
`auth-service`, `payment-service`, `inventory-service`, `checkout-api` → that app's read-only blades.

## How to run the two-window interview demo

Goal: **left** = CRE Copilot console (agents, evidence feed, timeline, gate, runbook); **right** =
the Playwright-controlled Azure Portal, ending on the affected service's **Log Stream** so you can
watch it update live *while* the CRE Copilot timeline says Detector/Correlator/Impact are working.

```bash
# one-time (if not done): install + sign in
pip install -r portal_agent/requirements.txt && playwright install chromium
python portal_agent/portal_agent.py login          # sign in (Reader account + MFA); session saved

# run — the flag is loaded from demo/.env (local), so it stays on across restarts
source demo/env.sh && ./demo/console.sh             # http://localhost:8000
```

1. **Arrange windows:** put your CRE Copilot browser on the **left half** of the screen. The Portal
   Agent's Chromium opens on the **right** automatically (position `1000,0`, size `1000×1300`).
   Tune with `PORTAL_WINDOW_POS` / `PORTAL_WINDOW_SIZE` (e.g. `PORTAL_WINDOW_POS=1280,0`).
2. In CRE Copilot: **Sandbox → Bad deploy / Kill / Inject errors / Traffic** on a service.
3. Click **▶ Run incident response**.
4. Watch both live:
   - **Right (Portal):** opens the event-specific page first (Revisions / Overview / App Insights
     Failures / Metrics), then lands on **Log Stream** and **stays there ≥60s** streaming live.
   - **Left (CRE Copilot):** agents + evidence feed + timeline advance; the Portal Agent's steps
     also stream into the timeline so the two sides line up.
5. Approve to remediate → the Verifier runs and the Portal Agent keeps Log Stream open.
6. The browser stays open until the investigation completes **+ 15 seconds**, then closes.

## Embedded live browser (noVNC) — optional, `PORTAL_AGENT_NOVNC=true`

Instead of an external window, stream a **true live browser** into the console's right panel. A
Docker container runs headed Chromium on a virtual display + noVNC; the backend drives that Chromium
over CDP; the console embeds noVNC in an `<iframe>`. **Default off. If the container isn't reachable,
it falls back to the external headed Chromium automatically.** Local-only, read-only, not deployed.

```bash
# 1) build + run the VNC container (needs Docker Desktop)
docker compose -f portal_agent/vnc/docker-compose.yml up --build       # noVNC :6080, CDP :9222
#    (or: docker build -t cre-portal-vnc portal_agent/vnc &&
#         docker run --rm -p 6080:6080 -p 9222:9222 -v cre-portal-profile:/profile cre-portal-vnc)

# 2) open noVNC once to LOG IN (Azure + MFA) — the container profile persists it
open http://localhost:6080/vnc.html          # sign in to the Azure Portal in this view

# 3) run CRE Copilot with the embedded panel on
PORTAL_AGENT_NOVNC=true AZURE_PORTAL_AGENT_ENABLED=true ./demo/console.sh
#    → open http://localhost:8000 ; the right panel "Live Azure Portal (embedded)" shows the stream
```

- **Login once:** do it in the noVNC view (step 2) — the in-container Chromium profile (Docker
  volume `cre-portal-profile`) keeps you signed in across runs.
- **Run:** break a service → **Run incident response** → the embedded browser navigates the
  event-specific blades → Log Stream, in sync with the timeline on the left.
- **Fallback:** if Docker isn't running / port 9222 unreachable, the backend logs
  `portal.novnc_connect_failed` and opens the **external** headed Chromium instead — the demo still works.
- **Turn off:** just omit `PORTAL_AGENT_NOVNC=true` → back to the external-window mode (or omit
  `AZURE_PORTAL_AGENT_ENABLED` to disable the Portal Agent entirely).

## Config (env)
| Var | Default |
|---|---|
| `AZURE_PORTAL_AGENT_ENABLED` | `false` |
| `AZURE_SUBSCRIPTION_ID` / `AZURE_TENANT_ID` / `AZURE_RESOURCE_GROUP` | our sub / tenant / `rg-cre-copilot` |
| `PORTAL_USER_DATA_DIR` | `~/.cre-portal-profile` (gitignored) |

## Azure permissions
Use a dedicated **Entra user with Reader** on `rg-cre-copilot` (and *Log Analytics Reader* for the
Log stream). Reader can view status/revisions/metrics/logs but **cannot modify** anything — so even
if a control were clicked, the account can't perform a destructive action.
