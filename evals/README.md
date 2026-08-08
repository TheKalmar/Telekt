# CEO behavioral evals

These cases exercise the real `AgentEngine.decide` path and grade stable business
behavior rather than exact wording. Validate fixtures without spending tokens:

```powershell
.\.venv\Scripts\python evals\run_local.py --validate-only
```

Run the live cloud suite only when intentional (five CEO calls):

```powershell
.\.venv\Scripts\python evals\run_local.py --mode cloud
```

The command writes `evals/results/latest.json` and exits non-zero when any case
fails. Local mode is supported, but model capability can make scores differ.
