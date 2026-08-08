# Agent Skill Registry

Skills are trusted, versioned execution guidance. They are not credentials,
tools, permissions, approvals, or free-form memory.

Each skill declares:

- stable ID, name, and semantic version;
- allowed specialist roles and action types;
- required tools and permission labels;
- bounded execution instructions;
- observable success criteria;
- operational status: `available`, `disabled`, or `missing_access`.

The CEO sees the catalog in canonical company state and includes up to five
skill IDs in a typed `TaskProposal`. Before specialist execution, the
orchestrator rejects unknown, unavailable, wrong-role, or wrong-action skills.
Only the validated definitions enter the specialist prompt. Assignment is
recorded as `skills.assigned` in the company audit.

Old tasks without skill IDs remain executable: the registry deterministically
selects up to three matching available skills. This compatibility route does
not relax Governor policy.

Read the active catalog with `GET /api/skills`. Operators can change availability
using `PUT /api/skills/{skill_id}/{available|disabled|missing_access}`. Built-in
definition updates are synchronized by stable ID when a company store opens;
operator status is preserved.

Initial skills cover market evidence, build/buy/integrate analysis, lean product
definition, adversarial QA, outreach preparation, safe browser operations, and
ecommerce platform operations.
