# Model reliability

## Failure handling

Every agent uses the Agents SDK runtime retry policy for transient network,
timeout, rate-limit, and selected 5xx failures. Provider replay-safety advice is
included in the policy. Backoff is exponential with jitter and bounded delay.

Structured-output failures are handled separately because malformed JSON is not
a transport failure. The complete run is retried once with an explicit schema
repair instruction. No tool side effects exist inside these model runs; adding
tools later requires reevaluating whether a whole-run replay is safe.

Environment defaults:

```env
MODEL_TIMEOUT_SECONDS=240
MODEL_TRANSIENT_RETRIES=2
STRUCTURED_OUTPUT_RETRIES=1
AGENT_MAX_TURNS=8
```

## Cloud fallback

Cloud fallback is disabled for every company by default. Local mode therefore
cannot silently consume OpenAI tokens. To permit fallback, pause the company,
open **Model settings**, and explicitly select **Use OpenAI after local failure**.
Fallback additionally requires `OPENAI_API_KEY`; without both conditions the
original local error is raised.

Hybrid mode already routes CEO, Development, and web-enabled Research to cloud. Its local specialist
roles can use the same opt-in fallback. Cloud mode has no secondary fallback.

Local-only Research cannot claim online market evidence. It returns a failed
result explaining that no web source was queried. Cloud and Hybrid research use
the hosted Web Search tool and must return at least two distinct HTTP(S) source
URLs; otherwise the result fails the deterministic evidence gate.

## Audit events

- `model.succeeded`: role, provider, repair attempt, and latency.
- `model.structured_output_error`: schema/behavior failure and attempt.
- `model.provider_error`: exhausted provider/network failure.
- `model.cloud_fallback`: explicit transition from local to OpenAI.

Error messages are truncated before persistence. API keys are never included.

## Current limits

The audit records latency but not token usage or exact monetary cost. Circuit
breaking and provider health scoring are not implemented. Fallback is a company
preference, not a per-task budget policy; production must estimate and authorize
cloud cost before fallback execution.
Local structured agents default to `LOCAL_THINKING=false` and
`LOCAL_MAX_OUTPUT_TOKENS=768`. This avoids long DeepSeek reasoning preambles
and keeps Ollama responses inside the configured request timeout. Enable local
thinking deliberately only when latency and output budgets have been raised.
