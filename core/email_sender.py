"""Outbound email (core/database.py's password_reset_tokens ->
api/auth_routes.py's forgot/reset-password routes). Uses stdlib smtplib
against SMTP_HOST/SMTP_USER/SMTP_PASSWORD (config/settings.py) - no
third-party mail provider dependency.

Treats an unset SMTP_HOST as "email not configured" and never raises for
that case (same gate-don't-fabricate pattern core/billing.py uses for an
unset Stripe key) - the reset link is logged instead, so an Owner/Admin
can relay it manually until real SMTP credentials are set. Setting them
activates real delivery with no other code changes.
"""
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CORE_DIR.parent / "config"))

from logger import get_logger
from settings import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL, SMTP_USE_TLS

logger = get_logger("axim.ui", filename="ui.log")


def is_configured():
    return bool(SMTP_HOST)


def send_password_reset_email(to_email, reset_url):
    """Returns {configured, sent}. Never raises for the "not configured"
    case; a real SMTP failure (bad credentials, unreachable host) is
    caught and returned as sent=False rather than crashing the request -
    the caller (api/auth_routes.py) always shows the same generic
    "if that email exists, a link was sent" message either way, so this
    failure is operator-visible (the log line) but never user-visible,
    consistent with not leaking whether an email address has an account."""
    if not is_configured():
        logger.warning(
            "email_sender: SMTP not configured - password reset link for %s: %s",
            to_email, reset_url,
        )
        return {"configured": False, "sent": False}

    message = EmailMessage()
    message["Subject"] = "Reset your AXIM TradeStation password"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "A password reset was requested for your AXIM TradeStation account.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 30 minutes. If you didn't request this, you can ignore this email."
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
        logger.info("email_sender: password reset email sent to %s", to_email)
        return {"configured": True, "sent": True}
    except Exception:
        logger.exception("email_sender: failed to send password reset email to %s", to_email)
        return {"configured": True, "sent": False}
