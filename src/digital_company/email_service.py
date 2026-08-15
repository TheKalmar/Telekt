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

    def send_daily_brief(self, company_id: str, summary: dict, approvals: list[dict], settings: dict) -> int:
        """Send one executive digest with recipient-specific links for every decision."""
        if not settings["enabled"] or not settings["approvers"]:
            return 0
        host, public_url, sender = os.getenv("SMTP_HOST", ""), os.getenv("PUBLIC_BASE_URL", "").rstrip("/"), os.getenv("SMTP_FROM", "")
        if not host or not public_url or not sender:
            raise RuntimeError("SMTP_HOST, SMTP_FROM, and PUBLIC_BASE_URL are required")
        sent = 0
        expires = int(time.time()) + int(os.getenv("APPROVAL_LINK_TTL_HOURS", "72")) * 3600
        for recipient in settings["approvers"]:
            cards = []
            plain = []
            for item in approvals:
                proposal = item["proposal"]
                review = item.get("review") or {}
                token = approval_token(company_id, item["id"], recipient, expires)
                url = f"{public_url}/approval/{quote(company_id)}/{quote(item['id'])}?email={quote(recipient)}&expires={expires}&token={token}"
                review_text = str(review.get("content", ""))[:12_000]
                plain.append(
                    f"- {proposal.title}: {url}" +
                    (f"\n\nDRAFT FOR REVIEW\n{review_text}" if review_text else "")
                )
                review_html = (
                    '<div style="margin:14px 0;padding:14px;background:#111923;border:1px solid #34445a;'
                    'border-radius:8px"><b>Prepared draft</b><pre style="white-space:pre-wrap;word-break:break-word;'
                    'font:12px/1.5 monospace;color:#cbd5e1">' + html.escape(review_text) + '</pre></div>'
                    if review_text else ""
                )
                cards.append(
                    f'<div style="background:#0d1219;padding:16px;border-radius:10px;margin:12px 0">'
                    f'<b>{html.escape(proposal.title)}</b><p>{html.escape(proposal.objective)}</p>'
                    f'{review_html}<a style="color:#4ee3a1" href="{html.escape(url)}">'
                    'Review decision — approve, reject, or request changes</a></div>'
                )
            msg = EmailMessage()
            content_review = len(approvals) == 1 and approvals[0]["proposal"].action.value == "publish_content"
            msg["Subject"] = (
                f"Content ready for review: {approvals[0]['proposal'].title}"
                if content_review else f"Daily CEO brief: {settings['sender_name']}"
            )
            msg["From"] = f'{settings["sender_name"]} <{sender}>'
            msg["To"] = recipient
            result_text = "\n".join(f"- {title}" for title in summary["results"]) or "- No new completed work"
            result_html = "".join(f"<li>{html.escape(title)}</li>" for title in summary["results"]) or "<li>No new completed work</li>"
            msg.set_content(f"Status: {summary['control']}\nSpent: EUR {summary['spent']:.2f}\nRemaining: EUR {summary['remaining']:.2f}\nResults:\n{result_text}\nPending approvals: {len(approvals)}\n" + "\n".join(plain))
            msg.add_alternative(f'''<!doctype html><html><body style="background:#0b0f15;color:#eaf1f8;font-family:Arial;padding:28px"><div style="max-width:650px;margin:auto"><div style="color:#4ee3a1">DAILY CEO BRIEF</div><h1>{html.escape(settings["sender_name"])}</h1><p>Status: <b>{html.escape(summary["control"])}</b> · Spent: <b>€{summary["spent"]:.2f}</b> · Remaining: <b>€{summary["remaining"]:.2f}</b></p><h2>Results</h2><ul>{result_html}</ul><h2>Decisions ({len(approvals)})</h2>{''.join(cards) or '<p>No decisions required today.</p>'}<p style="color:#667386;font-size:12px">This is the single routine stakeholder digest for the current 24-hour window.</p></div></body></html>''', subtype="html")
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
                if os.getenv("SMTP_TLS", "true").lower() == "true": smtp.starttls()
                if os.getenv("SMTP_USERNAME", ""): smtp.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD", ""))
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
