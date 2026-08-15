# Agent operating cadence

Telekt agents are long-lived digital employees, not one-shot model calls. The
OpenAI Agents SDK performs one typed decision or specialist task. Temporal owns
retries, independent agent workflows, scheduled sleep, and wake-up after a
process restart.

## Lifecycle

`running -> sleeping -> running` is the normal scheduled lifecycle.

- `running`: Temporal may schedule the next bounded agent activity.
- `sleeping`: the agent finished all useful work for this shift and has a
  persisted `next_wake_at`. Temporal wakes it without owner interaction.
- `waiting_approval`: every useful path is blocked by a human decision.
- `waiting_human`: a genuine human-only setup checkpoint is blocking progress.
- `paused`: the owner temporarily paused execution.
- `stopped`: the owner stopped the agent, its model budget is exhausted, or a
  non-scheduled agent made a terminal decision. A content planner's `STOP`
  proposal is translated to `sleeping`; it cannot silently terminate itself.
- `error`: execution exhausted bounded safe recovery.

The current workflow type is `AgentLoopWorkflowV2`. V2 uses a different stable
workflow ID from V1 so old Temporal history is never replayed against the new
timer state machine.

## Content pipeline

Every topic receives a durable `work_item_id`. Tasks, WordPress drafts,
publication approvals, review comments, email headers, and the final published
post retain this identity.

The normal state sequence is:

`researched -> draft_ready -> draft_saved -> awaiting_review -> approved -> published`

A rejection moves only that topic to `changes_requested`. Other topics continue
independently. Published and archived topics no longer count toward the active
pipeline target, so the agent can replenish its queue on a later wake-up.

The following Content & SEO agent settings are editable in Configuration >
Agents:

- `active_topic_target`: parallel topic target; default `5`.
- `wake_interval_minutes`: scheduled wake interval; production default `1440`.
  Use `10` only for a short local test.
- `review_followup_hours`: how long to wait before emailing a pending content
  review again; default `24`.

## Email review threads

Each content work item is delivered as a separate message with a stable subject,
`References`, `In-Reply-To`, and `X-Telekt-Work-Item` headers. Approval-page
comments are canonical stakeholder feedback. A revision produces another email
under the same content work item; routine executive decisions may still be
batched into the daily company brief.

SMTP is send-only. Typing a reply directly into Gmail is not ingested until an
IMAP or Gmail API read adapter is added. Owners should currently use the signed
review link in the email for APPROVE, REJECT, and comments.

## Safe local test

1. Set `wake_interval_minutes` to `10` and keep `active_topic_target` at `5`.
2. Start the Content & SEO agent once.
3. Confirm that distinct topics appear in the agent card and each publication
   review arrives as an individual email.
4. When the active queue is full, confirm status becomes `sleeping` and
   `next_wake_at` is visible.
5. Press Stop if the test should not wake again.
6. Before production, restore `wake_interval_minutes` to `1440`.

Starting the agent can consume model budget and can send configured review
emails. Automated tests use fake model and SMTP implementations and do neither.
