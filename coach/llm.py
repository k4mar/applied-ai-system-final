"""Generation step of the coach: prompt construction and model providers.

Two providers share one interface:

* :class:`OpenAIProvider` -- a real LLM call (default ``gpt-4o-mini``), used
  when ``OPENAI_API_KEY`` is set and the ``openai`` package is installed.
* :class:`OfflineProvider` -- a deterministic, rule-based stand-in that reads
  the same prompt context and writes JSON in the same schema. It is NOT a
  language model. It exists so the app, the tests and the evaluation harness
  run reproducibly with no API key and no network, and so a grader gets
  identical output to the logs committed in ``docs/``.

Whichever provider runs, its output goes through the same guardrails in
``coach/guardrails.py`` -- the guardrails do not trust either one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from coach.planner import GameObservation, Plan
from coach.retriever import RetrievedPassage

logger = logging.getLogger("coach.llm")

SYSTEM_PROMPT = """You are the AI Coach for a number-guessing game.
You help the player choose their next guess and explain why, in plain language.

Rules you must follow:
1. You do NOT know the secret number. Never claim to know it or reveal it.
2. suggested_guess MUST be a whole number inside the live interval given to you,
   and must not repeat an earlier guess.
3. Base your explanation ONLY on the game facts and the numbered knowledge
   passages provided. Cite the passage ids you used in "sources".
4. Keep the explanation under 60 words, friendly, and specific to this turn.
5. confidence is a number from 0 to 1: how sure you are this is the best guess.

Reply with ONLY a JSON object, no markdown:
{"suggested_guess": <int>, "explanation": "<text>", "confidence": <0-1>,
 "sources": ["<passage id>", ...]}"""


@dataclass
class CoachPrompt:
    """A fully built prompt plus the structured context it was built from."""

    system: str
    user: str
    observation: GameObservation
    plan: Plan
    passages: list[RetrievedPassage]
    repair_feedback: list[str] = field(default_factory=list)


def build_prompt(
    obs: GameObservation,
    plan: Plan,
    passages: list[RetrievedPassage],
    repair_feedback: list[str] | None = None,
) -> CoachPrompt:
    """Render the user message: game facts, retrieved passages, any repair notes."""
    history = (
        ", ".join(f"{g} ({o})" for g, o in obs.history) if obs.history else "none yet"
    )
    guaranteed = "yes" if plan.win_guaranteed else "no"
    lines = [
        (
            f"Difficulty: {obs.difficulty} (range {obs.low}-{obs.high}, "
            f"{obs.attempt_limit} attempts)"
        ),
        f"Guesses so far: {history}",
        (
            "Live interval (numbers that can still win): "
            f"{plan.live_low}-{plan.live_high} ({plan.candidates} numbers)"
        ),
        (
            f"Attempts left: {plan.attempts_left}; a perfect search needs at most "
            f"{plan.guesses_needed}; win guaranteed: {guaranteed}"
        ),
        f"Midpoint of live interval: {plan.optimal_guess}",
    ]
    if plan.wasted_guesses:
        lines.append(
            "Guesses that taught nothing (repeat or already ruled out): "
            + ", ".join(map(str, plan.wasted_guesses))
        )
    lines.append("")
    lines.append("Knowledge passages:")
    for r in passages:
        lines.append(f"[{r.id}] {r.passage.title}: {r.passage.text}")
    if repair_feedback:
        lines.append("")
        lines.append(
            "Your previous answer was rejected by the safety checker for these "
            "reasons. Fix every one and answer again:"
        )
        lines.extend(f"- {msg}" for msg in repair_feedback)

    return CoachPrompt(
        system=SYSTEM_PROMPT,
        user="\n".join(lines),
        observation=obs,
        plan=plan,
        passages=passages,
        repair_feedback=list(repair_feedback or []),
    )


class ProviderError(RuntimeError):
    """The provider could not produce any output (network, auth, timeout...)."""


class OpenAIProvider:
    """Chat-completions call to OpenAI in JSON mode."""

    def __init__(self, model: str | None = None, timeout: float = 20.0):
        try:
            from openai import OpenAI  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ProviderError("openai package is not installed") from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError("OPENAI_API_KEY is not set")
        self.model = model or os.environ.get("COACH_MODEL", "gpt-4o-mini")
        self.name = f"openai:{self.model}"
        self._client = OpenAI(timeout=timeout, max_retries=1)

    def generate(self, prompt: CoachPrompt) -> str:  # pragma: no cover - network
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                max_tokens=300,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
            )
        except Exception as exc:  # any SDK/network failure becomes ProviderError
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        return resp.choices[0].message.content or ""


class OfflineProvider:
    """Deterministic, rule-based stand-in for the LLM. Not a language model.

    It answers from the same context the LLM gets: the planner's facts and the
    retrieved passages. Its explanation is assembled from a template plus the
    top retrieved passage, and it only cites passages it actually received.
    """

    name = "offline-rules"

    def generate(self, prompt: CoachPrompt) -> str:
        plan, obs = prompt.plan, prompt.observation
        guess = plan.optimal_guess
        lo, hi = plan.live_low, plan.live_high

        if not obs.history:
            why = (
                f"Nothing is ruled out yet, so open in the middle of {lo}-{hi}. "
                "Whatever the hint says, half the range is gone."
            )
        elif lo == hi:
            why = f"Only {lo} is left -- the hints have pinned it down."
        else:
            last_guess, last_outcome = obs.history[-1]
            if last_guess in plan.wasted_guesses:
                # Reasoning from a ruled-out guess ("1 was too low, so the secret
                # is above 1") is true but useless; point at the interval instead.
                why = (
                    f"{last_guess} was already ruled out, so it taught nothing new. "
                    f"The secret is still in {lo}-{hi} ({plan.candidates} numbers); "
                    f"{guess} splits them in half."
                )
            else:
                direction = "below" if last_outcome == "Too High" else "above"
                why = (
                    f"{last_guess} was {last_outcome.lower()}, so the secret is "
                    f"{direction} it. {plan.candidates} numbers remain ({lo}-{hi}); "
                    f"{guess} splits them in half."
                )

        if plan.wasted_guesses:
            why += (
                " Stay inside the interval -- repeats and ruled-out numbers"
                " cost a turn."
            )
        if plan.attempts_left == 1:
            why += " Last attempt: any number inside the interval is a fair shot."
        elif not plan.win_guaranteed:
            why += (
                f" A win is no longer guaranteed ({plan.candidates} numbers, "
                f"{plan.attempts_left} attempts), but the midpoint is still your "
                "best odds."
            )
        elif lo != hi:
            n = plan.guesses_needed
            why += f" Halving every turn finishes within {n} guess{'es' * (n != 1)}."

        sources = [r.id for r in prompt.passages[:2]]
        # Confidence reflects how decisive the advice is: forced answers and
        # perfectly balanced splits are certain; a hopeless position is less so.
        confidence = 0.95 if plan.win_guaranteed or lo == hi else 0.7
        return json.dumps(
            {
                "suggested_guess": guess,
                "explanation": why,
                "confidence": confidence,
                "sources": sources,
            }
        )


def get_provider(preference: str | None = None):
    """Pick a provider.

    ``preference`` (or the ``COACH_PROVIDER`` env var) is ``"openai"``,
    ``"offline"`` or ``"auto"`` (default): auto uses OpenAI when a key is
    configured and falls back to offline otherwise, logging why.
    """
    pref = (preference or os.environ.get("COACH_PROVIDER", "auto")).lower()
    if pref == "offline":
        return OfflineProvider()
    try:
        return OpenAIProvider()
    except ProviderError as exc:
        if pref == "openai":
            raise
        logger.warning("OpenAI provider unavailable (%s); using offline provider", exc)
        return OfflineProvider()
