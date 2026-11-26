"""Reliability evaluation for the coach.

Four experiments, each answering one question a skeptical reviewer would ask:

1. **Autoplay** -- if a player follows the coach every turn, do they win, and
   in how many attempts? Run exhaustively: every secret in every difficulty.
2. **Fault injection** -- when the model misbehaves in a specific way (bad
   JSON, ruled-out guess, leaked secret, fake citation, crash...), do the
   guardrails catch it, and is the advice the player sees still safe?
3. **Consistency** -- does the same position get the same advice every time?
4. **Retrieval** -- for hand-labelled situations, is the right passage in the
   top 3 results?

``scripts/evaluate.py`` runs all four and writes ``docs/evaluation_report.md``.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass

from coach.agent import MODE_ACCEPTED, MODE_FALLBACK, MODE_REPAIRED, CoachAgent
from coach.llm import CoachPrompt, OfflineProvider, ProviderError
from coach.planner import GameObservation, make_plan, retrieval_query
from coach.retriever import Retriever
from logic_utils import DIFFICULTIES, TOO_HIGH, TOO_LOW, WIN, check_guess


class ScriptedProvider:
    """Returns pre-written responses in order; used to simulate a faulty LLM.

    Each item is either a string (returned as the model's raw output) or a
    callable ``prompt -> str`` or an Exception instance (raised). When the
    script runs out, the last item repeats.
    """

    def __init__(self, script, name: str = "scripted"):
        self.script = list(script)
        self.name = name
        self.calls: list[CoachPrompt] = []

    def generate(self, prompt: CoachPrompt) -> str:
        self.calls.append(prompt)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item(prompt) if callable(item) else item


def _good(prompt: CoachPrompt) -> str:
    return OfflineProvider().generate(prompt)


def _with(**overrides):
    """A response that is good except for the overridden fields."""

    def make(prompt: CoachPrompt) -> str:
        data = json.loads(_good(prompt))
        data.update(
            {k: (v(prompt) if callable(v) else v) for k, v in overrides.items()}
        )
        return json.dumps(data)

    return make


# name -> (bad response, what it simulates)
FAULTS = {
    "invalid_json": ("Sure! I think you should guess 50.", "chatty non-JSON reply"),
    "markdown_fenced_json": (
        lambda p: "```json\n" + _good(p) + "\n```",
        "valid JSON wrapped in a markdown fence (should be ACCEPTED)",
    ),
    "out_of_game_range": (
        _with(suggested_guess=999),
        "guess outside the difficulty range",
    ),
    "already_ruled_out": (
        _with(
            suggested_guess=lambda p: (
                p.plan.live_high + 1
                if p.plan.live_high < p.observation.high
                else p.plan.live_low - 1
            )
        ),
        "guess the hints already eliminated",
    ),
    "repeats_previous_guess": (
        _with(suggested_guess=lambda p: p.observation.history[-1][0]),
        "re-suggests the last guess",
    ),
    "claims_to_know_secret": (
        _with(explanation="The secret number is 42, trust me."),
        "hallucinated / leaked secret",
    ),
    "fabricated_source": (
        _with(sources=["strategy_guide#page-7"]),
        "cites a passage it was never given",
    ),
    "no_sources": (_with(sources=[]), "ungrounded answer with no citation"),
    "overconfident": (_with(confidence=1.7), "confidence outside 0-1"),
    "too_long": (
        _with(explanation="Halve the interval. " * 40),
        "rambling explanation",
    ),
    "decimal_guess": (_with(suggested_guess=37.5), "non-integer guess"),
    "provider_crash": (ProviderError("simulated timeout"), "network/API failure"),
}

# The position used for fault injection: Normal, 60 too high, 20 too low.
FAULT_POSITION = GameObservation(1, 100, 8, ((60, TOO_HIGH), (20, TOO_LOW)), "Normal")


@dataclass
class FaultResult:
    name: str
    simulates: str
    first_try_caught: bool
    final_mode: str
    final_guess: int | None
    final_safe: bool
    errors: list[str]


def run_fault_injection(retriever: Retriever, repair: bool = True) -> list[FaultResult]:
    """Inject each fault once. With ``repair=True`` the second call is good,
    so a caught fault should end in MODE_REPAIRED; a provider crash cannot be
    repaired and should end in MODE_FALLBACK."""
    plan = make_plan(FAULT_POSITION)
    results = []
    for name, (bad, desc) in FAULTS.items():
        script = [bad, _good] if repair else [bad]
        agent = CoachAgent(ScriptedProvider(script, name), retriever)
        advice = agent.advise(FAULT_POSITION)
        first_check = next((t for t in advice.trace if t["step"] == "check"), None)
        caught = (first_check is not None and not first_check["ok"]) or any(
            t["step"] == "generate" and "error" in t for t in advice.trace
        )
        g = advice.suggested_guess
        safe = (
            g is not None
            and plan.contains(g)
            and g not in {h for h, _ in FAULT_POSITION.history}
            and "secret number is" not in advice.explanation.lower()
        )
        results.append(
            FaultResult(
                name, desc, caught, advice.mode, g, safe, advice.guardrail_errors
            )
        )
    return results


@dataclass
class AutoplaySummary:
    difficulty: str
    games: int
    wins: int
    mean_attempts: float
    max_attempts: int
    attempt_limit: int
    modes: dict
    mean_confidence: float
    min_confidence: float


def run_autoplay(agent: CoachAgent, difficulties=None) -> list[AutoplaySummary]:
    """Follow the coach every turn against EVERY possible secret."""
    out = []
    for diff in difficulties or DIFFICULTIES:
        low, high, limit = DIFFICULTIES[diff]
        attempts_per_game, wins, confs = [], 0, []
        modes = {MODE_ACCEPTED: 0, MODE_REPAIRED: 0, MODE_FALLBACK: 0}
        for secret in range(low, high + 1):
            history: list[tuple[int, str]] = []
            while len(history) < limit:
                advice = agent.advise(
                    GameObservation(low, high, limit, tuple(history), diff)
                )
                modes[advice.mode] = modes.get(advice.mode, 0) + 1
                confs.append(advice.confidence)
                outcome = check_guess(advice.suggested_guess, secret)
                history.append((advice.suggested_guess, outcome))
                if outcome == WIN:
                    wins += 1
                    break
            attempts_per_game.append(len(history))
        out.append(
            AutoplaySummary(
                diff,
                high - low + 1,
                wins,
                round(statistics.mean(attempts_per_game), 2),
                max(attempts_per_game),
                limit,
                modes,
                round(statistics.mean(confs), 3),
                round(min(confs), 2),
            )
        )
    return out


CONSISTENCY_POSITIONS = [
    GameObservation(1, 100, 8, (), "Normal"),
    GameObservation(1, 100, 8, ((50, TOO_LOW), (75, TOO_HIGH)), "Normal"),
    GameObservation(1, 200, 8, ((100, TOO_HIGH), (50, TOO_LOW), (75, TOO_LOW)), "Hard"),
    GameObservation(1, 20, 6, ((10, TOO_HIGH), (10, TOO_HIGH), (3, TOO_LOW)), "Easy"),
]


def run_consistency(agent: CoachAgent, repeats: int = 5) -> list[dict]:
    rows = []
    for obs in CONSISTENCY_POSITIONS:
        guesses = [agent.advise(obs).suggested_guess for _ in range(repeats)]
        rows.append(
            {
                "position": f"{obs.difficulty} {list(obs.history) or 'opening'}",
                "suggestions": guesses,
                "consistent": len(set(guesses)) == 1,
            }
        )
    return rows


# Hand-labelled: (position, a passage id that MUST appear in the top 3).
RETRIEVAL_CASES = [
    (GameObservation(1, 100, 8, (), "Normal"), "binary_search#opening-move"),
    (
        GameObservation(1, 100, 8, ((60, TOO_HIGH),), "Normal"),
        "reading_hints#too-high-means-every-number-at-or-above-the-guess-is-ruled-out",
    ),
    (
        GameObservation(1, 100, 8, ((40, TOO_LOW),), "Normal"),
        "reading_hints#too-low-means-every-number-at-or-below-the-guess-is-ruled-out",
    ),
    (
        GameObservation(1, 100, 8, ((60, TOO_HIGH), (70, TOO_HIGH)), "Normal"),
        "recovery#after-a-wasted-guess",
    ),
    (
        GameObservation(
            1, 200, 8, ((5, TOO_LOW), (10, TOO_LOW), (15, TOO_LOW)), "Hard"
        ),
        "recovery#the-midpoint-is-still-the-best-play-when-behind",
    ),
    (
        GameObservation(
            1,
            20,
            6,
            ((10, TOO_LOW), (15, TOO_LOW), (18, TOO_LOW), (19, TOO_LOW), (12, TOO_LOW)),
            "Easy",
        ),
        "recovery#last-attempt",
    ),
    (
        GameObservation(
            1,
            100,
            8,
            (
                (50, TOO_LOW),
                (75, TOO_LOW),
                (88, TOO_LOW),
                (94, TOO_LOW),
                (97, TOO_LOW),
                (99, TOO_LOW),
            ),
            "Normal",
        ),
        "reading_hints#keep-track-of-the-live-interval",
    ),
]


def evaluate_retrieval(retriever: Retriever, k: int = 3) -> list[dict]:
    rows = []
    for obs, expected in RETRIEVAL_CASES:
        plan = make_plan(obs)
        query = retrieval_query(obs, plan)
        got = [r.id for r in retriever.search(query, k=k)]
        rows.append(
            {
                "situation": list(plan.situation),
                "expected": expected,
                "hit_at_1": bool(got) and got[0] == expected,
                f"hit_at_{k}": expected in got,
                "retrieved": got,
            }
        )
    return rows
