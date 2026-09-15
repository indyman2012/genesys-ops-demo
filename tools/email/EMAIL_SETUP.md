# Email setup (Gmail + Office 365)

GenesysCloudToolkit sends scheduled reports and ad-hoc exports via SMTP.  
The sender module is `tools/email/gmail_send.py` (name kept for backward compatibility).

## Which config file?

| File | Use when |
|------|----------|
| `email.env` | **Preferred** on work hosts and Azure App Service (O365) |
| `doug.env` | Local dev on Doug's PC with personal Gmail (legacy) |
| Process env | Azure App Service settings / Key Vault references (override file values) |

Resolution order:

1. `--env-file path` if passed on the command line
2. `email.env` if it exists in the project root
3. `doug.env` (Gmail local dev fallback)

Process environment variables always override values from the env file.

## Office 365 (Archwell) — production target

Confirmed SMTP settings:

| Setting | Value |
|---------|-------|
| Host | `smtp.office365.com` |
| Port | `587` (TCP) |
| Encryption | STARTTLS (TLS 1.2+) |

### Quick start

1. Copy the template:

   ```powershell
   cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
   Copy-Item email.env.example email.env
   ```

2. Fill in credentials (when IT provides them):

   ```env
   EMAIL_PROVIDER=office365
   SMTP_HOST=smtp.office365.com
   SMTP_PORT=587
   SMTP_USE_TLS=true
   SMTP_USER=your.name@archwellhealth.com
   SMTP_PASSWORD=your-app-or-mailbox-password
   SMTP_FROM=your.name@archwellhealth.com
   FROM_NAME=GenesysCloudToolkit Reports
   ```

3. Dry-run (builds message, does not send):

   ```powershell
   python tools/email/gmail_send.py --dry-run --subject "O365 test" --body "Config check"
   ```

4. Send when creds are ready:

   ```powershell
   python tools/email/gmail_send.py --to doug.hendrickson@archwellhealth.com --subject "O365 test" --body "Hello from toolkit"
   ```

Setting `EMAIL_PROVIDER=office365` (without `SMTP_HOST`) also selects the O365 defaults (host/port/TLS).

### Azure App Service

On Azure, prefer **Application settings** (or Key Vault references) instead of a file on disk:

| App setting | Example |
|-------------|---------|
| `EMAIL_PROVIDER` | `office365` |
| `SMTP_HOST` | `smtp.office365.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USE_TLS` | `true` |
| `SMTP_USER` | `@Microsoft.KeyVault(SecretUri=...)` or plain UPN |
| `SMTP_PASSWORD` | `@Microsoft.KeyVault(SecretUri=...)` |
| `SMTP_FROM` | `genesys-reports@archwellhealth.com` |
| `FROM_NAME` | `GenesysCloudToolkit Reports` |

Notes for IT handoff:

- Store `SMTP_PASSWORD` in Key Vault; reference it from App Service — never commit secrets.
- Ensure the mailbox has **SMTP AUTH** enabled if using authenticated SMTP (Microsoft 365 admin).
- For shared mailboxes, confirm send-as / full-access delegation for `SMTP_USER`.
- Scheduled jobs inherit process env automatically; no `email.env` file required on App Service if all vars are set.

## Personal Gmail (local dev)

See [GMAIL_SETUP.md](GMAIL_SETUP.md) for Gmail app passwords and OAuth.  
Existing `doug.env` with `USER` + `PASSWORD` continues to work unchanged until cutover.

When `SMTP_HOST` is unset and `EMAIL_PROVIDER` is not `office365`, defaults are `smtp.gmail.com:587` with STARTTLS.

## Env variable reference

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `EMAIL_PROVIDER` | No | — | Set `office365` to force O365 defaults |
| `SMTP_HOST` | No | Gmail or O365 per provider | Any SMTP server |
| `SMTP_PORT` | No | `587` | |
| `SMTP_USE_TLS` | No | `true` | STARTTLS on port 587 |
| `SMTP_USER` | Yes* | — | Also accepts legacy `USER` / `GMAIL_USER` |
| `SMTP_PASSWORD` | Yes* | — | Also accepts legacy `PASSWORD` / `GMAIL_APP_PASSWORD` |
| `SMTP_FROM` | No | `SMTP_USER` | Send-as address |
| `FROM_NAME` | No | Toolkit default display name | Shown in From header |

\* Required unless using Gmail API OAuth mode (`--oauth`).

## Scheduled jobs

Weekly/biweekly jobs call `gmail_send.py` with `--env-file` defaulting to `email.env` when present, else `doug.env`.  
Jobs also honor process env (Azure settings) because `gmail_send.py` merges env file + `os.environ`.
