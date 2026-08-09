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
- click-level numbered steps that name the account, project, resource, permission,
  or configuration screen involved;
- evidence the stakeholder should return;
- guided-cockpit and normal-browser paths;
- completed and blocked outcomes with a required written result.

The guided button opens the frozen handoff URL directly in the active company's
isolated cockpit. The cockpit is not an operating-system browser window: the
dashboard renders its live screenshot and sends click/type/Enter actions to the
isolated Chromium runtime. Required product and identity-provider hostnames are
part of the frozen handoff contract. The normal-browser link remains available
for sites that reject headless Chromium.

The returned result becomes canonical CEO context and wakes the worker. A
cancelled handoff also requires the operator to explain the blocker so the CEO
can choose a different route. CAPTCHA must never be bypassed, outsourced to a
solving service, or misrepresented as completed.

If the cockpit reports that the browser runtime is offline, start the complete
stack with `./scripts/up.ps1 -Mode Existing -Build`, or run
`digital-company-browser` in a separate terminal for a native developer setup.

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
