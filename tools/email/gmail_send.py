#!/usr/bin/env python3
"""Send email via SMTP (Office 365, Gmail, or generic) or Gmail API OAuth."""
from __future__ import annotations

import argparse
import base64
import mimetypes
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import setup_path, tool_log
from core.paths import OUTPUTS_DIR
from tools.email.config import (
    DEFAULT_FROM_NAME,
    DEFAULT_WORK_TO,
    SmtpConfig,
    default_email_env_file,
    load_env_file,
    smtp_config,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
DEFAULT_CREDENTIALS = Path(__file__).resolve().parents[2] / "gmail_credentials.json"
DEFAULT_TOKEN = Path(__file__).resolve().parents[2] / "gmail_token.json"


def sender_display_name(env_path: Path | None, override: str | None = None) -> str:
    if override and override.strip():
        return override.strip()
    if env_path and env_path.exists():
        env = load_env_file(env_path)
        configured = (env.get("FROM_NAME") or env.get("SENDER_NAME") or "").strip()
        if configured:
            return configured
    return DEFAULT_FROM_NAME


def format_sender(address: str, display_name: str) -> str:
    if display_name:
        return formataddr((display_name, address))
    return address


def build_email_message(
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    attachments: list[Path],
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachments:
        if not path.exists():
            raise FileNotFoundError(path)
        mime_type, _encoding = mimetypes.guess_type(path.name)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return msg


def send_via_smtp(config: SmtpConfig, message: EmailMessage) -> None:
    with smtplib.SMTP(config.host, config.port, timeout=60) as smtp:
        smtp.ehlo()
        if config.use_tls:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(config.user, config.password)
        smtp.send_message(message)


def get_gmail_api_service(credentials_path: Path, token_path: Path, *, force_auth: bool = False):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Gmail API dependencies missing. Install requirements-gmail.txt or use SMTP env config."
        ) from exc

    creds = None
    if not force_auth and token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise SystemExit(f"Missing OAuth client file: {credentials_path}")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_via_gmail_api(service, message: EmailMessage) -> str:
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return str(sent.get("id") or "")


def latest_direct_outbound_report() -> Path | None:
    matches = sorted(OUTPUTS_DIR.glob("direct_outbound_agents_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def default_report_email(report_path: Path) -> tuple[str, str]:
    subject = "Direct Outbound Agents Report (Genesys)"
    body = f"""Hi Doug,

Attached is the corrected direct-outbound agents report from Genesys Cloud.

Window: last 14 days (Jun 26 through Jul 9, 2026)
Filter: outbound voice with no queueId on participant/session/segment
Threshold: more than 100 direct outbound calls per agent

File: {report_path.name}

— Sent via GenesysCloudToolkit email helper
"""
    return subject, body


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Send email via SMTP (O365/Gmail) or Gmail API OAuth")
    parser.add_argument("--to", default=DEFAULT_WORK_TO, help="Recipient email")
    parser.add_argument("--subject", default=None, help="Email subject")
    parser.add_argument("--body", default=None, help="Plain-text body")
    parser.add_argument("--body-file", default=None, help="Read body from a text file")
    parser.add_argument("--attach", action="append", default=[], help="Attachment path (repeatable)")
    parser.add_argument(
        "--report",
        nargs="?",
        const="latest",
        default=None,
        help="Attach latest direct_outbound_agents_*.xlsx from outputs/",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Env file (default: email.env if present, else doug.env)",
    )
    parser.add_argument(
        "--from-name",
        default=None,
        help=f"Display name for From header (default: env FROM_NAME or {DEFAULT_FROM_NAME})",
    )
    parser.add_argument("--oauth", action="store_true", help="Use Gmail API OAuth instead of SMTP")
    parser.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS), help="OAuth client JSON path")
    parser.add_argument("--token", default=str(DEFAULT_TOKEN), help="Saved OAuth token path")
    parser.add_argument("--auth", action="store_true", help="Force browser sign-in (OAuth mode)")
    parser.add_argument("--dry-run", action="store_true", help="Build message only; do not send")
    args = parser.parse_args()

    env_path = Path(args.env_file) if args.env_file else default_email_env_file()
    attachments = [Path(p) for p in args.attach]

    if args.report is not None:
        report = latest_direct_outbound_report() if args.report == "latest" else Path(args.report)
        if not report or not report.exists():
            raise SystemExit("No direct outbound report found in outputs/. Run the report first.")
        attachments.append(report)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
        subject = args.subject or "Message from GenesysCloudToolkit"
    elif args.body:
        body = args.body
        subject = args.subject or "Message from GenesysCloudToolkit"
    elif args.report is not None and attachments:
        subject, body = default_report_email(attachments[-1])
        if args.subject:
            subject = args.subject
    else:
        body = args.body or ""
        subject = args.subject or "Message from GenesysCloudToolkit"

    if args.oauth:
        service = get_gmail_api_service(Path(args.credentials), Path(args.token), force_auth=args.auth)
        profile = service.users().getProfile(userId="me").execute()
        sender_address = profile.get("emailAddress") or "me"
        display_name = sender_display_name(env_path, args.from_name)
        password = ""
        smtp_cfg = None
    else:
        smtp_cfg = smtp_config(env_path, require_credentials=not args.dry_run)
        sender_address = smtp_cfg.from_address
        display_name = args.from_name.strip() if args.from_name and args.from_name.strip() else smtp_cfg.from_name
        tool_log(
            "info",
            f"Using {smtp_cfg.provider} SMTP ({smtp_cfg.host}:{smtp_cfg.port}, TLS={smtp_cfg.use_tls}) "
            f"sender: {format_sender(sender_address, display_name)}",
        )

    from_header = format_sender(sender_address, display_name)
    message = build_email_message(
        sender=from_header,
        to=args.to,
        subject=subject,
        body=body,
        attachments=attachments,
    )
    if args.dry_run:
        provider = "oauth" if args.oauth else (smtp_cfg.provider if smtp_cfg else "smtp")
        tool_log("dry", f"Would send via {provider} from {from_header} to {args.to} with {len(attachments)} attachment(s)")
        return 0

    if args.oauth:
        message_id = send_via_gmail_api(service, message)
        tool_log("ok", f"Sent message id {message_id} from {from_header} to {args.to}")
    else:
        assert smtp_cfg is not None
        send_via_smtp(smtp_cfg, message)
        tool_log("ok", f"Sent email from {from_header} to {args.to}")

    if attachments:
        tool_log("info", "Attachments: " + ", ".join(p.name for p in attachments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
