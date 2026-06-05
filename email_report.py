"""
Email helpers for sending generated trading system reports.

The integration uses SMTP so it can run locally, in GitHub Actions, or in a
cloud batch job without tying the scanner to one email provider.
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    sender: str
    recipient: str
    use_tls: bool = True
    use_ssl: bool = False
    timeout_seconds: int = 60


def _env_value(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def load_email_config() -> EmailConfig:
    """Load SMTP email configuration from environment variables or .env."""
    load_dotenv()

    use_ssl = _env_bool("SMTP_USE_SSL", False)
    default_port = 465 if use_ssl else 587
    smtp_host = _env_value("SMTP_HOST")
    smtp_username = _env_value("SMTP_USERNAME")
    smtp_password = _env_value("SMTP_PASSWORD")
    recipient = _env_value("REPORT_EMAIL_TO", "EMAIL_TO")
    sender = _env_value("REPORT_EMAIL_FROM", "EMAIL_FROM", default=smtp_username)

    missing = [
        name
        for name, value in {
            "SMTP_HOST": smtp_host,
            "SMTP_USERNAME": smtp_username,
            "SMTP_PASSWORD": smtp_password,
            "REPORT_EMAIL_TO": recipient,
            "REPORT_EMAIL_FROM": sender,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing email configuration: "
            + ", ".join(missing)
            + ". Add these to .env or cloud secrets."
        )

    return EmailConfig(
        smtp_host=str(smtp_host),
        smtp_port=_env_int("SMTP_PORT", default_port),
        smtp_username=str(smtp_username),
        smtp_password=str(smtp_password),
        sender=str(sender),
        recipient=str(recipient),
        use_tls=_env_bool("SMTP_USE_TLS", not use_ssl),
        use_ssl=use_ssl,
        timeout_seconds=_env_int("SMTP_TIMEOUT_SECONDS", 60),
    )


def _attach_file(message: EmailMessage, path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Attachment not found: {path}")

    content_type, _ = mimetypes.guess_type(str(path))
    if content_type:
        maintype, subtype = content_type.split("/", 1)
    elif path.suffix.lower() in {".md", ".txt", ".log"}:
        maintype, subtype = "text", "plain"
    else:
        maintype, subtype = "application", "octet-stream"

    data = path.read_bytes()
    message.add_attachment(
        data,
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )


def send_email(
    subject: str,
    body: str,
    attachments: Optional[Iterable[str | Path]] = None,
    config: Optional[EmailConfig] = None,
) -> None:
    """Send an email with optional file attachments."""
    email_config = config or load_email_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_config.sender
    message["To"] = email_config.recipient
    message.set_content(body)

    for attachment in attachments or []:
        _attach_file(message, Path(attachment))

    if email_config.use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            email_config.smtp_host,
            email_config.smtp_port,
            timeout=email_config.timeout_seconds,
            context=context,
        ) as smtp:
            smtp.login(email_config.smtp_username, email_config.smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(
        email_config.smtp_host,
        email_config.smtp_port,
        timeout=email_config.timeout_seconds,
    ) as smtp:
        if email_config.use_tls:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(email_config.smtp_username, email_config.smtp_password)
        smtp.send_message(message)


def send_report_email(
    report_path: str | Path,
    subject: str,
    body: str,
    extra_attachments: Optional[Iterable[str | Path]] = None,
    config: Optional[EmailConfig] = None,
) -> None:
    """Send the generated Markdown report as the primary attachment."""
    attachments = [Path(report_path)]
    attachments.extend(Path(path) for path in extra_attachments or [])
    send_email(subject=subject, body=body, attachments=attachments, config=config)
