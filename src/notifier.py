"""
notifier.py
===========
Sends one reminder email per due account. Uses smtplib (stdlib) for
transport and Jinja2 to render the message body from
templates/reminder_email.html.

Supports a --dry-run mode (also used automatically if SMTP env vars are
missing) that prints what WOULD be sent instead of actually sending -
this is what lets the whole pipeline be tested without real mail
credentials.
"""

import argparse
import email.utils
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import ssl

from jinja2 import Environment, FileSystemLoader, select_autoescape

from db import get_connection
from expiry_checker import get_due_reminders, mark_sent

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_email(record: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("reminder_email.html")
    return template.render(**record)


def render_expired_alert_email(records: list[dict], extra_context: dict | None = None) -> str:
    """Renders executive HTML alert for all overdue/expired components."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("expired_summary_email.html")
    prepared = []
    for r in records:
        rc = dict(r)
        if "overdue_str" not in rc:
            dl = rc.get("days_left", 0)
            rc["overdue_str"] = f"{abs(dl)} days overdue" if dl < 0 else f"{dl} days remaining"
        prepared.append(rc)
    ctx = {
        "records": prepared,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "total_count": len(prepared),
    }
    if extra_context:
        ctx.update(extra_context)
    return template.render(**ctx)


def render_maintenance_cadence_email(
    schedules: list[dict],
    state: str | None = None,
    extra_context: dict | None = None,
) -> str:
    """Renders executive HTML alert for weekly operational maintenance cadence per state/team."""
    from datetime import date
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("maintenance_cadence_email.html")

    state_names = {"AK": "Alaska (AK)", "ND": "North Dakota (ND)", "NH": "New Hampshire (NH)"}
    state_label = state_names.get(state, f"State {state}") if state else "Fleet-Wide (All States)"

    today_name = datetime.now().strftime("%A").lower()
    today_dt = date.today().isoformat()

    team_colors = {
        "Core": "#0284c7",
        "Cognos": "#4f46e5",
        "Letters": "#059669",
        "App Server": "#d97706",
        "Informatica": "#ea580c",
    }

    prepared = []
    active_today_count = 0
    team_counts = {}

    for s in schedules:
        if state and s.get("state") != state:
            continue
        sc = dict(s)
        team = sc.get("team", "Core")
        team_counts[team] = team_counts.get(team, 0) + 1
        days_str = str(sc.get("days_of_week", "")).lower()
        is_today = today_name in days_str or (sc.get("next_run_date") == today_dt)
        if is_today:
            active_today_count += 1
        sc["is_today"] = is_today
        sc["team_color"] = team_colors.get(team, "#0f172a")
        prepared.append(sc)

    ctx = {
        "schedules": prepared,
        "state": state,
        "state_label": state_label,
        "total_schedules": len(prepared),
        "active_today_count": active_today_count,
        "team_counts": team_counts,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "recipients_str": "Designated State & Infrastructure Leads",
    }
    if extra_context:
        ctx.update(extra_context)
    return template.render(**ctx)


def send_real_smtp_email(
    smtp_config: dict,
    to_emails: list[str] | str,
    subject: str,
    html_body: str,
    item_count: int = 1,
) -> dict:
    """
    Transmits an authentic email message across the network via SMTP.
    Supports STARTTLS (default port 587) and SSL (port 465).
    Returns a delivery receipt with server status, message ID, and timestamp.
    """
    if isinstance(to_emails, str):
        recipients = [e.strip() for e in to_emails.split(",") if e.strip()]
    else:
        recipients = [e.strip() for e in to_emails if e and e.strip()]

    if not recipients:
        raise ValueError("Recipient list cannot be empty.")

    # Guard against sending to mock or placeholder domains
    for r in recipients:
        if "@example.com" in r.lower() or "@ets.internal" in r.lower():
            raise ValueError(
                f"Attempted to send real email to mock address: {r}. "
                "Real SMTP dispatch must target valid operational addresses only."
            )

    host = smtp_config.get("host")
    port = int(smtp_config.get("port") or 587)
    user = smtp_config.get("user")
    password = smtp_config.get("password")
    from_addr = smtp_config.get("from_addr") or user

    if not host:
        raise ValueError("SMTP host is missing. Please configure SMTP_HOST.")
    if not user or not password:
        raise ValueError(
            "SMTP credentials missing. Please configure username and password/app password."
        )

    # Sanitize headers against CRLF injection
    clean_subject = subject.replace("\r", "").replace("\n", "")
    clean_from = from_addr.replace("\r", "").replace("\n", "")
    clean_recipients = [r.replace("\r", "").replace("\n", "") for r in recipients]

    msg = MIMEMultipart("alternative")
    domain = host if "." in host else "ets.watchtower"
    msg_id = email.utils.make_msgid(domain=domain)
    now_utc = datetime.now(timezone.utc)

    msg["Message-ID"] = msg_id
    msg["Date"] = email.utils.formatdate(now_utc.timestamp(), localtime=False)
    msg["Subject"] = clean_subject
    msg["From"] = clean_from
    msg["To"] = ", ".join(clean_recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Explicit TLS 1.2+ context for hardened transport security
    ssl_context = ssl.create_default_context()
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    # Select SSL or standard SMTP with STARTTLS
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl_context)
    else:
        server = smtplib.SMTP(host, port, timeout=30)

    try:
        server.ehlo()
        if port != 465:
            server.starttls(context=ssl_context)
            server.ehlo()

        server.login(user, password)
        refused = server.sendmail(clean_from, clean_recipients, msg.as_string())

        status_code = "250 2.0.0 OK: Delivered"
        try:
            code, resp = server.noop()
            if code == 250:
                status_code = f"{code} {resp.decode('utf-8', errors='ignore').strip() or 'OK: Delivered'}"
        except Exception:
            pass

        server.quit()

        return {
            "status": "DELIVERED",
            "message_id": msg_id,
            "smtp_response": status_code,
            "refused_recipients": refused,
            "recipients": recipients,
            "from_addr": from_addr,
            "host": f"{host}:{port}",
            "timestamp": now_utc.isoformat(),
            "subject": subject,
            "item_count": item_count,
        }
    except Exception:
        try:
            server.close()
        except Exception:
            pass
        raise


def send_api_email(
    api_key: str,
    to_emails: list[str] | str,
    subject: str,
    html_body: str,
    from_addr: str | None = None,
    service: str = "auto",
) -> dict:
    """
    Transmits an email via HTTPS REST API (Port 443) using Resend or Brevo.
    Completely bypasses cloud firewall restrictions on outbound SMTP ports (25, 465, 587).
    """
    import json
    import urllib.request

    if isinstance(to_emails, str):
        recipients = [e.strip() for e in to_emails.split(",") if e.strip()]
    else:
        recipients = [e.strip() for e in to_emails if e and e.strip()]

    if not recipients:
        raise ValueError("Recipient list cannot be empty.")

    clean_subject = subject.replace("\r", "").replace("\n", "")
    now_utc = datetime.now(timezone.utc)

    # Auto-detect service: Resend keys start with 're_', Brevo keys start with 'xkeysib-'
    is_resend = service == "resend" or api_key.startswith("re_") or "RESEND" in service.upper()

    if is_resend:
        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "ETS-Watchtower/1.0 (Enterprise Expiry Alert System)",
        }
        sender = from_addr if (from_addr and "@" in from_addr and not from_addr.endswith(".internal")) else "onboarding@resend.dev"
        payload = {
            "from": sender,
            "to": recipients,
            "subject": clean_subject,
            "html": html_body,
        }
    else:
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "ETS-Watchtower/1.0 (Enterprise Expiry Alert System)",
        }
        sender_email = from_addr if (from_addr and "@" in from_addr and not from_addr.endswith(".internal")) else "alerts@ets-watchtower.com"
        payload = {
            "sender": {"email": sender_email, "name": "ETS Watchtower"},
            "to": [{"email": r} for r in recipients],
            "subject": clean_subject,
            "htmlContent": html_body,
        }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=30) as resp:
        resp_body = resp.read().decode("utf-8")
        msg_id = ""
        try:
            parsed = json.loads(resp_body)
            msg_id = parsed.get("id") or parsed.get("messageId") or ""
        except Exception:
            pass

        return {
            "status": "DELIVERED",
            "message_id": msg_id,
            "smtp_response": f"200 OK (HTTPS REST API Port 443 - {'Resend' if is_resend else 'Brevo'})",
            "refused_recipients": [],
            "recipients": recipients,
            "from_addr": sender if is_resend else sender_email,
            "host": "api.resend.com:443" if is_resend else "api.brevo.com:443",
            "timestamp": now_utc.isoformat(),
            "subject": clean_subject,
            "delivery_channel": "HTTPS_REST_API",
        }


def send_flexible_email(
    smtp_config: dict | None,
    to_emails: list[str] | str,
    subject: str,
    html_body: str,
    item_count: int = 1,
) -> dict:
    """
    Intelligently routes email dispatch:
    - If RESEND_API_KEY or BREVO_API_KEY is configured in env or smtp_config, routes via HTTPS API (Port 443).
    - Otherwise falls back to direct SMTP transport (ports 587/465).
    """
    load_env()
    api_key = (
        (smtp_config.get("api_key") if smtp_config else None)
        or os.environ.get("RESEND_API_KEY")
        or os.environ.get("BREVO_API_KEY")
    )
    if api_key:
        from_addr = (smtp_config.get("from_addr") if smtp_config else None) or os.environ.get("SMTP_FROM")
        return send_api_email(
            api_key=api_key,
            to_emails=to_emails,
            subject=subject,
            html_body=html_body,
            from_addr=from_addr,
        )
    return send_real_smtp_email(smtp_config or smtp_config_from_env(), to_emails, subject, html_body, item_count)


def send_email(smtp_config: dict, to_email: str, subject: str, html_body: str):
    """Backwards-compatible wrapper routing to flexible mail transport."""
    return send_flexible_email(smtp_config, [to_email], subject, html_body)


def dispatch_expired_alert_real(
    recipients: list[str] | str,
    expired_records: list[dict],
    smtp_config: dict | None = None,
    is_simulation: bool = False,
) -> dict:
    """
    Compiles and transmits a live alert email for all expired/overdue items
    strictly to the designated test recipients via SMTP or HTTPS REST API.
    """
    if smtp_config is None:
        smtp_config = smtp_config_from_env()

    # Format records with overdue duration
    prepared = []
    for r in expired_records:
        days_left = r.get("days_left", 0)
        overdue_days = abs(days_left)
        rec_copy = dict(r)
        rec_copy["overdue_str"] = f"{overdue_days} days overdue"
        prepared.append(rec_copy)

    subject = f"[URGENT ACTION] ETS Infrastructure Alert: {len(prepared)} Overdue Component Expirations Detected"
    html_body = render_expired_alert_email(prepared)

    return send_flexible_email(
        smtp_config=smtp_config,
        to_emails=recipients,
        subject=subject,
        html_body=html_body,
        item_count=len(prepared),
    )


def dispatch_cadence_alert_real(
    recipients: list[str] | str,
    schedules: list[dict],
    state: str | None = None,
    smtp_config: dict | None = None,
    is_simulation: bool = False,
) -> dict:
    """
    Compiles and transmits a live weekly maintenance cadence alert email
    differentiated state-wise across all 5 functional teams.
    """
    if smtp_config is None:
        smtp_config = smtp_config_from_env()

    state_names = {"AK": "Alaska (AK)", "ND": "North Dakota (ND)", "NH": "New Hampshire (NH)"}
    state_label = state_names.get(state, f"State {state}") if state else "Fleet-Wide (All States)"

    subject = f"[CADENCE NOTICE] ETS Weekly Maintenance Windows: {state_label} (5 Teams Scheduled)"
    recips_str = ", ".join(recipients) if isinstance(recipients, list) else recipients
    html_body = render_maintenance_cadence_email(schedules, state=state, extra_context={"recipients_str": recips_str})

    target_schedules = [s for s in schedules if not state or s.get("state") == state]

    return send_flexible_email(
        smtp_config=smtp_config,
        to_emails=recipients,
        subject=subject,
        html_body=html_body,
        item_count=len(target_schedules),
    )


def load_env():
    """Loads variables from project root .env if present and not already set in environment."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k not in os.environ:
                    os.environ[k] = v


def smtp_config_from_env() -> dict:
    load_env()
    return {
        "host": os.environ.get("SMTP_HOST"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER"),
        "password": os.environ.get("SMTP_PASSWORD"),
        "from_addr": os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
    }


def subject_for(record: dict) -> str:
    team = record.get("team") or "Cognos"
    component = record.get("component") or "Database Passwords"
    state = record.get("state") or "AK"
    env = record.get("env") or record.get("environment") or "DEV"
    days = record.get("days_left", 0)
    return f"Action Required: {team} {component} Expiring in {days} Days ({state} {env})"


def run(db_path: str, threshold_days: int, dry_run: bool = False) -> int:
    conn = get_connection(db_path)
    due = get_due_reminders(conn, threshold_days)

    smtp_config = smtp_config_from_env()
    really_dry_run = dry_run or not smtp_config["host"]

    sent_count = 0
    for record in due:
        subject = subject_for(record)
        body = render_email(record)

        if really_dry_run:
            tag = "FIRST" if record["is_first_reminder"] else "REPEAT"
            print(f"[DRY RUN][{tag}] would email {record['owner_email']!r} "
                  f"subject={subject!r} (account={record['username']})")
        else:
            send_email(smtp_config, record["owner_email"], subject, body)
            print(f"Sent to {record['owner_email']} for {record['username']}")

        mark_sent(conn, record)
        sent_count += 1

    conn.close()
    return sent_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send (or dry-run) today's reminder emails.")
    parser.add_argument("--db", default="data/expiry.db")
    parser.add_argument("--threshold-days", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be sent instead of sending real email")
    args = parser.parse_args()

    count = run(args.db, args.threshold_days, dry_run=args.dry_run)
    print(f"\n{count} reminder(s) processed.")