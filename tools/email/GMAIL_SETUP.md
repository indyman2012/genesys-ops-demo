# Personal Gmail sender (to Archwell work inbox)

> **Migrating to work O365?** See [EMAIL_SETUP.md](EMAIL_SETUP.md) for `email.env`, Azure App Service, and Key Vault settings.  
> This doc covers the legacy **personal Gmail** path used on Doug's PC until cutover.

Send toolkit reports from **personal Gmail** to your **work address** when O365 is device-restricted.

## Quick start (recommended): `doug.env` + SMTP

Put credentials in the toolkit root as `doug.env`:

```env
USER=your.personal@gmail.com
PASSWORD=your-16-char-app-password
# Optional — shown in the From header instead of bare address
FROM_NAME=Doug Hendrickson - Cursor Report Engine
```

`PASSWORD` is a [Gmail App Password](https://myaccount.google.com/apppasswords) (not your normal Gmail password).  
2-Step Verification must be enabled on the Google account.

Send the latest direct outbound report:

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python tools/email/gmail_send.py --report --to doug.hendrickson@archwellhealth.com
```

Dry run:

```powershell
python tools/email/gmail_send.py --dry-run --report
```

## Alternative: Gmail API OAuth

If you prefer OAuth instead of SMTP, use `--oauth` and place Desktop OAuth JSON at `gmail_credentials.json`.  
See Google Cloud steps below only if you need this path.

```powershell
pip install -r requirements-gmail.txt
python tools/email/gmail_send.py --oauth --auth --dry-run --report
python tools/email/gmail_send.py --oauth --report
```

### Google Cloud (OAuth only)

1. [Google Cloud Console](https://console.cloud.google.com/) → enable **Gmail API**
2. OAuth consent screen → add your Gmail as test user
3. Credentials → **Desktop app** → save as `gmail_credentials.json`

## Other examples

```powershell
python tools/email/gmail_send.py --env-file doug.env `
  --to doug.hendrickson@archwellhealth.com `
  --subject "Test from toolkit" `
  --body "Report attached." `
  --attach "outputs\direct_outbound_agents_20260710_023054.xlsx"
```

## Notes

- Sends **from personal Gmail**, not `@archwellhealth.com`.
- `doug.env`, `gmail_credentials.json`, and `gmail_token.json` are gitignored — keep them private.
