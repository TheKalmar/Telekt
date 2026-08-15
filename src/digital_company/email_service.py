"""SMTP delivery and signed, recipient-specific approval links."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote, urlparse

from digital_company.integration_connectors import secret_name
from digital_company.models import TaskProposal
from digital_company.runtime_secrets import get_secret
from digital_company.content_rendering import render_content_review, sanitize_article_html


@dataclass(frozen=True)
class SMTPTransport:
    """Resolved transport containing secrets only for the lifetime of one send."""

    host: str
    port: int
    security: str
    authentication: str
    username: str
    password: str
    sender: str
    public_url: str


def approval_token(company_id: str, approval_id: str, recipient: str, expires: int) -> str:
    secret = os.getenv("APPROVAL_SIGNING_SECRET", "") or get_secret("APPROVAL_SIGNING_SECRET") or ""
    if not secret:
        raise RuntimeError("APPROVAL_SIGNING_SECRET is not configured")
    body = f"{company_id}:{approval_id}:{recipient.lower()}:{expires}".encode()
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_approval_token(company_id: str, approval_id: str, recipient: str, expires: int, token: str) -> bool:
    if expires < int(time.time()):
        return False
    try:
        expected = approval_token(company_id, approval_id, recipient, expires)
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, token)


class ApprovalMailer:
    """Send polished approval requests through configured SMTP infrastructure."""

    @staticmethod
    def _transport(settings: dict, require_callback: bool = True) -> SMTPTransport:
        connection = settings.get("smtp_connection")
        if connection:
            if connection.get("adapter") != "smtp" or connection.get("status") != "ready":
                raise RuntimeError("Selected SMTP connection is not ready")
            parsed = urlparse(connection["base_url"])
            config = connection.get("config") or {}
            security = str(config.get("security") or (
                "ssl" if parsed.scheme == "smtps" else "starttls"
            )).lower()
            authentication = str(config.get("authentication", "password")).lower()
            username = get_secret(secret_name(connection["id"], "username")) or ""
            password = get_secret(secret_name(connection["id"], "password")) or ""
            if authentication == "password" and (not username or not password):
                raise RuntimeError("Selected SMTP connection is missing username or password")
            sender = str(settings.get("from_address", "")).strip()
            public_url = str(settings.get("public_base_url", "")).strip().rstrip("/")
            transport = SMTPTransport(
                host=parsed.hostname or "",
                port=parsed.port or (465 if security == "ssl" else 587),
                security=security,
                authentication=authentication,
                username=username,
                password=password,
                sender=sender,
                public_url=public_url,
            )
        else:
            transport = SMTPTransport(
                host=os.getenv("SMTP_HOST", ""),
                port=int(os.getenv("SMTP_PORT", "587")),
                security="starttls" if os.getenv("SMTP_TLS", "true").lower() == "true" else "plain",
                authentication="password" if os.getenv("SMTP_USERNAME", "") else "none",
                username=os.getenv("SMTP_USERNAME", ""),
                password=os.getenv("SMTP_PASSWORD", ""),
                sender=str(settings.get("from_address") or os.getenv("SMTP_FROM", "")).strip(),
                public_url=str(
                    settings.get("public_base_url") or os.getenv("PUBLIC_BASE_URL", "")
                ).strip().rstrip("/"),
            )
        if not transport.host or not transport.sender:
            raise RuntimeError("SMTP host and sender address are required")
        if require_callback and not transport.public_url:
            raise RuntimeError("A public callback URL is required for approval links")
        return transport

    @staticmethod
    def _deliver(message: EmailMessage, transport: SMTPTransport) -> None:
        smtp_class = smtplib.SMTP_SSL if transport.security == "ssl" else smtplib.SMTP
        with smtp_class(transport.host, transport.port, timeout=15) as smtp:
            if transport.security == "starttls":
                smtp.starttls()
            if transport.authentication == "password":
                smtp.login(transport.username, transport.password)
            smtp.send_message(message)

    def send(self, company_id: str, approval_id: str, proposal: TaskProposal, reason: str, settings: dict) -> int:
        if not settings["enabled"] or not settings["approvers"]:
            return 0
        transport = self._transport(settings)
        sent = 0
        expires = int(time.time()) + int(os.getenv("APPROVAL_LINK_TTL_HOURS", "72")) * 3600
        for recipient in settings["approvers"]:
            token = approval_token(company_id, approval_id, recipient, expires)
            url = f"{transport.public_url}/approval/{quote(company_id)}/{quote(approval_id)}?email={quote(recipient)}&expires={expires}&token={token}"
            msg = EmailMessage()
            msg["Subject"] = f"Approval required: {proposal.title}"
            msg["From"] = f'{settings["sender_name"]} <{transport.sender}>'
            msg["To"] = recipient
            msg.set_content(f"Approval required for {proposal.title}. Review safely at: {url}")
            msg.add_alternative(self._html(proposal, reason, url, settings["sender_name"]), subtype="html")
            self._deliver(msg, transport)
            sent += 1
        return sent

    def send_daily_brief(self, company_id: str, summary: dict, approvals: list[dict], settings: dict) -> int:
        """Send one executive digest with recipient-specific links for every decision."""
        if not settings["enabled"] or not settings["approvers"]:
            return 0
        transport = self._transport(settings)
        sent = 0
        expires = int(time.time()) + int(os.getenv("APPROVAL_LINK_TTL_HOURS", "72")) * 3600
        for recipient in settings["approvers"]:
            cards = []
            plain = []
            for item in approvals:
                proposal = item["proposal"]
                review = item.get("review") or {}
                token = approval_token(company_id, item["id"], recipient, expires)
                url = f"{transport.public_url}/approval/{quote(company_id)}/{quote(item['id'])}?email={quote(recipient)}&expires={expires}&token={token}"
                review_source = str(
                    (review.get("content_package") or {}).get("html_content")
                    or review.get("content", "")
                )[:100_000]
                review_text = html.unescape(re.sub(
                    r"<[^>]+>", " ", sanitize_article_html(review_source),
                ))
                plain.append(
                    f"- {proposal.title}: {url}" +
                    (f"\n\nDRAFT FOR REVIEW\n{review_text}" if review_text else "")
                )
                review_html = render_content_review(review)
                cards.append(
                    f'<div style="background:#0d1219;padding:16px;border-radius:10px;margin:12px 0">'
                    f'<b>{html.escape(proposal.title)}</b><p>{html.escape(proposal.objective)}</p>'
                    f'{review_html}<a style="color:#4ee3a1" href="{html.escape(url)}">'
                    'Review decision — approve, reject, or request changes</a></div>'
                )
            msg = EmailMessage()
            content_review = len(approvals) == 1 and approvals[0]["proposal"].action.value == "publish_content"
            msg["Subject"] = (
                f"Content review: {approvals[0]['proposal'].title} "
                f"[{(approvals[0]['proposal'].work_item_id or approvals[0]['id'])[:8]}]"
                if content_review else f"Daily CEO brief: {settings['sender_name']}"
            )
            msg["From"] = f'{settings["sender_name"]} <{transport.sender}>'
            msg["To"] = recipient
            if content_review:
                work_item_id = approvals[0]["proposal"].work_item_id or approvals[0]["id"]
                domain = (transport.sender.split("@", 1)[-1] or "telekt.local").replace(">", "")
                root_id = f"<telekt-content-{work_item_id}@{domain}>"
                recipient_key = hashlib.sha256(recipient.encode()).hexdigest()[:12]
                msg["Message-ID"] = (
                    f"<telekt-review-{approvals[0]['id']}-{recipient_key}@{domain}>"
                )
                msg["In-Reply-To"] = root_id
                msg["References"] = root_id
                msg["X-Telekt-Work-Item"] = work_item_id
            result_text = "\n".join(f"- {title}" for title in summary["results"]) or "- No new completed work"
            result_html = "".join(f"<li>{html.escape(title)}</li>" for title in summary["results"]) or "<li>No new completed work</li>"
            msg.set_content(f"Status: {summary['control']}\nSpent: EUR {summary['spent']:.2f}\nRemaining: EUR {summary['remaining']:.2f}\nResults:\n{result_text}\nPending approvals: {len(approvals)}\n" + "\n".join(plain))
            msg.add_alternative(f'''<!doctype html><html><body style="background:#0b0f15;color:#eaf1f8;font-family:Arial;padding:28px"><div style="max-width:650px;margin:auto"><div style="color:#4ee3a1">DAILY CEO BRIEF</div><h1>{html.escape(settings["sender_name"])}</h1><p>Status: <b>{html.escape(summary["control"])}</b> · Spent: <b>€{summary["spent"]:.2f}</b> · Remaining: <b>€{summary["remaining"]:.2f}</b></p><h2>Results</h2><ul>{result_html}</ul><h2>Decisions ({len(approvals)})</h2>{''.join(cards) or '<p>No decisions required today.</p>'}<p style="color:#667386;font-size:12px">This is the single routine stakeholder digest for the current 24-hour window.</p></div></body></html>''', subtype="html")
            self._deliver(msg, transport)
            sent += 1
        return sent

    def send_content_review(
        self, company_id: str, summary: dict, approval: dict, settings: dict,
    ) -> int:
        """Deliver one draft as one stable email conversation topic."""
        return self.send_daily_brief(company_id, summary, [approval], settings)

    def send_test(self, recipient: str, settings: dict) -> None:
        """Send only after an operator explicitly presses the SMTP test button."""
        transport = self._transport(settings, require_callback=False)
        msg = EmailMessage()
        msg["Subject"] = f"Telekt SMTP test: {settings['sender_name']}"
        msg["From"] = f'{settings["sender_name"]} <{transport.sender}>'
        msg["To"] = recipient
        msg.set_content(
            "SMTP is configured correctly for this Telekt company. "
            "No approval or company action was performed."
        )
        msg.add_alternative(
            '<div style="font-family:Arial;padding:24px"><h2>SMTP works</h2>'
            '<p>This Telekt company can deliver approval messages. No company action was performed.</p></div>',
            subtype="html",
        )
        self._deliver(msg, transport)

    @staticmethod
    def _html(proposal: TaskProposal, reason: str, url: str, company: str) -> str:
        esc = html.escape
        return f"""<!doctype html><html><body style="margin:0;background:#0b0f15;color:#eaf1f8;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px">
<table width="620" style="max-width:100%;background:#151c26;border:1px solid #2a384b;border-radius:16px"><tr><td style="padding:28px">
<div style="color:#4ee3a1;font-size:12px;font-weight:bold;letter-spacing:1px">{esc(company.upper())} · HUMAN APPROVAL</div>
<h1 style="font-size:24px;margin:14px 0;color:#fff">{esc(proposal.title)}</h1>
<p style="color:#aeb9c7;line-height:1.6">{esc(proposal.objective)}</p>
<table width="100%" style="background:#0d1219;border-radius:10px;margin:20px 0"><tr><td style="padding:16px;color:#cbd5e1">
<b>Action:</b> {esc(proposal.action.value)}<br><b>Estimated cost:</b> €{proposal.estimated_cost_eur:.2f}<br><b>Why approval:</b> {esc(reason)}
</td></tr></table><p style="color:#aeb9c7">Approve or decline on the secure page. A reason is mandatory when declining.</p>
<a href="{esc(url)}" style="display:inline-block;background:#4ee3a1;color:#06140e;text-decoration:none;font-weight:bold;padding:13px 20px;border-radius:9px">Review decision</a>
<p style="font-size:11px;color:#667386;margin-top:24px">The link only opens a review form. No action occurs until you explicitly submit it.</p>
</td></tr></table></td></tr></table></body></html>"""
