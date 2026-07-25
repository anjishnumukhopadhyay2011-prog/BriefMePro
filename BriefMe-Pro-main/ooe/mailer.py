"""
ooe/mailer.py
~~~~~~~~~~~~~
Lightweight transactional email helper using Python's stdlib smtplib only.
Supports SMTP/STARTTLS (port 587) and SMTP_SSL (port 465).
Call send_email() — it is safe to call even when email is disabled; it logs
and returns False instead of raising.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

_logger = logging.getLogger(__name__)


def send_email(
    settings: dict[str, Any],
    to: str,
    subject: str,
    html: str,
    text: str = "",
) -> bool:
    """
    Send a transactional email.  Returns True on success, False on failure.
    Will not raise — all errors are logged.

    ``settings`` is the top-level config dict; reads settings["email"].
    """
    cfg = (settings.get("email") or {})
    if not cfg.get("enabled"):
        _logger.debug("Email disabled — skipping send to %s: %s", to, subject)
        return False

    host     = str(cfg.get("smtp_host", ""))
    port     = int(cfg.get("smtp_port", 587))
    user     = str(cfg.get("smtp_user", ""))
    password = str(cfg.get("smtp_password", ""))
    from_addr = str(cfg.get("from_address", user))
    from_name = str(cfg.get("from_name", "BriefMe Pro"))

    if not host or not user:
        _logger.warning("Email enabled but smtp_host/smtp_user not configured.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{from_addr}>"
    msg["To"]      = to

    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx) as smtp:
                smtp.login(user, password)
                smtp.sendmail(from_addr, to, msg.as_string())
        else:
            with smtplib.SMTP(host, port) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(user, password)
                smtp.sendmail(from_addr, to, msg.as_string())
        _logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception as exc:
        _logger.error("Failed to send email to %s: %s", to, exc)
        return False


# ── Email templates ─────────────────────────────────────────────────────────

def _base(title: str, body: str) -> str:
    # Light-themed transactional email — dark themes get spam-filed by Gmail
    # heuristics, light themes render reliably in every client (Gmail, Outlook,
    # Apple Mail, Yahoo) and avoid the "this looks phishy" reaction users
    # have to dark-mode-with-neon-CTA emails from anyone they don't already
    # trust. Inline styles only — most clients strip <style>.
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f5f6f8;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1a1d23;-webkit-font-smoothing:antialiased">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f5f6f8;padding:32px 12px">
  <tr><td align="center">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="540" style="max-width:540px;width:100%">
      <tr><td style="padding:0 4px 18px;font-size:14px;font-weight:600;color:#1a1d23;letter-spacing:-0.01em">BriefMe Pro</td></tr>
      <tr><td style="background:#ffffff;border:1px solid #e6e8ec;border-radius:12px;padding:36px 36px 32px">
        {body}
      </td></tr>
      <tr><td style="padding:24px 4px 0;font-size:12px;line-height:1.6;color:#7a808a">
        You're receiving this because an account at BriefMe Pro is registered to this email.
        If that wasn't you, ignore this message — no action will be taken.
        <br><br>
        BriefMe Pro &middot;
        <a href="#" style="color:#7a808a;text-decoration:underline">Privacy</a> &middot;
        <a href="#" style="color:#7a808a;text-decoration:underline">Terms</a>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


_HEADING = (
    'style="margin:0 0 14px;font-size:22px;line-height:1.25;'
    'font-weight:600;letter-spacing:-0.02em;color:#1a1d23"'
)
_PARA = (
    'style="margin:0 0 16px;font-size:15px;line-height:1.55;color:#3d434c"'
)
_BTN = (
    'style="display:inline-block;padding:12px 24px;background:#1a1d23;'
    'color:#ffffff;font-weight:600;font-size:14px;border-radius:8px;'
    'text-decoration:none;letter-spacing:-0.005em;margin:6px 0 18px"'
)
_LINK_FALLBACK = (
    'style="margin:0 0 4px;font-size:12px;color:#7a808a"'
)
_URL_BOX = (
    'style="display:block;font-family:ui-monospace,SF Mono,Menlo,monospace;'
    'font-size:12px;line-height:1.5;word-break:break-all;color:#3d434c;'
    'background:#f5f6f8;border:1px solid #e6e8ec;border-radius:6px;'
    'padding:10px 12px;margin:0 0 18px"'
)


def email_verify_html(verify_url: str, display_name: str = "") -> tuple[str, str]:
    """Returns (subject, html)."""
    greeting = f"Hi {display_name}," if display_name else "Hi,"
    subject  = "Confirm your email for BriefMe Pro"
    body = f"""
    <h1 {_HEADING}>Confirm your email</h1>
    <p {_PARA}>{greeting} you're one click away from your BriefMe Pro account. Confirm this email so we can send you account and billing notifications.</p>
    <p style="margin:0"><a href="{verify_url}" {_BTN}>Confirm email →</a></p>
    <p {_LINK_FALLBACK}>Button not working? Paste this link into your browser:</p>
    <code {_URL_BOX}>{verify_url}</code>
    <p {_PARA}>This link is valid for <strong>24 hours</strong>. After that, sign in and we'll send a fresh one.</p>
    """
    return subject, _base(subject, body)


def password_reset_html(reset_url: str) -> tuple[str, str]:
    """Returns (subject, html)."""
    subject = "Reset your BriefMe Pro password"
    body = f"""
    <h1 {_HEADING}>Reset your password</h1>
    <p {_PARA}>We got a password-reset request for your BriefMe Pro account. If that was you, click below to choose a new one.</p>
    <p style="margin:0"><a href="{reset_url}" {_BTN}>Reset password →</a></p>
    <p {_LINK_FALLBACK}>Button not working? Paste this link into your browser:</p>
    <code {_URL_BOX}>{reset_url}</code>
    <p {_PARA}>This link is valid for <strong>1 hour</strong>. If you didn't request a reset, you can safely ignore this — your password hasn't changed.</p>
    """
    return subject, _base(subject, body)


def welcome_html(display_name: str = "", app_url: str = "") -> tuple[str, str]:
    """Returns (subject, html)."""
    greeting = f"Welcome, {display_name}." if display_name else "Welcome."
    subject  = "Welcome to BriefMe Pro"
    cta = f'<p style="margin:0"><a href="{app_url}" {_BTN}>Open the dashboard →</a></p>' if app_url else ""
    body = f"""
    <h1 {_HEADING}>{greeting}</h1>
    <p {_PARA}>Your account is active. The globe is now ingesting from ~30 sources and personalising as you click around.</p>
    {cta}
    <p {_PARA} style="font-size:13px;color:#7a808a">A few things worth doing first:</p>
    <ul style="margin:0 0 18px;padding-left:20px;font-size:13px;line-height:1.7;color:#3d434c">
      <li>Click any event dot to read the full story and rate it 👍/👎 — that's how the feed learns your taste</li>
      <li>Open the scenario panel for any region to see the AI's read on what happens next</li>
      <li>Set up email alerts in Account → Notifications so big-severity events ping you in real time</li>
    </ul>
    <p {_PARA} style="font-size:13px;color:#7a808a">Reply to this email if anything's broken or confusing — it goes straight to me.</p>
    """
    return subject, _base(subject, body)
