"""Outgoing email (verification and password-reset codes) over SMTP or Brevo.

With ``BREVO_API_KEY`` set, messages go through Brevo's HTTPS API (for hosts
that block outgoing SMTP, such as Render's free plan). ``MAIL_FROM`` is then
required and must be a sender verified in Brevo (``Ancor Capital <you@gmail.com>`` or
just the address).

Otherwise they go over SMTP, configured for Gmail by default:
``MAIL_USERNAME`` is the Gmail address and ``MAIL_PASSWORD`` an *app password*
(Google account → Security → 2-Step Verification → App passwords; the normal
password does not work). ``MAIL_SMTP_HOST``/``MAIL_SMTP_PORT`` (default
smtp.gmail.com:465, SSL) and ``MAIL_FROM`` (default the username) can be
changed.

In tests the messages go to ``app.extensions["mail_outbox"]``. Without any
settings the debug server prints the message to its console (development
only); otherwise sending fails with ``MailError``.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import parseaddr

import requests
from flask import current_app

logger = logging.getLogger(__name__)

OUTBOX_KEY = "mail_outbox"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class MailError(Exception):
    """The message could not be sent."""


def configure(app) -> None:
    app.config.setdefault("MAIL_SMTP_HOST", os.environ.get("MAIL_SMTP_HOST", "smtp.gmail.com"))
    app.config.setdefault("MAIL_SMTP_PORT", int(os.environ.get("MAIL_SMTP_PORT", "465")))
    # Values pasted into a host's dashboard often carry a stray space or newline,
    # which requests refuses in a header (InvalidHeader) and SMTP rejects.
    for key in ("MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_FROM", "BREVO_API_KEY"):
        app.config.setdefault(key, os.environ.get(key, "").strip())
    if app.testing:
        app.extensions[OUTBOX_KEY] = []


def send_mail(to: str, subject: str, body: str) -> None:
    app = current_app
    if app.testing:
        app.extensions[OUTBOX_KEY].append({"to": to, "subject": subject, "body": body})
        return
    if app.config["BREVO_API_KEY"]:
        _send_with_brevo(app.config["BREVO_API_KEY"], app.config["MAIL_FROM"], to, subject, body)
        return
    username, password = app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"]
    if not username or not password:
        if app.debug:
            # Development fallback only: the code is shown on the server console.
            # Printed rather than logged: the migrations' logging config can
            # disable this module's logger at startup.
            print(
                f"[Ancor Capital] MAIL_USERNAME/MAIL_PASSWORD sin configurar; correo a {to} no enviado:\n{subject}\n{body}",
                file=sys.stderr,
                flush=True,
            )
            return
        raise MailError("El envío de correos no está configurado en el servidor.")
    message = EmailMessage()
    message["From"] = app.config["MAIL_FROM"] or username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL(app.config["MAIL_SMTP_HOST"], app.config["MAIL_SMTP_PORT"], context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        logger.error("Sending email to %s failed: %s", to, type(error).__name__)
        raise MailError("No se pudo enviar el correo. Intenta de nuevo en unos minutos.") from error


def _send_with_brevo(api_key: str, sender: str, to: str, subject: str, body: str) -> None:
    name, address = parseaddr(sender)
    if not address:
        logger.error("BREVO_API_KEY is set but MAIL_FROM is empty")
        raise MailError("El envío de correos no está configurado en el servidor.")
    payload = {
        "sender": {"email": address, **({"name": name} if name else {})},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    try:
        response = requests.post(
            BREVO_URL,
            json=payload,
            headers={"api-key": api_key, "accept": "application/json"},
            timeout=20,
        )
    except requests.RequestException as error:
        logger.error("Sending email to %s through Brevo failed: %s", to, type(error).__name__)
        raise MailError("No se pudo enviar el correo. Intenta de nuevo en unos minutos.") from error
    if response.status_code >= 400:
        # Brevo explains rejections (unverified sender, bad key) in "message".
        logger.error(
            "Brevo rejected the email to %s (%s): %s", to, response.status_code, response.text[:300]
        )
        raise MailError("No se pudo enviar el correo. Intenta de nuevo en unos minutos.")


def send_code(to: str, purpose: str, code: str) -> None:
    """Email with a verification or reset code (plain text, in Spanish)."""
    if purpose == "verify":
        subject = "Ancor Capital · Código para verificar tu correo"
        intro = "Usa este código para verificar tu correo en Ancor Capital:"
    else:
        subject = "Ancor Capital · Código para restablecer tu contraseña"
        intro = "Usa este código para crear una contraseña nueva en Ancor Capital:"
    send_mail(
        to,
        subject,
        f"{intro}\n\n    {code}\n\nVence en 15 minutos y solo sirve una vez.\n"
        "Si no lo pediste, ignora este mensaje: nadie puede entrar sin este código.\n\n"
        "Ancor Capital · Información educativa. No es recomendación de inversión ni asesoría financiera.\n",
    )
