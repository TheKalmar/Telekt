"""Legacy single-company command-line interface.

The web control plane is the primary multi-company interface. This CLI remains
useful for smoke tests and direct inspection of the original ``.company`` state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AuthenticationError, PermissionDeniedError, RateLimitError

from digital_company.orchestrator import CompanyOrchestrator
from digital_company.store import CompanyStore

ROOT = Path.cwd()
STATE_DIR = Path(os.getenv("COMPANY_DATA_DIR", str(ROOT / ".company"))).expanduser().resolve()


def store() -> CompanyStore:
    """Open the legacy root company database used by CLI commands."""
    return CompanyStore(STATE_DIR / "company.db")


def main() -> None:
    """Parse a command, execute it, and print machine-readable JSON."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env.local")
    parser = argparse.ArgumentParser(description="Telekt digital-company control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--goal", required=True)
    init.add_argument("--budget", type=float, required=True)
    run = sub.add_parser("run")
    run.add_argument("--max-cycles", type=int, default=8)
    sub.add_parser("status")
    sub.add_parser("approvals")
    approve = sub.add_parser("approve")
    approve.add_argument("approval_id")
    args = parser.parse_args()
    db = store()

    if args.command == "init":
        db.initialize(args.goal, args.budget)
        print(
            json.dumps(
                {"status": "initialized", "goal": args.goal, "budget": args.budget}, indent=2
            )
        )
    elif args.command == "run":
        try:
            result = CompanyOrchestrator(db, STATE_DIR / "artifacts").run(args.max_cycles)
        except RateLimitError as exc:
            code = getattr(exc, "code", None)
            exhausted = code in {"insufficient_quota", "credit_balance_exhausted"}
            result = {
                "status": "api_unavailable",
                "reason": "OpenAI API credits or quota are exhausted"
                if exhausted
                else "OpenAI API rate limit reached",
                "next_action": "Check https://platform.openai.com/settings/organization/billing/",
            }
        except AuthenticationError:
            result = {"status": "api_unavailable", "reason": "OPENAI_API_KEY was rejected"}
        except PermissionDeniedError:
            result = {
                "status": "api_unavailable",
                "reason": "The key or project cannot access the configured model",
            }
        print(json.dumps(result, indent=2))
    elif args.command == "status":
        print(db.snapshot().model_dump_json(indent=2))
    elif args.command == "approvals":
        print(json.dumps(db.list_approvals(), indent=2))
    elif args.command == "approve":
        db.approve(args.approval_id)
        print(json.dumps({"status": "approved", "approval_id": args.approval_id}, indent=2))


if __name__ == "__main__":
    main()
