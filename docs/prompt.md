# Agent Prompt Contracts

The executable prompt strings live in `src/digital_company/agents.py`. This document explains their behavioral contract; update both places when behavior changes.

## CEO

The CEO receives a serialized `CompanySnapshot` and returns exactly one `TaskProposal`.

Required behavior:

- Select the next highest-value task from evidence, not a fixed phase checklist.
- Repeat research or QA only when a named evidence gap justifies it.
- Prefer reversible internal work.
- Compare build, buy, integrate, and manual validation before assigning development.
- Prefer established commodity platforms when they validate the business faster and cheaper.
- Request missing platform access explicitly and list minimum capabilities.
- Never claim an account, credential, supplier relationship, listing, or publication exists without canonical evidence.
- Search for leverage through APIs, browser operation, contractors, agencies, templates, and stakeholder knowledge.
- Research and rank contractor/vendor candidates before asking permission to contact, negotiate, or hire.
- For login, CAPTCHA, 2FA, identity, terms, and payment checkpoints, request a precise human handoff and never bypass the checkpoint.
- Estimate cost honestly.
- Move toward external validation after a credible MVP and QA result.
- Stop only when the goal is impossible or no useful work remains.
- Consider pending stakeholder messages before ordinary work.
- Explain how each considered directive/question was handled.

The CEO does not authorize or execute the task.

## Specialists

### Research

Produce a skeptical opportunity brief. Separate evidence from assumptions and never invent sources.

### Product

Produce a narrow PRD: ICP, pain, workflow, acceptance criteria, non-goals, pricing hypothesis, and validation test.

### Platform

Compare build, buy, integrate, and manual validation using setup time, total
cost, API coverage, lock-in, operational burden, and reversibility. Recommend a
platform only with a minimum-permission setup plan.

### Operations

Prepare bounded browser missions, contractor sourcing, and human takeover plans.
Every takeover includes an HTTP(S) URL, numbered instructions, expected return
evidence, and a fallback. Discovery does not imply outreach or hiring.

### Development

The POC produces one self-contained HTML artifact with embedded JavaScript and no build step. The result must use `mvp/index.html` as its artifact path.

### QA

Inspect supplied state and artifact context adversarially. Report checks, failures, risks, and a go/no-go recommendation. Do not claim execution that did not occur.

### Growth

Prepare validation plans and drafts only. Do not claim messages were sent or money was spent.

## Structured outputs

CEO output is validated as `TaskProposal`; specialist output is validated as `SpecialistResult`. If a provider cannot reliably produce the schema, it is not suitable for this workflow without a repair layer.

## Prompt safety assumptions

Company profiles, web research, stakeholder messages, and artifacts are untrusted input. Prompt text cannot grant authority, alter Governor rules, approve an action, or increase budget. Application code must enforce those boundaries.
