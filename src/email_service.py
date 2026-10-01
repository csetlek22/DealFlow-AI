import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(
    to: str,
    subject: str,
    body: str,
):
    """Send an email through the configured Gmail SMTP account."""
    sender = (
        os.getenv("SENDER_EMAIL")
        or os.getenv("sender_email")
    )

    password = (
        os.getenv("SENDER_PASSWORD")
        or os.getenv("sender_password")
    )

    if not sender or not password:
        raise ValueError(
            "SENDER_EMAIL and SENDER_PASSWORD are required."
        )

    if not to.strip():
        raise ValueError(
            "Recipient email is required."
        )

    if not subject.strip():
        raise ValueError(
            "Email subject is required."
        )

    if not body.strip():
        raise ValueError(
            "Email body is required."
        )

    message = MIMEMultipart()

    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject

    message.attach(
        MIMEText(
            body,
            "plain",
            "utf-8",
        )
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
    ) as server:
        server.login(
            sender,
            password,
        )

        server.sendmail(
            sender,
            to,
            message.as_string(),
        )