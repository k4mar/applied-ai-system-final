"""Tests for the AI Game Coach: retrieval, planning, guardrails and the agent loop.

The LLM is replaced by ScriptedProvider wherever behaviour under bad model
output matters, so these tests are deterministic and need no API key.
"""

import dataclasses
import json
import random
import re

import pytest

from coach.agent import (
    MODE_ACCEPTED,
    MODE_FALLBACK,
    MODE_REPAIRED,
    MODE_TERMINAL,
    CoachAgent,
    combined_confidence,
    split_quality,
)
from coach.cli import parse_history
from coach.evaluation import (
    FAULTS,
    ScriptedProvider,
    evaluate_retrieval,
    run_autoplay,
    run_fault_injection,
)
from coach.guardrails import validate
from coach.llm import OfflineProvider, ProviderError, build_prompt, get_provider
from coach.planner import (
    GameObservation,
    InconsistentHistoryError,
    make_plan,
    retrieval_query,
)
from coach.retriever import Retriever, load_passages, tokenize
from logic_utils import DIFFICULTIES, TOO_HIGH, TOO_LOW, WIN, check_guess


@pytest.fixture(scope="module")
def retriever():
    return Retriever.from_directory()


@pytest.fixture
def agent(retriever):
    return CoachAgent(provider=OfflineProvider(), retriever=retriever)


def obs(history=(), difficulty="Normal"):
    low, high, limit = DIFFICULTIES[difficulty]
    return GameObservation(low, high, limit, tuple(history), difficulty)


def good_json(**overrides):
    data = {
        "suggested_guess": 40,
        "explanation": "Halve the live interval.",
        "confidence": 0.9,
        "sources": ["binary_search#guess-the-middle-of-what-is-left"],
    }
    data.update(overrides)
    return json.dumps(data)


# --------------------------------------------------------------------------- retriever


def test_knowledge_base_loads_one_passage_per_section():
    passages = load_passages()
    assert len(passages) >= 20
    assert len({p.id for p in passages}) == len(passages), "passage ids must be unique"
    assert all(p.text and p.title for p in passages)


def test_missing_knowledge_dir_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_passages(tmp_path / "nope")
    with pytest.raises(FileNotFoundError):
        load_passages(tmp_path)  # exists but empty


def test_search_ranks_the_on_topic_passage_first(retriever):
    top = retriever.search("opening move first guess", k=3)
    assert top[0].id == "binary_search#opening-move"
    assert [r.score for r in top] == sorted((r.score for r in top), reverse=True)


def test_unrelated_query_returns_nothing_rather_than_noise(retriever):
    assert retriever.search("pizza toppings weather forecast") == []


def test_tokenizer_drops_stopwords_and_plurals():
    assert tokenize("The guesses are Too High") == ["guess", "too", "high"]


def test_retrieval_finds_expected_passage_in_top_3_for_every_labelled_case(retriever):
    rows = evaluate_retrieval(retriever)
    assert all(r["hit_at_3"] for r in rows), [r for r in rows if not r["hit_at_3"]]


# --------------------------------------------------------------------------- planner


def test_observation_has_no_secret_field():
    """Structural guardrail: the coach cannot leak what it is never given."""
    names = {f.name for f in dataclasses.fields(GameObservation)}
    assert "secret" not in names


def test_opening_plan_is_the_whole_range():
    plan = make_plan(obs())
    assert (plan.live_low, plan.live_high, plan.optimal_guess) == (1, 100, 50)
    assert plan.win_guaranteed and "opening" in plan.situation


def test_hints_narrow_the_live_interval():
    plan = make_plan(obs([(60, TOO_HIGH), (20, TOO_LOW)]))
    assert (plan.live_low, plan.live_high) == (21, 59)
    assert plan.optimal_guess == 40
    assert plan.attempts_left == 6


def test_wasted_guesses_are_detected():
    plan = make_plan(obs([(60, TOO_HIGH), (70, TOO_HIGH), (60, TOO_HIGH)]))
    assert plan.wasted_guesses == (70, 60)
    assert "wasted_guess" in plan.situation


def test_falling_behind_the_budget_is_reported_honestly():
    plan = make_plan(obs([(5, TOO_LOW), (10, TOO_LOW), (15, TOO_LOW)], "Hard"))
    assert not plan.win_guaranteed and "behind" in plan.situation


def test_contradictory_hints_raise():
    with pytest.raises(InconsistentHistoryError):
        make_plan(obs([(60, TOO_LOW), (55, TOO_HIGH)]))


def test_won_and_lost_rounds_have_no_next_guess():
    assert make_plan(obs([(50, WIN)])).status == "won"
    lost = make_plan(obs([(i, TOO_LOW) for i in range(1, 9)]))
    assert lost.status == "lost" and lost.optimal_guess is None


def test_bad_observations_are_rejected():
    with pytest.raises(ValueError):
        GameObservation(10, 1, 5)
    with pytest.raises(ValueError):
        GameObservation(1, 10, 5, ((3, "Maybe"),))


def test_retrieval_query_reflects_the_situation():
    q = retrieval_query(obs([(60, TOO_HIGH)]), make_plan(obs([(60, TOO_HIGH)])))
    assert "too high" in q


# --------------------------------------------------------------------------- guardrails


@pytest.fixture
def position():
    o = obs([(60, TOO_HIGH), (20, TOO_LOW)])
    return o, make_plan(o), {"binary_search#guess-the-middle-of-what-is-left"}


def test_well_formed_advice_passes(position):
    report = validate(good_json(), *position)
    assert report.ok and report.advice.suggested_guess == 40


def test_code_fenced_json_is_accepted(position):
    assert validate("```json\n" + good_json() + "\n```", *position).ok


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ("guess 40!", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        (good_json(suggested_guess=500), "outside the game range"),
        (good_json(suggested_guess=75), "already ruled out"),
        (good_json(suggested_guess=37.5), "whole number"),
        (good_json(suggested_guess=True), "whole number"),
        (good_json(explanation=""), "non-empty"),
        (good_json(explanation="x" * 401), "longer than"),
        (good_json(explanation="The secret is 42."), "claims to know the secret"),
        (
            good_json(explanation="It is definitely 33, go for it."),
            "claims to know the secret",
        ),
        (good_json(confidence=1.5), "between 0 and 1"),
        (good_json(confidence="high"), "number between 0 and 1"),
        (good_json(sources=[]), "cite at least one"),
        (good_json(sources=["made_up#x"]), "not provided"),
    ],
)
def test_guardrails_reject_bad_output(position, raw, fragment):
    report = validate(raw, *position)
    assert not report.ok
    assert any(fragment in e for e in report.errors), report.errors


def test_repeating_a_previous_guess_is_rejected():
    o = obs([(60, TOO_HIGH), (60, TOO_HIGH)])
    report = validate(
        good_json(suggested_guess=60),
        o,
        make_plan(o),
        {"binary_search#guess-the-middle-of-what-is-left"},
    )
    assert any("repeats" in e for e in report.errors)


def test_naming_the_number_is_allowed_once_the_hints_pin_it_down():
    o = obs([(50, TOO_LOW), (52, TOO_HIGH)])  # only 51 is left
    src = {"binary_search#guess-the-middle-of-what-is-left"}
    ok = validate(
        good_json(suggested_guess=51, explanation="The secret must be 51."),
        o,
        make_plan(o),
        src,
    )
    assert ok.ok, ok.errors


def test_guardrails_report_every_failure_not_just_the_first(position):
    report = validate(
        good_json(suggested_guess=999, confidence=3, sources=[]), *position
    )
    assert len(report.errors) >= 3


# --------------------------------------------------------------------------- agent loop


def test_agent_accepts_good_output_first_time(agent):
    advice = agent.advise(obs([(60, TOO_HIGH)]))
    assert advice.mode == MODE_ACCEPTED
    assert advice.suggested_guess == 30
    assert [t["step"] for t in advice.trace] == [
        "plan",
        "retrieve",
        "generate",
        "check",
    ]
    assert advice.sources and set(advice.sources) <= {
        p for t in advice.trace if t["step"] == "retrieve" for p, _ in t["passages"]
    }


def test_agent_repairs_bad_output_and_tells_the_model_why(retriever):
    def good(p):
        return OfflineProvider().generate(p)

    provider = ScriptedProvider([good_json(suggested_guess=999), good])
    advice = CoachAgent(provider, retriever).advise(
        obs([(60, TOO_HIGH), (20, TOO_LOW)])
    )
    assert advice.mode == MODE_REPAIRED
    assert advice.suggested_guess == 40
    assert "outside the game range" in provider.calls[1].user  # feedback was sent
    assert "repair" in [t["step"] for t in advice.trace]


def test_agent_falls_back_when_repair_also_fails(retriever):
    provider = ScriptedProvider(["not json"])
    advice = CoachAgent(provider, retriever, max_repairs=1).advise(
        obs([(60, TOO_HIGH)])
    )
    assert advice.mode == MODE_FALLBACK
    assert advice.suggested_guess == 30
    assert advice.confidence <= 0.6
    assert len(provider.calls) == 2  # one try + one repair, no more


def test_agent_falls_back_on_provider_error(retriever):
    provider = ScriptedProvider([ProviderError("timeout")])
    advice = CoachAgent(provider, retriever).advise(obs())
    assert advice.mode == MODE_FALLBACK and advice.suggested_guess == 50
    assert any("provider error" in e for e in advice.guardrail_errors)


def test_agent_does_not_call_the_model_for_finished_or_broken_rounds(retriever):
    provider = ScriptedProvider([AssertionError("model must not be called")])
    agent = CoachAgent(provider, retriever)
    for history in ([(50, WIN)], [(60, TOO_LOW), (55, TOO_HIGH)]):
        advice = agent.advise(obs(history))
        assert advice.mode == MODE_TERMINAL and advice.suggested_guess is None
    assert provider.calls == []


def test_every_fault_ends_in_safe_advice(retriever):
    for repair in (True, False):
        for result in run_fault_injection(retriever, repair=repair):
            assert result.final_safe, result
            if result.name != "markdown_fenced_json":
                assert result.first_try_caught, result.name
    assert len(FAULTS) >= 10


def test_advice_is_always_inside_the_live_interval_for_random_histories(agent):
    rng = random.Random(110)
    for _ in range(300):
        diff = rng.choice(list(DIFFICULTIES))
        low, high, limit = DIFFICULTIES[diff]
        secret = rng.randint(low, high)
        history = []
        for _ in range(rng.randint(0, limit - 1)):
            g = rng.randint(low, high)  # a careless human, not the coach
            out = check_guess(g, secret)
            history.append((g, out))
            if out == WIN:
                break
        advice = agent.advise(GameObservation(low, high, limit, tuple(history), diff))
        if advice.mode == MODE_TERMINAL:
            continue
        plan = advice.plan
        assert plan.contains(advice.suggested_guess)
        assert plan.contains(secret), "planner must never rule out the real secret"
        assert 0.0 <= advice.confidence <= 1.0


def test_following_the_coach_wins_every_game_on_every_difficulty(agent):
    for summary in run_autoplay(agent):
        assert summary.wins == summary.games, summary
        assert summary.max_attempts <= summary.attempt_limit


# --------------------------------------------------------------------------- confidence


def test_split_quality_rewards_the_midpoint():
    plan = make_plan(obs([(60, TOO_HIGH), (20, TOO_LOW)]))  # 21-59
    assert split_quality(plan, 40) == 1.0
    assert split_quality(plan, 22) < 0.6
    assert split_quality(plan, 70) == 0.0


def test_confidence_drops_for_a_legal_but_poor_suggestion(retriever):
    o = obs([(60, TOO_HIGH), (20, TOO_LOW)])
    plan = make_plan(o)
    hits = retriever.search("guess middle", k=3)
    assert combined_confidence(
        plan, 22, 0.99, hits, MODE_ACCEPTED
    ) < combined_confidence(plan, 40, 0.99, hits, MODE_ACCEPTED)


def test_model_cannot_inflate_confidence_past_one(retriever):
    plan = make_plan(obs())
    hits = retriever.search("opening move", k=3)
    assert combined_confidence(plan, 50, 50.0, hits, MODE_ACCEPTED) <= 1.0


# ----------------------------------------------------- prompt / provider / cli


def test_prompt_contains_facts_and_passages_but_never_a_secret(retriever):
    o = obs([(60, TOO_HIGH)])
    plan = make_plan(o)
    hits = retriever.search(retrieval_query(o, plan))
    prompt = build_prompt(o, plan, hits)
    assert "1-59" in prompt.user and hits[0].id in prompt.user
    # The knowledge notes talk about "the secret" in general; what must never
    # appear is the secret *value*, i.e. "secret" followed by a number.
    assert not re.search(r"secret\W{0,5}(is|=|:)?\W{0,3}\d", prompt.user, re.IGNORECASE)
    assert "Never claim to know it" in prompt.system


def test_auto_provider_falls_back_to_offline_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_provider("auto").name == "offline-rules"
    with pytest.raises(ProviderError):
        get_provider("openai")


def test_cli_history_parser():
    assert parse_history("60:high, 40:Low,50:win") == (
        (60, TOO_HIGH),
        (40, TOO_LOW),
        (50, WIN),
    )
    assert parse_history("") == ()
