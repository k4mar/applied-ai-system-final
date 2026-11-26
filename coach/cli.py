"""Command-line interface to the coach, for reproducible demos and logs.

Examples:
    # Ask for advice on a specific position
    python -m coach.cli --difficulty Normal --history "60:high,40:low"

    # Let the coach play a whole round against a pinned secret
    python -m coach.cli --difficulty Hard --secret 137 --autoplay

    # Force a provider (default: auto -> OpenAI if OPENAI_API_KEY is set)
    python -m coach.cli --provider offline --history "50:low"
"""

from __future__ import annotations

import argparse
import sys

from coach.agent import CoachAdvice, CoachAgent
from coach.llm import get_provider
from coach.logging_setup import configure_logging
from coach.planner import GameObservation
from logic_utils import (
    DIFFICULTIES,
    TOO_HIGH,
    TOO_LOW,
    WIN,
    check_guess,
    get_attempt_limit,
    get_range_for_difficulty,
)

_OUTCOME_ALIASES = {
    "high": TOO_HIGH,
    "h": TOO_HIGH,
    "too high": TOO_HIGH,
    "low": TOO_LOW,
    "l": TOO_LOW,
    "too low": TOO_LOW,
    "win": WIN,
    "w": WIN,
}


def parse_history(text: str) -> tuple[tuple[int, str], ...]:
    """Parse ``"60:high,40:low"`` into ``((60, "Too High"), (40, "Too Low"))``."""
    if not text.strip():
        return ()
    pairs = []
    for item in text.split(","):
        guess, _, outcome = item.partition(":")
        key = outcome.strip().lower()
        if key not in _OUTCOME_ALIASES:
            raise argparse.ArgumentTypeError(
                f"Bad history item {item!r}: use GUESS:high, GUESS:low or GUESS:win"
            )
        pairs.append((int(guess.strip()), _OUTCOME_ALIASES[key]))
    return tuple(pairs)


def print_advice(advice: CoachAdvice, show_trace: bool = True) -> None:
    p = advice.plan
    if p is not None:
        print(
            f"  live interval   : {p.live_low}-{p.live_high} ({p.candidates} numbers), "
            f"attempts left {p.attempts_left}, win guaranteed: {p.win_guaranteed}"
        )
    print(f"  coach suggests  : {advice.suggested_guess}")
    print(f"  explanation     : {advice.explanation}")
    print(f"  confidence      : {advice.confidence:.2f}")
    print(f"  sources         : {', '.join(advice.sources) or '-'}")
    print(f"  mode / provider : {advice.mode} / {advice.provider}")
    if advice.guardrail_errors:
        print(f"  guardrail errors: {advice.guardrail_errors}")
    if show_trace:
        print("  agent trace     :")
        for t in advice.trace:
            detail = {k: v for k, v in t.items() if k not in ("step", "raw")}
            print(f"    - {t['step']:<9} {detail}")


def autoplay(agent: CoachAgent, difficulty: str, secret: int, show_trace: bool) -> int:
    """Play a full round taking the coach's advice every turn. Returns exit code."""
    low, high = get_range_for_difficulty(difficulty)
    limit = get_attempt_limit(difficulty)
    if not low <= secret <= high:
        print(
            f"secret {secret} is outside {difficulty} range {low}-{high}",
            file=sys.stderr,
        )
        return 2
    history: list[tuple[int, str]] = []
    print(
        f"{difficulty}: range {low}-{high}, {limit} attempts, secret pinned to {secret}"
    )
    while len(history) < limit:
        obs = GameObservation(low, high, limit, tuple(history), difficulty)
        advice = agent.advise(obs)
        print(f"\n[turn {len(history) + 1}]")
        print_advice(advice, show_trace)
        guess = advice.suggested_guess
        outcome = check_guess(guess, secret)
        history.append((guess, outcome))
        print(f"  >> player guesses {guess} -> {outcome}")
        if outcome == WIN:
            print(f"\nRESULT: won in {len(history)} of {limit} attempts")
            return 0
    print(f"\nRESULT: lost -- secret was {secret}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask the AI Game Coach for advice.")
    parser.add_argument("--difficulty", choices=list(DIFFICULTIES), default="Normal")
    parser.add_argument("--history", default="", help='e.g. "60:high,40:low"')
    parser.add_argument("--secret", type=int, help="pin the secret (with --autoplay)")
    parser.add_argument(
        "--autoplay", action="store_true", help="coach plays a full round"
    )
    parser.add_argument(
        "--provider", choices=["auto", "openai", "offline"], default=None
    )
    parser.add_argument("--no-trace", action="store_true", help="hide the agent trace")
    args = parser.parse_args(argv)

    log_file = configure_logging()
    agent = CoachAgent(provider=get_provider(args.provider))
    print(f"(provider: {agent.provider.name}; log: {log_file.name})")

    if args.autoplay:
        if args.secret is None:
            parser.error("--autoplay needs --secret")
        return autoplay(agent, args.difficulty, args.secret, not args.no_trace)

    low, high = get_range_for_difficulty(args.difficulty)
    obs = GameObservation(
        low,
        high,
        get_attempt_limit(args.difficulty),
        parse_history(args.history),
        args.difficulty,
    )
    print(f"{args.difficulty}: history = {list(obs.history) or 'none'}")
    print_advice(agent.advise(obs), not args.no_trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
