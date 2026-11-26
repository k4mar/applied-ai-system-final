"""The coach agent: plan -> retrieve -> generate -> check -> (repair) -> answer.

This is the piece that turns four separate components into one agentic loop.
The model's draft is checked by the guardrails; if it fails, the agent sends the
exact failure reasons back to the model and asks for a corrected answer. If the
repair also fails -- or the provider errors -- the agent falls back to the
planner's exact midpoint, so the player always gets safe advice and the trace
says honestly which path produced it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from coach.guardrails import GuardrailReport, validate
from coach.llm import ProviderError, build_prompt, get_provider
from coach.planner import (
    GameObservation,
    InconsistentHistoryError,
    Plan,
    make_plan,
    retrieval_query,
)
from coach.retriever import RetrievedPassage, Retriever

logger = logging.getLogger("coach.agent")

# Where an answer came from. Shown to the player and counted by the evaluator.
MODE_ACCEPTED = "accepted"  # provider answer passed guardrails on the first try
MODE_REPAIRED = "repaired"  # failed, was told why, passed on retry
MODE_FALLBACK = "fallback"  # provider output never passed; planner answer used
MODE_TERMINAL = "terminal"  # round over or history inconsistent; no model call


@dataclass
class CoachAdvice:
    """What the player is shown, plus the audit trail behind it."""

    suggested_guess: int | None
    explanation: str
    confidence: float
    sources: list[str]
    mode: str
    provider: str
    plan: Plan | None
    trace: list[dict] = field(default_factory=list)
    guardrail_errors: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


def split_quality(plan: Plan, guess: int) -> float:
    """How evenly ``guess`` splits the live interval: 1.0 = perfect halving.

    This is the main ingredient of the coach's confidence score. It is computed
    from the planner's exact interval, so the model cannot inflate it.
    """
    n = plan.candidates
    if n <= 1 or not plan.contains(guess):
        return 1.0 if n == 1 and plan.contains(guess) else 0.0
    below = guess - plan.live_low
    above = plan.live_high - guess
    worst = max(below, above)
    best_worst = (n - 1) - (n - 1) // 2  # worst side of a perfect split
    return round(best_worst / worst, 3) if worst else 1.0


def combined_confidence(
    plan: Plan,
    guess: int,
    model_confidence: float,
    retrieved: list[RetrievedPassage],
    mode: str,
) -> float:
    """Blend independent signals into one 0-1 score the player can read.

    * 60% split quality -- is the suggested guess actually a good move?
    * 25% the model's self-reported confidence (clamped to 0-1)
    * 15% retrieval support -- did we find relevant passages to ground it?

    A repaired answer is discounted 10%, a fallback is capped at 0.6 because
    the explanation is a template rather than the model's reasoning.
    """
    retrieval = min(1.0, (retrieved[0].score / 5.0)) if retrieved else 0.0
    score = (
        0.60 * split_quality(plan, guess)
        + 0.25 * max(0.0, min(1.0, model_confidence))
        + 0.15 * retrieval
    )
    if mode == MODE_REPAIRED:
        score *= 0.9
    if mode == MODE_FALLBACK:
        score = min(score, 0.6)
    return round(score, 2)


class CoachAgent:
    """Runs the coach loop. Stateless between calls, so it is safe to cache."""

    def __init__(
        self,
        provider=None,
        retriever: Retriever | None = None,
        max_repairs: int = 1,
        top_k: int = 3,
    ):
        self.provider = provider or get_provider()
        self.retriever = retriever or Retriever.from_directory()
        self.max_repairs = max_repairs
        self.top_k = top_k

    def advise(self, obs: GameObservation) -> CoachAdvice:
        start = time.perf_counter()
        trace: list[dict] = []

        def step(name: str, **detail):
            trace.append({"step": name, **detail})
            logger.info("%s %s", name.upper(), detail)

        # 1. PLAN ------------------------------------------------------------
        try:
            plan = make_plan(obs)
        except InconsistentHistoryError as exc:
            step("plan", error=str(exc))
            return self._finish(
                CoachAdvice(
                    suggested_guess=None,
                    explanation=(
                        "These hints contradict each other, so no number can win. "
                        "That points to a bug in the game, not your play."
                    ),
                    confidence=0.0,
                    sources=[],
                    mode=MODE_TERMINAL,
                    provider=self.provider.name,
                    plan=None,
                    trace=trace,
                ),
                start,
            )
        step(
            "plan",
            interval=f"{plan.live_low}-{plan.live_high}",
            candidates=plan.candidates,
            attempts_left=plan.attempts_left,
            optimal=plan.optimal_guess,
            win_guaranteed=plan.win_guaranteed,
            situation=list(plan.situation),
        )
        if plan.status != "playing":
            msg = (
                "Round won -- nice. Start a new game for more coaching."
                if plan.status == "won"
                else "No attempts left this round. Start a new game to try again."
            )
            return self._finish(
                CoachAdvice(
                    None, msg, 1.0, [], MODE_TERMINAL, self.provider.name, plan, trace
                ),
                start,
            )

        # 2. RETRIEVE --------------------------------------------------------
        query = retrieval_query(obs, plan)
        retrieved = self.retriever.search(query, k=self.top_k)
        allowed = {r.id for r in retrieved}
        step("retrieve", query=query, passages=[(r.id, r.score) for r in retrieved])

        # 3-4. GENERATE + CHECK, with up to max_repairs repair rounds ---------
        feedback: list[str] = []
        all_errors: list[str] = []
        for attempt in range(self.max_repairs + 1):
            prompt = build_prompt(obs, plan, retrieved, feedback)
            try:
                raw = self.provider.generate(prompt)
            except ProviderError as exc:
                step("generate", attempt=attempt + 1, error=str(exc))
                all_errors.append(f"provider error: {exc}")
                break
            step("generate", attempt=attempt + 1, raw=raw[:500])

            report: GuardrailReport = validate(raw, obs, plan, allowed)
            step("check", attempt=attempt + 1, ok=report.ok, errors=report.errors)
            if report.ok:
                a = report.advice
                mode = MODE_ACCEPTED if attempt == 0 else MODE_REPAIRED
                return self._finish(
                    CoachAdvice(
                        suggested_guess=a.suggested_guess,
                        explanation=a.explanation,
                        confidence=combined_confidence(
                            plan, a.suggested_guess, a.confidence, retrieved, mode
                        ),
                        sources=a.sources,
                        mode=mode,
                        provider=self.provider.name,
                        plan=plan,
                        trace=trace,
                        guardrail_errors=all_errors,
                    ),
                    start,
                )
            feedback = report.errors
            all_errors.extend(report.errors)
            if attempt < self.max_repairs:
                step("repair", feedback=feedback)

        # 5. FALLBACK --------------------------------------------------------
        guess = plan.optimal_guess
        cite = [retrieved[0].id] if retrieved else []
        step(
            "fallback", guess=guess, reason="model output failed guardrails or errored"
        )
        return self._finish(
            CoachAdvice(
                suggested_guess=guess,
                explanation=(
                    f"The live interval is {plan.live_low}-{plan.live_high}; "
                    f"{guess} is its midpoint, which halves what is left. "
                    "(The AI's own answer failed a safety check, so this is the "
                    "planner's exact recommendation.)"
                ),
                confidence=combined_confidence(
                    plan, guess, 0.5, retrieved, MODE_FALLBACK
                ),
                sources=cite,
                mode=MODE_FALLBACK,
                provider=self.provider.name,
                plan=plan,
                trace=trace,
                guardrail_errors=all_errors,
            ),
            start,
        )

    @staticmethod
    def _finish(advice: CoachAdvice, start: float) -> CoachAdvice:
        advice.latency_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "ANSWER mode=%s guess=%s confidence=%.2f provider=%s latency_ms=%s",
            advice.mode,
            advice.suggested_guess,
            advice.confidence,
            advice.provider,
            advice.latency_ms,
        )
        return advice
