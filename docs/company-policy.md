# Company policy and approvals

Every company has its own active authorization document in `policy_versions`.
Editing policy never overwrites history: the current row becomes `superseded`
and a new immutable version becomes `active`. The Governor validates this
document in application code before any proposed task can execute.

The configurable fields are:

- actions that require approval;
- actions that are denied;
- the maximum spend one task may make autonomously;
- the number of distinct approval identities required;
- the approval validity window, from 1 to 720 hours.

`sign_contract` is an invariant deny rule. Even if it is omitted through the
API or removed from stored data, the Governor still denies it. Estimated cost
above the remaining company budget is also always denied.

## Quorum lifecycle

Each approval freezes the complete `TaskProposal`, required quorum, creation
time, and expiry. Votes are stored separately in `approval_votes` and are unique
per normalized voter identity. A vote below quorum leaves both approval and task
pending. Reaching quorum atomically promotes the frozen task to `approved` so
the worker can claim it. One rejection closes the proposal immediately and puts
the reason into CEO context.

The default quorum is one, preserving the original workflow. Before user
authentication is added, dashboard votes have the shared identity `dashboard`;
multi-person quorum should therefore use recipient-specific signed email links.

Expired approval authority cannot be exercised. Expiry changes the approval to
`expired`, rejects the waiting task, and emits an audit event. A future CEO cycle
may propose a new action using current evidence and policy.

## API

- `GET /api/policy` returns the active version, document, metadata, and supported
  action identifiers.
- `POST /api/policy` validates a complete document and creates a new version.
- `POST /api/approvals/{id}/approve` records a vote and returns its count/quorum.
- `POST /api/approvals/{id}/reject` requires a reason and closes the approval.

Policy editing is available under **Configuration → Company policy**.
