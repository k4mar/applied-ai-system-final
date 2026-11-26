"""Show the agent catching and repairing a misbehaving model, step by step.

The real LLM rarely misbehaves on demand, so this script swaps in a scripted
provider whose FIRST answer has a specific fault and whose second answer is
correct. You see exactly what the guardrails reject and what the player gets.

Usage:
    python scripts/guardrail_demo.py                       # default fault
    python scripts/guardrail_demo.py claims_to_know_secret
    python scripts/guardrail_demo.py --no-repair provider_crash
    python scripts/guardrail_demo.py --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    sys.path.insert(0, str(ROOT))
    from coach.agent import CoachAgent
    from coach.cli import print_advice
    from coach.evaluation import FAULT_POSITION, FAULTS, ScriptedProvider, _good
    from coach.logging_setup import configure_logging

    ap = argparse.ArgumentParser()
    ap.add_argument("fault", nargs="?", default="already_ruled_out")
    ap.add_argument("--no-repair", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for name, (_, desc) in FAULTS.items():
            print(f"{name:24} {desc}")
        return 0
    if args.fault not in FAULTS:
        ap.error(f"unknown fault {args.fault!r}; use --list")

    configure_logging()
    bad, desc = FAULTS[args.fault]
    script = [bad] if args.no_repair else [bad, _good]
    agent = CoachAgent(provider=ScriptedProvider(script, f"scripted:{args.fault}"))
    print(f"Injected fault: {args.fault} ({desc})")
    history = list(FAULT_POSITION.history)
    print(f"Position: {FAULT_POSITION.difficulty}, history {history}")
    advice = agent.advise(FAULT_POSITION)
    for t in advice.trace:
        if t["step"] == "generate" and "raw" in t:
            print(f"  model answer #{t['attempt']}: {t['raw']}")
    print_advice(advice)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
