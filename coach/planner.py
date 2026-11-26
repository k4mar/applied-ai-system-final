"""Planning step of the coach: turn the visible game history into facts.

The planner only ever sees what the *player* has seen -- each guess and the
hint it got back. It never receives the secret. That is a structural guardrail:
the coach cannot leak a number it was never given.

Everything here is deterministic and exact, which is why the language model is
not trusted to do this arithmetic itself. The model explains and advises; the
planner decides what is true.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from logic_utils import TOO_HIGH, TOO_LOW, WIN, minimum_attempts_needed

VALID_OUTCOMES = (WIN, TOO_HIGH, TOO_LOW)


class InconsistentHistoryError(ValueError):
    """Raised when the hints rule out every possible number."""


@dataclass(frozen=True)
class GameObservation:
    """Everything the coach is allowed to know about a round.

    Attributes:
        low, high: inclusive range for the difficulty.
        attempt_limit: total attempts for the round.
        history: ``(guess, outcome)`` pairs in play order, where outcome is one
            of the ``logic_utils`` constants. No secret field, on purpose.
        difficulty: display name, used only for retrieval and wording.
    """

    low: int
    high: int
    attempt_limit: int
    history: tuple[tuple[int, str], ...] = ()
    difficulty: str = "Normal"

    def __post_init__(self):
        if self.low > self.high:
            raise ValueError(f"low ({self.low}) must be <= high ({self.high})")
        if self.attempt_limit < 1:
            raise ValueError("attempt_limit must be at least 1")
        for guess, outcome in self.history:
            if outcome not in VALID_OUTCOMES:
                raise ValueError(f"Unknown outcome {outcome!r} for guess {guess}")


@dataclass(frozen=True)
class Plan:
    """The planner's analysis of a round. All fields are exact."""

    live_low: int
    live_high: int
    attempts_used: int
    attempts_left: int
    optimal_guess: int | None
    guesses_needed: int
    win_guaranteed: bool
    status: str  # "playing" | "won" | "lost"
    wasted_guesses: tuple[int, ...] = ()
    situation: tuple[str, ...] = field(default_factory=tuple)

    @property
    def candidates(self) -> int:
        return max(0, self.live_high - self.live_low + 1)

    def contains(self, n: int) -> bool:
        return self.live_low <= n <= self.live_high


def live_interval(obs: GameObservation) -> tuple[int, int, tuple[int, ...]]:
    """Fold the history into the interval of numbers that could still win.

    Returns:
        ``(low, high, wasted)`` where ``wasted`` lists guesses that were made
        outside the live interval (or repeated) and therefore taught nothing.

    Raises:
        InconsistentHistoryError: if the hints contradict each other.
    """
    lo, hi = obs.low, obs.high
    seen: set[int] = set()
    wasted = []
    for guess, outcome in obs.history:
        if guess in seen or not (lo <= guess <= hi):
            wasted.append(guess)
        seen.add(guess)
        if outcome == WIN:
            lo = hi = guess
        elif outcome == TOO_HIGH:
            hi = min(hi, guess - 1)
        elif outcome == TOO_LOW:
            lo = max(lo, guess + 1)
        if lo > hi:
            raise InconsistentHistoryError(
                f"Hints leave no possible number after guess {guess} ({outcome})"
            )
    return lo, hi, tuple(wasted)


def make_plan(obs: GameObservation) -> Plan:
    """Analyse a round and decide the mathematically best next guess."""
    lo, hi, wasted = live_interval(obs)
    used = len(obs.history)
    left = max(0, obs.attempt_limit - used)
    won = any(outcome == WIN for _, outcome in obs.history)
    needed = minimum_attempts_needed(lo, hi)

    if won:
        status = "won"
    elif left == 0:
        status = "lost"
    else:
        status = "playing"

    situation = []
    if status != "playing":
        situation.append(status)
    else:
        if used == 0:
            situation.append("opening")
        elif lo == hi:
            situation.append("solved")
        elif hi - lo + 1 <= 3:
            situation.append("endgame")
        else:
            situation.append("narrowing")
        if needed > left:
            situation.append("behind")
        if left == 1:
            situation.append("last_attempt")
        if wasted:
            situation.append("wasted_guess")
        if used and obs.history[-1][1] in (TOO_HIGH, TOO_LOW):
            situation.append(
                "after_too_high" if obs.history[-1][1] == TOO_HIGH else "after_too_low"
            )

    return Plan(
        live_low=lo,
        live_high=hi,
        attempts_used=used,
        attempts_left=left,
        optimal_guess=(lo + hi) // 2 if status == "playing" else None,
        guesses_needed=needed,
        win_guaranteed=status == "playing" and needed <= left,
        status=status,
        wasted_guesses=wasted,
        situation=tuple(situation),
    )


# Search terms per situation. The retriever is plain BM25, so the query is
# phrased in the vocabulary the knowledge base actually uses.
_SITUATION_QUERIES = {
    "opening": "opening move first guess midpoint whole range",
    "narrowing": "guess middle what is left live interval halve",
    "endgame": "small interval midpoint last numbers",
    "solved": "live interval single number guess it",
    "behind": "win no longer guaranteed behind recovering midpoint best chance",
    "last_attempt": "last attempt final guess inside interval",
    "wasted_guess": "wasted guess repeat outside interval ruled out",
    "after_too_high": "too high ruled out upper bound lower",
    "after_too_low": "too low ruled out lower bound higher",
}


def retrieval_query(obs: GameObservation, plan: Plan) -> str:
    """Build the retrieval query for the current situation."""
    parts = [_SITUATION_QUERIES[s] for s in plan.situation if s in _SITUATION_QUERIES]
    parts.append(f"{obs.difficulty} attempts")
    return " ".join(parts)
