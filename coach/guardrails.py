"""Check step of the coach: validate model output before a player sees it.

Every rule here exists because a language model can plausibly break it:
malformed JSON, a guess outside the range, a guess the hints already ruled
out, a made-up citation, a claim to know the secret, or an overconfident score.
A failed check is never shown to the player; the agent either repairs it or
falls back to the planner's exact answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from coach.planner import GameObservation, Plan

MAX_EXPLANATION_CHARS = 400

# Phrases that claim knowledge of the secret. The coach is never given the
# secret, so any such claim is a hallucination -- and if it happened to be
# right, it would be a leak.
_SECRET_CLAIM = re.compile(
    r"\b(secret|answer|hidden number|target)\s+(number\s+)?(is|was|=|equals)\s+\d+",
    re.IGNORECASE,
)
_CERTAINTY_CLAIM = re.compile(
    r"\b(definitely|guaranteed to be|must be)\s+(the\s+)?(number\s+)?\d+\b",
    re.IGNORECASE,
)


@dataclass
class Advice:
    """Model output that passed parsing (not necessarily validation)."""

    suggested_guess: int
    explanation: str
    confidence: float
    sources: list[str]


@dataclass
class GuardrailReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    advice: Advice | None = None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def parse_advice(raw: str) -> tuple[Advice | None, list[str]]:
    """Parse raw model text into :class:`Advice`, collecting schema errors."""
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return None, ["Output was not valid JSON."]
    if not isinstance(data, dict):
        return None, ["Output must be a JSON object."]

    errors = []
    guess = data.get("suggested_guess")
    if isinstance(guess, bool) or not isinstance(guess, (int, float)):
        errors.append("suggested_guess must be a whole number.")
    elif isinstance(guess, float) and guess != int(guess):
        errors.append("suggested_guess must be a whole number, not a decimal.")

    explanation = data.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        errors.append("explanation must be a non-empty string.")

    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        errors.append("confidence must be a number between 0 and 1.")

    sources = data.get("sources")
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        errors.append("sources must be a list of passage id strings.")

    if errors:
        return None, errors
    return (
        Advice(
            suggested_guess=int(guess),
            explanation=explanation.strip(),
            confidence=float(confidence),
            sources=list(sources),
        ),
        [],
    )


def validate(
    raw: str,
    obs: GameObservation,
    plan: Plan,
    allowed_sources: set[str],
) -> GuardrailReport:
    """Run every guardrail. Returns all failures, not just the first."""
    advice, errors = parse_advice(raw)
    if advice is None:
        return GuardrailReport(ok=False, errors=errors)

    g = advice.suggested_guess
    if not (obs.low <= g <= obs.high):
        errors.append(
            f"suggested_guess {g} is outside the game range {obs.low}-{obs.high}."
        )
    elif not plan.contains(g):
        errors.append(
            f"suggested_guess {g} is already ruled out; it must be inside "
            f"{plan.live_low}-{plan.live_high}."
        )
    if g in {guess for guess, _ in obs.history}:
        errors.append(f"suggested_guess {g} repeats an earlier guess.")

    if len(advice.explanation) > MAX_EXPLANATION_CHARS:
        errors.append(f"explanation is longer than {MAX_EXPLANATION_CHARS} characters.")
    claimed = [
        int(re.search(r"\d+", m.group(0)).group(0))
        for pattern in (_SECRET_CLAIM, _CERTAINTY_CLAIM)
        for m in pattern.finditer(advice.explanation)
    ]
    # A claim is only legitimate when the hints have already pinned the
    # interval to that single number -- then it is a deduction, not a leak.
    solved_value = plan.live_low if plan.live_low == plan.live_high else None
    if any(n != solved_value for n in claimed):
        errors.append(
            "explanation claims to know the secret number; the coach cannot know it."
        )

    if not 0.0 <= advice.confidence <= 1.0:
        errors.append(f"confidence {advice.confidence} must be between 0 and 1.")

    if not advice.sources:
        errors.append("sources is empty; cite at least one knowledge passage.")
    unknown = [s for s in advice.sources if s not in allowed_sources]
    if unknown:
        errors.append(f"sources cite passages that were not provided: {unknown}.")

    return GuardrailReport(ok=not errors, errors=errors, advice=advice)
