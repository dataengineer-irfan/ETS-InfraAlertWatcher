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
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from db import get_connection
from expiry_checker import get_due_reminders, mark_sent

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_email(record: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("reminder_email.html")
    return template.render(**record)


def send_email(smtp_config: dict, to_email: str, subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config["from_addr"]
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_config["host"], smtp_config["port"]) as server:
        server.starttls()
        server.login(smtp_config["user"], smtp_config["password"])
        server.sendmail(smtp_config["from_addr"], [to_email], msg.as_string())


def smtp_config_from_env() -> dict:
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