# We Tried to Build an AI Company, Not Another AI Assistant

> Give it a goal, a budget, and boundaries. Then let it decide what work comes next.

The project started with a deliberately uncomfortable prompt:

> “You have a €1,000 budget. Find a B2B problem we can solve with software and try to build a profitable product.”

I did not want a chat window that waited for my next instruction. I wanted a
digital organization that could research a market, reject weak ideas, define a
product, build an MVP, test it, and return only when it needed a decision that
actually belonged to a human.

That idea became **Telekt**.

Telekt is still a proof of concept, not a company-in-a-box. But building it
changed my understanding of autonomous agents. The difficult part was never
getting a model to write a plan or generate code. The difficult part was making
the system continue responsibly after the first impressive demo.

This is the story of the design changes that got us there.

## Act I: the obvious architecture was wrong

My first mental model looked like a tiny org chart: a CEO agent delegates to
Research, Product, Development, QA, and Growth agents. The CEO decides what to
do next, specialists do the work, and the loop repeats.

That sounds autonomous. It is not—at least not by itself.

If the sequence is still “research, write a PRD, build, test,” we have merely
hidden a fixed workflow behind character names. A useful CEO must be able to
say:

- the evidence is weak, so research again;
- the unit economics are bad, so abandon the idea;
- buyers care about the problem but dislike our solution, so change it;
- buying or integrating a platform is cheaper than building one;
- a human or contractor is the right tool for this step.

The first important design principle followed from that realization:

> The model proposes the next job. It does not own the workflow, authority,
> budget, or truth.

Telekt therefore uses a bounded loop: observe durable facts, propose one typed
task, evaluate it deterministically, execute it, validate the result, persist
the evidence, and reconsider.

```text
Observe -> Propose -> Govern -> Execute -> Validate -> Persist -> Reconsider
                         |                         |
                         +-- deny / approval -----+
```

The loop is small on purpose. Autonomy comes from repeatedly choosing the next
useful action, not from generating an enormous plan once.

## Act II: “memory” is not a company database

An early temptation was to let the conversation contain the company. That
works until a process restarts, context becomes too large, two agents disagree,
or somebody asks the uncomfortable question: “Why did it spend that money?”

We moved company facts into PostgreSQL: companies, agents, tasks, decisions,
approvals, budgets, messages, artifacts, connections, and audit events. Model
context became a projection of that state, not the state itself.

Temporal became the durable execution layer. A workflow can wait for hours for
a stakeholder, survive a restart, retry a failed activity, and continue from a
known point. A waiting workflow does not need to burn tokens or poll constantly.

This separation gave every layer one job:

| Layer | Owns |
|---|---|
| Model / Agents SDK | reasoning and typed proposals |
| Governor | permission and budget decisions |
| Temporal | time, retries, signals, and durable progress |
| PostgreSQL | canonical business state and audit history |
| Git / workspace | produced software and artifacts |
| Execution runtimes | constrained access to browsers, files, and services |

It also exposed a less glamorous truth: retries are easy; **idempotent retries**
are hard. At one point an activity retried safely but reused the wrong execution
identity, making a running agent appear idle. At another point duplicate content
tasks quietly inflated context until model output became unreliable. Both bugs
looked like “the AI stopped thinking.” Neither was an AI problem.

The fixes were deterministic: agent-scoped execution keys, frozen task payloads,
atomic claims, uniqueness rules, bounded context, and explicit progress queues.

## Act III: we accidentally built a needy CEO

Permissions were essential from day one:

- research and write code: allowed;
- send customer email or spend meaningful money: approval;
- deploy production: approval;
- sign a contract: denied.

Then we overused approvals.

The dashboard filled with requests that were technically safe but practically
annoying. The “CEO” was asking for help so often that the human had become its
workflow engine. That defeated the entire point.

The better rule was not “ask whenever uncertain.” It was:

> Human attention is scarce executive capital. Spend it only when authority,
> identity, irreversible impact, or genuinely missing information requires it.

We consolidated routine questions into a daily CEO brief, kept direct
intervention for real blockers, and separated three concepts that had been
blurred together:

- an **approval** authorizes a frozen action;
- a **human handoff** asks a person to perform a step the agent cannot perform;
- a **stakeholder directive** changes priorities and supersedes stale work.

Email approvals then became useful rather than noisy: one rendered draft can be
reviewed with context, approved or rejected with a reason, and returned to the
agent as durable evidence.

## Act IV: a smart CEO should not build everything

Our first prototypes loved generating HTML. It looked productive, but a real
CEO would first ask whether Shopify, WordPress, an API, an existing internal
tool, or a contractor was the cheaper route.

That observation produced the build / buy / integrate / delegate gate. Before
commissioning software, an agent can research platforms, compare economics,
request access, or propose outside help.

It also changed the product model completely.

Originally, a “company” implicitly contained one CEO and a pile of abilities.
That did not scale. A law firm content agent, an e-commerce operator, and a SaaS
CEO do not need the same setup, model, tools, or budget.

Telekt now separates four things:

1. **Company** — durable organizational facts and policy.
2. **Agent** — a typed digital employee with a mandate, model, schedule, and limits.
3. **Plugin** — a reusable capability such as WordPress publishing, email, web research, or browser automation.
4. **Connection** — the actual provider endpoint and credentials granted to a plugin.

This means one company can run a strong, expensive CEO model and a cheaper
content model at the same time. Two agents can share a plugin definition while
receiving different connections and permissions. Adding WordPress does not
silently grant a browser, email account, or production publishing authority.

Prompt text describes what an agent should do. Code decides what it **can** do.

## Act V: the browser is a capability, not magic hands

We wanted agents to explore tools and learn new interfaces. Browser automation
helped, but login, CAPTCHA, 2FA, identity checks, terms, and payment forms are
real boundaries—not inconvenient details to “work around.”

The resulting pattern is a resumable cockpit:

1. the agent navigates within an allowlisted scope;
2. it stops at a human-only checkpoint;
3. the stakeholder completes login, CAPTCHA, or identity work;
4. the agent resumes with explicit evidence.

Browser access became an optional plugin. If a content agent has a reliable
WordPress REST connection, it does not need to open an editor or bother a human.
If the plugin is removed, browser readiness disappears from that agent entirely.

That sounds obvious in retrospect. Most good architecture does.

## A concrete test: the law-firm content agent

The most useful test case was not a fictional startup. It was a Content & SEO
agent for a law firm in Banja Luka.

Its job is to research locally relevant legal questions, verify claims against
authoritative sources, prepare structured SEO content, create WordPress drafts,
and send a complete review email. It must not invent laws, deadlines, or
procedures, and it must not publish without the configured approval.

That test forced the platform to handle details that a toy demo avoids:

- persistent editorial rules learned from stakeholder chat;
- multiple topics progressing concurrently;
- categories, tags, metadata, internal links, CTA, FAQ, and SEO checks;
- optional featured-image generation;
- idempotent WordPress drafts instead of duplicate posts;
- full content inside the approval email;
- daily follow-up without an always-running token furnace.

It also produced wonderfully ordinary failures: invalid structured JSON, a
strict-schema mismatch, missing plugin capabilities, stale approvals, and an
agent that reached `stopped` with no visible result. Each one made the platform
less theatrical and more operational.

## What works today

The current proof of concept includes:

- isolated multi-company state;
- independently runnable typed agents;
- per-agent model, token, and estimated spend limits;
- local Ollama and remote model connections;
- reusable least-privilege capability plugins;
- durable Temporal workflows backed by PostgreSQL;
- start, pause, stop, recovery, and stakeholder chat;
- deterministic allow / approve / deny policy;
- email approvals and human handoffs;
- operational telemetry and audit history;
- confined artifact and browser runtimes;
- a real WordPress content pipeline.

It can be run locally with Docker and a local model, in cloud-model mode without
Ollama, or in a hybrid configuration.

## What does not work yet

Telekt is deliberately labeled a POC. Before exposing it as a multi-tenant
production service it needs authentication, tenant authorization, encrypted
secret storage, stronger per-job isolation, backup/restore operations, security
hardening, observability, and adversarial evaluation.

It also does not prove that an autonomous company will make money. It proves a
more foundational point: a goal-driven system can choose and execute useful work
over time while keeping state, cost, authority, and human intervention outside
the model’s imagination.

## What I learned

1. **Autonomy is a control-system property, not a prompt personality.** Calling
   an agent “CEO” does not make it one.
2. **Durability beats cleverness.** The best reasoning is useless if a restart
   loses the decision.
3. **Permissions must be executable.** A rule in a system prompt is not an
   authorization boundary.
4. **Human attention needs a budget too.** Approval spam is a design failure.
5. **Integrating can be smarter than building.** Productive-looking code is not
   always valuable work.
6. **Most “agent intelligence” bugs live outside the model.** Identity,
   idempotency, context size, state projection, and UI truth matter enormously.
7. **A digital company is not one giant agent.** It is a portfolio of bounded
   workers sharing durable facts and governed capabilities.

## Where this goes next

The long-term vision remains unchanged: a human sets the objective, capital,
and boundaries; a digital organization decides how to pursue the objective.

But the route is clearer now. We do not add autonomy by removing controls. We
add it by making state more durable, tools more composable, decisions more
observable, and human intervention rarer and more meaningful.

The code is being prepared in the open because the interesting work is no
longer the demo. It is the messy engineering between “the model can do this” and
“the system can be trusted to keep doing this tomorrow.”

If that problem interests you, start with the [README](README.md), inspect the
[architecture](docs/architecture.md), run the stack locally, and challenge the
assumptions. The best contribution may be a feature. It may also be a failure
case we have not discovered yet.

