# Browser missions and human takeover

## Goal

The CEO should acquire capabilities rather than wait for a hardcoded connector.
For any external system it can choose among documented API integration, bounded
browser operation, manual stakeholder action, outsourced work, or custom build.

## Browser mission contract

The first browser-runtime slice provides a persistent Playwright/Chromium context
per company, an HTTPS/domain boundary, screenshots, manual coordinate clicks,
keyboard input, navigation, and checkpoint detection. Browser profiles persist
in a Docker volume so cookies can survive container replacement.

The cockpit supports human operation and the worker can run an approved Computer
Use mission. It receives a mission, not unrestricted internet access:

- objective and allowed domains;
- allowed read/write actions;
- forbidden actions;
- maximum steps and time;
- whether authentication is already available;
- success evidence and captured screenshots;
- approval class for each side effect.

AI-controlled authenticated browser operation currently requires approval. Sending a message,
submitting a proposal, negotiating, hiring, spending, publishing, accepting terms,
and production changes remain separate governed actions.

After approval, the worker opens an isolated single-domain session, executes each
returned action, captures a fresh screenshot, and returns it as
`computer_call_output`. Every action is audited. The mission stops at
`COMPUTER_USE_MAX_STEPS` (12 by default), before Enter/Return form submission, at
any model safety check, or whenever login, CAPTCHA, or verification is detected.
The page is always treated as untrusted input.

## Human takeover

When a browser reaches login, CAPTCHA, 2FA, identity verification, terms, payment
details, or another account-owner checkpoint, it stops. The dashboard shows:

- why the CEO needs help;
- the exact HTTP(S) URL;
- numbered steps;
- evidence the stakeholder should return;
- **Done** and **Can't complete** outcomes.

The human opens the site in their normal browser, performs the checkpoint, and
records the outcome. For sites that work in headless Chromium, the human can also
use the dashboard cockpit's screenshot, click, type, and Enter controls. That
outcome becomes canonical CEO context and wakes the
worker. CAPTCHA must never be bypassed, outsourced to a solving service, or
misrepresented as completed.

## Outsourcing sequence

The intended sequence is deliberately separated:

1. Detect a capability or quality gap from evidence.
2. Research internal fixes, tools, agencies, and freelancers.
3. Produce a ranked shortlist with portfolio evidence, price assumptions, risks,
   and a recommended negotiation range.
4. Request approval before contacting anyone.
5. Draft or send bounded outreach after approval.
6. Return competing offers to the CEO/stakeholder.
7. Require approval for hiring, spend, terms, and contracts.

This lets the CEO be resourceful without granting it authority to impersonate the
stakeholder, create accounts under false identity, accept platform terms, or spend
capital silently.
