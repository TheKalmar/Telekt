# Email approvals

## Behavior

Each company can enable approval notifications and configure up to 50 approver
email addresses from **Email approvals** in the dashboard. When the Governor
creates an approval, every recipient receives an individual HTML message with a
signed review link.

Opening a link is read-only. This prevents mail security scanners from approving
actions by following links automatically. The recipient must submit Approve or
Decline on the review page. Decline requires a reason; Approve accepts an
optional comment. Comments and the verified recipient address are stored with
the approval and added to canonical stakeholder context for the CEO and
specialists.

The current group policy is first-response-wins. Once one recipient decides, all
other links show the resolved state. Quorum and role-weighted approval are not
implemented yet.

## Local testing with Mailpit

Start the complete stack with the local inbox:

```powershell
.\scripts\up.ps1 -Mode Bundled -Gpu -Mail -Build
```

Open the application at `http://127.0.0.1:8421` and Mailpit at
`http://127.0.0.1:8025`. Configure recipients in **Email approvals**. Mailpit
captures messages locally and never delivers them to the internet.

## Production SMTP configuration

Do not use `compose.email.yaml` in production. Configure these runtime secrets:

```env
APPROVAL_SIGNING_SECRET=a-long-random-secret
APPROVAL_LINK_TTL_HOURS=72
PUBLIC_BASE_URL=https://company.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=service-account
SMTP_PASSWORD=secret
SMTP_FROM=approvals@example.com
SMTP_TLS=true
```

`PUBLIC_BASE_URL` must be reachable by recipients and use HTTPS. Keep the signing
secret stable across app and worker containers. Changing it immediately
invalidates outstanding links.

Recipient lists and sender display names are company state. SMTP credentials and
the signing secret are deployment configuration and are never saved in a company
database or returned by the API.

## Security properties and limits

- Links are HMAC signed, bound to company, approval, recipient, and expiry.
- Each approval is single-use at the database level.
- GET requests never mutate state.
- Decline comments are mandatory and bounded to 4,000 characters.
- Email delivery failure is audited and does not remove the dashboard approval.
- Recipient possession of the forwarded link currently grants decision authority;
  production should add authenticated user identity and match it to the signed
  recipient before enabling financial or production actions.
- External side-effect adapters still require provider idempotency keys.
