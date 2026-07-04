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
