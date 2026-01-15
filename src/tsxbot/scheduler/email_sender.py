"""Email Sender - SMTP-based email delivery.

Supports Gmail, SendGrid, and generic SMTP providers.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """Email configuration from environment."""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    recipients: list[str] = None

    def __post_init__(self):
        if self.recipients is None:
            self.recipients = []

    @classmethod
    def from_env(cls) -> EmailConfig:
        """Load config from environment variables."""
        recipients_str = os.getenv("ALERT_RECIPIENTS", "")
        recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

        smtp_user = os.getenv("SMTP_USER", "")

        return cls(
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=smtp_user,
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            from_address=os.getenv("SMTP_FROM", smtp_user),
            recipients=recipients,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.recipients)


class EmailSender:
    """
    Sends email alerts via SMTP.

    Usage:
        sender = EmailSender()
        sender.send_alert("Signal Alert", "Trade signal generated...")
    """

    def __init__(self, config: EmailConfig | None = None):
        self.config = config or EmailConfig.from_env()

        if not self.config.is_configured:
            logger.warning("Email not configured. Check SMTP_* and ALERT_RECIPIENTS env vars.")

    def send_alert(
        self,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> bool:
        """
        Send an email alert.

        Args:
            subject: Email subject
            body_text: Plain text body (required)
            body_html: Optional HTML body

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.config.is_configured:
            logger.warning("Email not configured, skipping send")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[TSXBot] {subject}"
            msg["From"] = self.config.from_address
            msg["To"] = ", ".join(self.config.recipients)

            # Attach plain text
            part1 = MIMEText(body_text, "plain")
            msg.attach(part1)

            # Attach HTML if provided
            if body_html:
                part2 = MIMEText(body_html, "html")
                msg.attach(part2)

            # Connect and send
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_user, self.config.smtp_password)
                server.sendmail(
                    self.config.from_address,
                    self.config.recipients,
                    msg.as_string(),
                )

            logger.info(f"Email sent: {subject}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def send_signal_packet(self, packet) -> bool:
        """
        Send a signal packet as an email.

        Args:
            packet: SignalPacket object with to_markdown() method

        Returns:
            True if sent successfully
        """
        body_text = packet.to_markdown() if hasattr(packet, "to_markdown") else str(packet)

        # Generate HTML from markdown (simple conversion)
        body_html = self._markdown_to_html(body_text)

        subject = (
            f"Signal: {packet.playbook} {packet.direction.upper()}"
            if packet.should_trade
            else "No Trade Signal"
        )

        return self.send_alert(subject, body_text, body_html)

    def _markdown_to_html(self, md: str) -> str:
        """Simple markdown to HTML conversion."""
        html = md

        # Headers
        import re

        html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
        html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)

        # Bold
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)

        # Lists
        html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)

        # Line breaks
        html = html.replace("\n\n", "</p><p>")
        html = f"<p>{html}</p>"

        # Wrap in basic HTML
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        {html}
        </body>
        </html>
        """
