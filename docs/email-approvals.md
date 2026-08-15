# Email approvals

## Behavior

Each company can enable approval notifications, select a reusable SMTP
connection, and configure up to 50 approver email addresses from
**Configuration -> Email and approvals** in the dashboard. When the Governor
creates an approval, every recipient receives an individual HTML message with a
signed review link.

Opening a link is read-only. This prevents mail security scanners from approving
actions by following links automatically. The recipient must submit Approve or
Decline on the review page. Decline requires a reason; Approve accepts an
optional comment. Comments and the verified recipient address are stored with
the approval and added to canonical stakeholder context for the CEO and
specialists.

The company policy selects a quorum from 1 to 20 distinct approver identities.
Approve records one recipient vote; the frozen task is released only after the
quorum is reached. A rejection closes the proposal immediately. Role-weighted
approval is not implemented.

## Local testing with Mailpit

Start the complete stack with the local inbox:

```powershell
.\scripts\up.ps1 -Mode Bundled -Gpu -Mail -Build
```

Open the application at `http://127.0.0.1:8421` and Mailpit at
`http://127.0.0.1:8025`. The Docker Mail profile preconfigures the
**Deployment .env fallback**, so selecting it is enough for a quick test.

To model the mailbox as a named reusable connection instead, create this
connection from **Email and approvals -> Configure new SMTP connection**:

```text
Name: Local Mailpit
Provider: Mailpit
Location: local
Adapter: SMTP mailbox
Base URL: smtp://mailpit:1025
Capabilities: email.send
Advanced config: {"security":"plain","authentication":"none"}
Username/password: leave empty
```

Select that connection, use `approvals@digital-company.local` as the sender,
and `http://127.0.0.1:8421` as the callback URL. Configure the recipient and
press **Send test email**. Mailpit
captures messages locally and never delivers them to the internet.

## Gmail SMTP from the UI

1. Enable two-step verification on the Google account.
2. Create a Google **App Password** for Telekt. Do not use the normal account
   password.
3. Open **Configuration -> Email and approvals -> Configure new SMTP
   connection** and use:

```text
Name: G&K approval mailbox
Provider: Gmail
Location: cloud
Adapter: SMTP mailbox
Base URL: smtp://smtp.gmail.com:587
Capabilities: email.send
Advanced config: {"security":"starttls","authentication":"password"}
Username: the full Gmail address
Password: the 16-character Google App Password
```

4. Save the connection. Telekt returns to email settings automatically.
5. Select it, set **From** to the same Gmail address, add the approvers, and set
   a public HTTPS callback such as `https://telekt.company.example`.
6. Save, then explicitly press **Send test email**.

Microsoft 365 and other providers use the same flow. Use the SMTP hostname and
port from that provider. Port 587 normally uses `smtp://` with `starttls`; port
465 normally uses `smtps://` with `ssl`.

The SMTP username and password are write-only values in the shared local runtime
vault. They are never returned by the API or shown again in the dashboard.

## Legacy environment fallback

Existing deployments can still configure SMTP through environment values:

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

When email is first enabled through the dashboard, Telekt creates a strong
approval signing secret in the write-only runtime vault if the deployment did
not provide one. Keep the shared data volume stable: replacing it invalidates
outstanding signed links. Recipient lists, selected SMTP connection ID, sender
identity, and callback URL are company state; credentials are not.

## Security properties and limits

- Links are HMAC signed, bound to company, approval, recipient, and expiry.
- Each recipient can vote once; the approval becomes executable once its policy
  quorum is reached.
- Approval authority expires at the policy deadline even if a signed email link
  has a later cryptographic expiry.
- GET requests never mutate state.
- Decline comments are mandatory and bounded to 4,000 characters.
- Email delivery failure is audited and does not remove the dashboard approval.
- Recipient possession of the forwarded link currently grants decision authority;
  production should add authenticated user identity and match it to the signed
  recipient before enabling financial or production actions.
- External side-effect adapters still require provider idempotency keys.
