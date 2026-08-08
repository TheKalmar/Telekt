"""SMTP delivery and signed, recipient-specific approval links."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import quote

from digital_company.models import TaskProposal


def approval_token(company_id: str, approval_id: str, recipient: str, expires: int) -> str:
    secret = os.getenv("APPROVAL_SIGNING_SECRET", "")
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

    def send(self, company_id: str, approval_id: str, proposal: TaskProposal, reason: str, settings: dict) -> int:
        if not settings["enabled"] or not settings["approvers"]:
            return 0
        host = os.getenv("SMTP_HOST", "")
        public_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        sender = os.getenv("SMTP_FROM", "")
        if not host or not public_url or not sender:
            raise RuntimeError("SMTP_HOST, SMTP_FROM, and PUBLIC_BASE_URL are required")
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "")
        password = os.getenv("SMTP_PASSWORD", "")
        use_tls = os.getenv("SMTP_TLS", "true").lower() == "true"
        sent = 0
        expires = int(time.time()) + int(os.getenv("APPROVAL_LINK_TTL_HOURS", "72")) * 3600
        for recipient in settings["approvers"]:
            token = approval_token(company_id, approval_id, recipient, expires)
            url = f"{public_url}/approval/{quote(company_id)}/{quote(approval_id)}?email={quote(recipient)}&expires={expires}&token={token}"
            msg = EmailMessage()
            msg["Subject"] = f"Approval required: {proposal.title}"
            msg["From"] = f'{settings["sender_name"]} <{sender}>'
            msg["To"] = recipient
            msg.set_content(f"Approval required for {proposal.title}. Review safely at: {url}")
            msg.add_alternative(self._html(proposal, reason, url, settings["sender_name"]), subtype="html")
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if use_tls:
                    smtp.starttls()
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
            sent += 1
        return sent

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
