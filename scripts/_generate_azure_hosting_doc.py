#!/usr/bin/env python3
"""One-off generator for Azure App Service hosting requirements Word doc."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

TODAY = date.today()
STAMP = TODAY.strftime("%Y%m%d")
OUTPUT_PATH = OUTPUTS_DIR / f"azure_hosting_requirements_{STAMP}.docx"


def add_heading(doc: Document, text: str, level: int = 1) -> None:
    doc.add_heading(text, level=level)


def add_bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def add_numbered(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Number")


def add_table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = h
        for p in hdr[i].paragraphs:
            for run in p.runs:
                run.bold = True
    for r_idx, row in enumerate(rows):
        cells = table.rows[r_idx + 1].cells
        for c_idx, val in enumerate(row):
            cells[c_idx].text = val
    doc.add_paragraph()


def build_document() -> Document:
    doc = Document()

    # Title block
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Azure App Service Hosting Requirements")
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = subtitle.add_run("GenesysToolkit")
    sub_run.font.size = Pt(14)
    sub_run.italic = True

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(f"Working document — {TODAY.strftime('%B %d, %Y')}\n").italic = True
    meta.add_run("Prepared for Azure / IT planning call").italic = True

    doc.add_paragraph()
    doc.add_paragraph(
        "Document owner: Doug Hendrickson (Contact Center Technology)\n"
        "Repository: GenesysCloudToolkit (internal Git)\n"
        "Current hosting: Render.com (demo) + Windows Task Scheduler on personal PC (scheduled reports)"
    )

    # 1. Executive Summary
    add_heading(doc, "1. Executive Summary / Purpose")
    doc.add_paragraph(
        "GenesysToolkit is a Python Flask "
        "operations dashboard and reporting toolkit for Genesys Cloud. It provides:"
    )
    add_bullets(
        doc,
        [
            "Queue Performance analytics with OAuth-protected supervisor views and PDF export",
            "Ops tools for telephony alignment, user/role audits, wrap-up reporting, and related admin workflows",
            "Scheduled weekly and biweekly reports emailed to operations stakeholders",
        ],
    )
    doc.add_paragraph(
        "Today the web dashboard runs on Render (free tier, public URL) while scheduled "
        "reports run from Doug's personal PC via Windows Task Scheduler, emailing outputs "
        "through personal Gmail because Archwell O365 is device-restricted on that machine."
    )
    doc.add_paragraph(
        "This document defines requirements to migrate both the hosted UI and scheduled "
        "jobs to work-owned Azure App Service on an internal domain. After migration, "
        "the application becomes an official IT/work product with documented ownership, "
        "secrets management, and a monthly release handoff cadence."
    )

    # 2. Core Platform
    add_heading(doc, "2. Core Platform Requirements")
    add_bullets(
        doc,
        [
            "Azure App Service (Linux preferred) running a Python Flask web application",
            "Git-based deployment (GitHub/Azure DevOps) OR monthly zip deploy per IT preference",
            "Outbound HTTPS to Genesys Cloud API (api.usw2.pure.cloud / login.usw2.pure.cloud)",
            "Environment variables and secrets for OAuth credentials (no client.env files in repo)",
            "Custom internal domain with TLS (e.g., genesys-ops.archwellhealth.com or IT-assigned hostname)",
            "Always-on or equivalent — app must not sleep; cold starts are unacceptable for supervisor use",
        ],
    )

    # 3. Runtime
    add_heading(doc, "3. Runtime Details")
    add_heading(doc, "3.1 Python & Dependencies", level=2)
    add_bullets(
        doc,
        [
            "Python 3.11 or 3.12 (project currently developed on 3.13; confirm App Service runtime support)",
            "Install via: pip install -r requirements.txt",
            "Key packages: Flask, gunicorn, purecloudplatformclientv2, SQLAlchemy, openpyxl, reportlab",
        ],
    )
    add_heading(doc, "3.2 Startup Command", level=2)
    p = doc.add_paragraph()
    p.add_run("Production entry (demo/socialization): ").bold = True
    p.add_run(
        "gunicorn genesys_toolkit_app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1"
    )
    doc.add_paragraph(
        "Note: 600-second timeout is required for analytics queries and on-demand report "
        "generation from the ops UI. Default 30s App Service / gunicorn timeouts will fail."
    )
    p2 = doc.add_paragraph()
    p2.add_run("Future production entry (execute-enabled): ").bold = True
    p2.add_run("gunicorn ops_app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1")

    add_heading(doc, "3.3 Request Timeouts & Long-Running Work", level=2)
    add_bullets(
        doc,
        [
            "App Service request timeout must accommodate 5–10 minute analytics/report runs",
            "Consider async/background job pattern for very large exports if platform limits apply",
            "Queue Performance PDF generation and multi-queue analytics are the primary long-run paths",
        ],
    )
    add_heading(doc, "3.4 Persistent Storage", level=2)
    add_bullets(
        doc,
        [
            "Writable /home or Azure Files mount for outputs/, logs/, and schedule state JSON",
            "Report artifacts (xlsx, csv, html, pdf) must persist across restarts and be downloadable from UI or SharePoint",
            "Optional: Azure Blob Storage for report archive if local disk is ephemeral",
            "If using shared folder today (QUEUE_PERFORMANCE_EXPORT_DIR), map equivalent Azure Files SMB path",
        ],
    )

    # 4. Authentication
    add_heading(doc, "4. Authentication & OAuth Configuration")
    doc.add_paragraph(
        "The app uses two separate Genesys OAuth clients (Admin → Integrations → OAuth):"
    )
    add_table(
        doc,
        ["Client type", "Env vars", "Purpose"],
        [
            [
                "Client Credentials",
                "GENESYS_CLIENT_ID, GENESYS_CLIENT_SECRET",
                "Server-to-server API calls: Queue Performance data, ops tools, scheduled report jobs",
            ],
            [
                "Authorization Code",
                "GENESYS_AGENT_CLIENT_ID, GENESYS_AGENT_CLIENT_SECRET",
                "Supervisor sign-in for /queue-performance and site-picker flows",
            ],
        ],
    )
    add_bullets(
        doc,
        [
            "GENESYS_REGION=usw2 (or GENESYS_CLOUD_REGION=usw2.pure.cloud)",
            "FLASK_SECRET_KEY — random string for Flask session encryption",
            "OPS_DEMO_MODE=1 for preview-only demo deployment (blocks execute actions)",
            "GENESYS_AGENT_REDIRECT_URI must match Azure hostname, e.g.: https://<host>/agent/oauth/callback",
            "Queue Performance allowed roles (default): Supervisor, Admin, Super Admin (configurable via OPS_QUEUE_PERFORMANCE_ROLES)",
        ],
    )
    doc.add_paragraph(
        "Migration action: Register new redirect URIs in Genesys OAuth client before cutover. "
        "Document OAuth reset procedure in IT setup doc (Azure env vars + Genesys Admin steps)."
    )

    # 5. Email & Reports
    add_heading(doc, "5. Email & Report Delivery")
    doc.add_paragraph(
        "Scheduled jobs currently email reports via personal Gmail SMTP (doug.env). "
        "Azure deployment should use work-approved delivery:"
    )
    add_bullets(
        doc,
        [
            "Preferred: Internal SMTP relay (O365 relay or approved SMTP service) with service account",
            "Alternative: Microsoft Graph API sendMail with app registration",
            "Fallback: Write reports to SharePoint/OneDrive folder; notify via distribution list or Teams",
            "Each report job should support configurable multi-recipient TO/CC via env vars or config file",
            "Remove dependency on personal Gmail and personal PC for production delivery",
        ],
    )

    # 6. Scheduled Jobs
    add_heading(doc, "6. Scheduled Jobs (Migrate from Windows Task Scheduler)")
    doc.add_paragraph(
        "Today these jobs run on Doug's personal PC. Target: Azure WebJobs, Logic Apps, "
        "Container Apps cron, or Azure Functions with shared codebase and secrets."
    )
    add_table(
        doc,
        ["Task name", "Schedule", "Job script", "Notes"],
        [
            [
                "GenesysToolkit-WeeklyDirectOutbound",
                "Mon 5:00 AM",
                "jobs/weekly_direct_outbound_report.py",
                "Prior Mon–Sun; emails xlsx",
            ],
            [
                "GenesysToolkit-WeeklySpamAbandon",
                "Mon 6:00 AM",
                "jobs/weekly_spam_abandon_report.py",
                "Prior Mon–Sun; spam/short abandon by queue",
            ],
            [
                "GenesysToolkit-WeeklyWrapupTimeout",
                "Mon 7:00 AM",
                "jobs/weekly_wrapup_timeout_report.py",
                "ININ-WRAPUP-TIMEOUT by agent",
            ],
            [
                "GenesysToolkit-WeeklyRepeatCaller",
                "Mon 8:00 AM",
                "jobs/weekly_repeat_caller_report.py",
                "Repeat inbound callers WoW",
            ],
            [
                "GenesysToolkit-WeeklyCallbackConversion",
                "Mon 8:30 AM",
                "jobs/weekly_callback_conversion_report.py",
                "Callback conversion metrics",
            ],
            [
                "GenesysToolkit-WeeklyQueueStaffingGap",
                "Mon 3:00 AM",
                "jobs/weekly_queue_staffing_gap_report.py",
                "PS queue staffing gap (ongoing)",
            ],
            [
                "GenesysToolkit-WeeklyQueuePerformancePDF",
                "Mon 6:30 AM",
                "jobs/weekly_queue_performance_pdf.py",
                "Prior Mon–Sun PDF; may use shared folder",
            ],
            [
                "GenesysToolkit-BiweeklyAhtTrend",
                "Mon 9:00 AM (biweekly)",
                "jobs/biweekly_aht_trend_report.py",
                "13-day gate; HTML report",
            ],
            [
                "GenesysToolkit-DailyCareNavigator",
                "Daily 6:00 AM",
                "jobs/daily_care_navigator_multi_queue_report.py",
                "Multi-queue care navigator compliance",
            ],
            [
                "GenesysToolkit-Business7dOutbound",
                "Daily 3:00 PM (business days)",
                "scripts/wait_until_3pm_business7d_outbound.ps1",
                "Tactical outbound window report",
            ],
        ],
    )
    doc.add_paragraph(
        "Jobs use JSON state files in logs/ for run counts and biweekly gating. "
        "Azure scheduler must preserve equivalent state on persistent storage."
    )

    # 7. Ops Dashboard
    add_heading(doc, "7. Ops Dashboard Requirements")
    add_bullets(
        doc,
        [
            "All ops tools invoked from the hosted Flask UI must run on Azure, not assume local PC paths",
            "Subprocess tool runs need writable working directory and sufficient timeout",
            "Demo mode (OPS_DEMO_MODE=1) for stakeholder access; production mode requires change control",
            "Queue Performance: OAuth gate + client-credentials backend fetch",
            "PDF export and on-demand reports downloadable from browser",
            "No dependency on C:\\Users\\... personal paths; use PROJECT_ROOT-relative paths only",
        ],
    )

    # 8. Security
    add_heading(doc, "8. Security & Access")
    add_bullets(
        doc,
        [
            "Store secrets in Azure Key Vault; reference via App Service Key Vault references",
            "Internal-only access: VNet integration, private endpoint, or Azure AD app proxy as IT standard",
            "Outbound allow-list: *.pure.cloud (Genesys API), SMTP/Graph endpoints if used",
            "No secrets in Git; rotate OAuth client secrets on schedule",
            "Separate OAuth clients for demo vs production execute mode recommended",
            "Audit logging for tool execute actions in production mode",
        ],
    )

    # 9. Operations & Ownership
    add_heading(doc, "9. Operations & Ownership")
    add_bullets(
        doc,
        [
            "After Azure cutover, app is work-owned — not a personal side project",
            "Monthly zip handoff to IT with deployable release + setup/operations doc",
            "Setup doc must include: Azure env var configuration, Genesys OAuth redirect URI updates, startup command, Python version, pip install, post-deploy smoke test checklist",
            "Smoke tests: login/OAuth, Queue Performance load, sample export, one scheduled job dry-run",
            "Bus-factor / continuity: document runbooks so another IT/admin can redeploy without Doug's PC",
            "Doug continues Git-based dev; IT applies monthly releases to App Service",
        ],
    )

    # 10. Questions for Azure Team
    add_heading(doc, "10. Questions for Azure Team")
    add_numbered(
        doc,
        [
            "Preferred deploy model: Git auto-deploy from Azure DevOps/GitHub vs monthly zip upload?",
            "Job scheduler recommendation: WebJobs cron, Logic Apps, Container Apps, or Functions?",
            "Secrets: Key Vault integration — who provisions and rotates OAuth credentials?",
            "Email: Internal SMTP relay available? Graph API app registration feasible? SharePoint drop folder instead?",
            "Request timeout limits on App Service — can we set 600s+ for analytics endpoints?",
            "Staging slot for pre-production OAuth testing with separate redirect URI?",
            "Persistent storage: Azure Files mount vs Blob — which fits IT standards?",
            "Internal DNS hostname and TLS certificate provisioning timeline?",
            "Network: VNet-only access requirements; any WAF or App Gateway in path?",
            "Python runtime version available on App Service Linux plan?",
            "Single App Service for web + jobs, or split web and worker tiers?",
            "Monitoring/alerting: Application Insights, failed job alerts, uptime checks?",
        ],
    )

    # 11. Decision Branches
    add_heading(doc, "11. Decision Branches / Options")
    add_table(
        doc,
        ["Decision area", "Option A", "Option B", "Option C", "Recommendation"],
        [
            [
                "Deploy model",
                "Git auto-deploy on push",
                "Monthly zip from Doug",
                "CI/CD pipeline with approval gate",
                "Start with zip if Git integration delayed; target CI/CD",
            ],
            [
                "Report email",
                "Internal SMTP relay",
                "Microsoft Graph sendMail",
                "SharePoint folder + DL notification",
                "SMTP or Graph per IT standard; SharePoint fallback",
            ],
            [
                "Recipients",
                "Hard-coded distribution lists",
                "Env var per report",
                "Admin UI config page",
                "Env vars initially; UI config later",
            ],
            [
                "Architecture",
                "Single App Service (web + cron)",
                "Web App + separate worker App Service",
                "Web App + Azure Functions for jobs",
                "Single App Service if cron supported; split if timeout/resource contention",
            ],
            [
                "Storage",
                "Local /home persistent",
                "Azure Files SMB mount",
                "Blob archive + short local cache",
                "Azure Files if reports need file-share semantics",
            ],
            [
                "Access",
                "Public + OAuth gate",
                "Internal VNet only",
                "Azure AD App Proxy",
                "Internal-only per security policy",
            ],
        ],
    )

    # 12. Pre-migration Checklist
    add_heading(doc, "12. Pre-Migration Checklist")
    add_table(
        doc,
        ["#", "Item", "Owner", "Status"],
        [
            ["1", "Azure App Service plan provisioned (always-on)", "IT", "Pending"],
            ["2", "Internal hostname + TLS certificate", "IT", "Pending"],
            ["3", "Key Vault created; OAuth secrets stored", "IT/Doug", "Pending"],
            ["4", "Genesys OAuth redirect URIs updated for Azure hostname", "Doug", "Pending"],
            ["5", "GENESYS_* and FLASK_SECRET_KEY configured in App Service", "IT", "Pending"],
            ["6", "gunicorn startup command with 600s timeout verified", "Doug/IT", "Pending"],
            ["7", "Persistent storage mounted (outputs/, logs/)", "IT", "Pending"],
            ["8", "Email delivery path confirmed (SMTP/Graph/SharePoint)", "IT", "Pending"],
            ["9", "Scheduler configured for all weekly/daily jobs", "IT/Doug", "Pending"],
            ["10", "Smoke test: Queue Performance OAuth login", "Doug", "Pending"],
            ["11", "Smoke test: one scheduled job end-to-end", "Doug", "Pending"],
            ["12", "Setup/operations doc co-authored with IT", "Doug/IT", "Pending"],
            ["13", "Monthly release handoff process agreed", "Doug/IT", "Pending"],
            ["14", "Decommission Render demo after Azure validated", "Doug", "Pending"],
            ["15", "Retire Windows Task Scheduler jobs on personal PC", "Doug", "Pending"],
        ],
    )

    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(
        f"Generated {TODAY.isoformat()} from GenesysCloudToolkit — working doc for Azure planning."
    ).italic = True

    return doc


def main() -> Path:
    doc = build_document()
    doc.save(OUTPUT_PATH)
    print(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    main()
