"""Run repeatable CEO behavior evals through the real AgentEngine decision path."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from graders import grade

from digital_company.agents import AgentEngine
from digital_company.models import CompanySnapshot


def load_cases() -> list[dict]:
    with (Path(__file__).parent / "cases.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--validate-only", action="store_true", help="Validate fixtures without model calls"
    )
    parser.add_argument("--mode", choices=["cloud", "local", "hybrid"], default="cloud")
    parser.add_argument("--case", help="Run only one case id")
    args = parser.parse_args()
    cases = load_cases()
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            raise SystemExit(f"Unknown case: {args.case}")
    for case in cases:
        CompanySnapshot.model_validate(case["snapshot"])
    if args.validate_only:
        print(json.dumps({"status": "valid", "cases": len(cases)}))
        return 0
    if args.mode in {"cloud", "hybrid"} and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for live cloud evals")
    engine = AgentEngine(
        mode=args.mode, local_model_name=os.getenv("LOCAL_MODEL", "deepseek-company:8b")
    )
    results = []
    for case in cases:
        try:
            proposal = engine.decide(CompanySnapshot.model_validate(case["snapshot"]))
            failures = grade(proposal, case["grader"])
            results.append(
                {
                    "id": case["id"],
                    "passed": not failures,
                    "failures": failures,
                    "proposal": proposal.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            results.append(
                {"id": case["id"], "passed": False, "failures": [f"{type(exc).__name__}: {exc}"]}
            )
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "passed": sum(item["passed"] for item in results),
        "total": len(results),
        "results": results,
    }
    target = Path(__file__).parent / "results" / "latest.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"passed": output["passed"], "total": output["total"], "result": str(target)}))
    return 0 if output["passed"] == output["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
