# Model Card: Glitch Guesser AI Coach

## 1. Overview

| | |
|---|---|
| **System** | AI Coach for the Game Glitch Investigator number-guessing game |
| **Base project** | Module 1, *Game Glitch Investigator: The Impossible Guesser* |
| **Task** | Given the guesses and hints so far, suggest the next guess and explain why |
| **AI techniques** | Retrieval-augmented generation (BM25 over 28 strategy passages), agentic plan → generate → check → repair loop, guardrails, reliability evaluation |
| **Models** | OpenAI `gpt-4o-mini` (configurable via `COACH_MODEL`) when `OPENAI_API_KEY` is set; otherwise `offline-rules`, a deterministic rule-based stand-in that is **not** a language model |
| **Intended users** | Players of the game who want to learn how to search efficiently |
| **Out of scope** | Anything other than this game. The coach has no general-purpose knowledge and refuses to guess when the hints contradict each other |

## 2. How it works, briefly

The game logic owns the secret number. The coach receives only a `GameObservation`:
each guess and the hint it got back. It has no field for the secret, and a test
enforces that.

1. A **planner** turns the hints into the interval of numbers that can still win and
   finds its midpoint.
2. A **retriever** picks the 3 most relevant strategy passages.
3. The **LLM** drafts JSON advice.
4. **Guardrails** reject drafts that are malformed, outside the interval, repeated,
   overconfident, uncited or claim to know the secret.
5. A rejected draft is sent back once with the reasons. If it fails again, or the API
   errors, the player gets the planner's exact midpoint, labelled as a fallback.

## 3. AI collaboration during development

I built this project with an AI coding assistant (Claude) working in my repository. I
directed it: I gave it the assignment and chose to extend my Module 1 project. The
assistant proposed the coach design and wrote most of the code, tests and documentation.
I reviewed the result, ran it, and am responsible for it. Everything below is a real
event from that build. The commit history shows the same sequence.

**A suggestion I accepted: keep the secret out of the coach entirely.** The obvious
design was to pass the whole game state to the coach and tell the model in the prompt
"don't reveal the secret". The assistant suggested making the boundary structural
instead: the app computes each hint, and only guesses and hints cross into `coach/`. I
accepted this because a prompt instruction can be ignored or jailbroken, while a value
that is never sent cannot leak. It also meant the guardrail that catches "the secret is
42" only has to catch *hallucinations*, never real leaks.

**Suggestions I rejected or revised:**

- **An over-eager leak detector.** The first version of the secret-claim guardrail also
  flagged phrases like *"it's 50"*. That would have rejected harmless sentences such as
  "it's 50/50 which half it's in", making the coach fall back for no reason. I narrowed
  it to explicit claims ("the secret is N", "definitely N", "must be N"). I also added an
  exception: once the hints pin the interval to a single number, saying "the secret must
  be 51" is a correct deduction, not a leak. A test covers that case.
- **A stemming rule that broke retrieval.** The AI-written tokenizer turned "guesses"
  into "guesse", so it never matched "guess". A unit test caught it before it reached
  the app, and the rule was rewritten. The lesson: AI-generated code needs the same
  tests as my own.
- **A misleading fallback message.** The fallback originally always said the AI's answer
  "failed a safety check", even when the real cause was that the API could not be
  reached. I changed it to name the actual cause, because telling users the wrong
  reason erodes trust in the system's honesty.
- **Explanations that were technically true but unhelpful.** Reviewing outputs by hand
  (see `docs/human_evaluation.md`), I found "within 1 guesses". I also found advice that
  reasoned from an already-wasted guess ("1 was too low, so the secret is above it").
  Neither broke a guardrail. Both were rewritten and pinned with regression tests.

## 4. Evaluation results

All numbers are reproducible with `python scripts/evaluate.py` and `pytest`; the full
tables are in `docs/evaluation_report.md`.

| Experiment | Result |
|---|---|
| Unit and integration tests | 133 / 133 pass (83 game, 50 coach) |
| Autoplay: follow the coach against every secret on every difficulty | 320 / 320 won; never exceeded the attempt limit |
| Fault injection: 11 kinds of bad model output | 11 / 11 caught on first check; 24 / 24 final answers safe |
| Consistency: same position, 5 runs | 4 / 4 positions identical |
| Retrieval accuracy | hit@3 7 / 7, hit@1 5 / 7 |
| Manual review of explanation quality | 6 / 8 → 8 / 8 after two fixes |

**Important caveat:** these numbers come from the **offline provider**. They prove that
the planner, retrieval, guardrails, repair and fallback work. They do **not** prove how
often the live LLM writes a good first draft. Running the evaluation with
`--provider openai` measures that, and the result will vary between runs.

**What surprised me.**

- How much the guardrails mattered compared with the prompt. Most fault types (a
  ruled-out guess, a fake citation, a confidence of 1.7) are things a prompt *tells* the
  model not to do. Only a checker guarantees it doesn't.
- The two worst explanation problems passed every automated check and were found only
  by reading the output as a player would.

## 5. Limitations and biases

- **One strategy, one voice.** The knowledge base teaches only binary search, so the
  coach will always recommend the midpoint. That is optimal for this game, but the
  coach has no way to say "your way also works". The knowledge base is English-only
  and assumes the player is comfortable with ranges and halving.
- **The leak detector is a regex.** It catches phrasings like "the secret is 42" but not
  every paraphrase (for example, "go with forty-two, that's the one"). This is
  acceptable only because the coach never has the real secret to leak. In a system where
  the model *did* see sensitive data, a regex filter would not be enough.
- **Confidence is partly self-reported.** The model's own confidence counts for 25% of
  the score and is not calibrated. The 60% split-quality component is exact, but the
  score is a heuristic, not a probability of winning.
- **Retrieval is lexical.** BM25 only matches words that appear in the notes. The
  planner writes queries in that vocabulary, so it works here. A free-text player
  question ("am I doomed?") would retrieve poorly.
- **Evaluation scope.** The automated numbers cover the offline provider. Live-LLM
  quality has only been checked through the guardrails, not measured at scale.
- **Latency and cost.** Each coach request can make up to 2 API calls (draft + repair).

## 6. Misuse and safety considerations

The stakes are low: this is a single-player game with no accounts or personal data. The
realistic risks and their mitigations are:

- **Cheating / leaking the answer.** The coach never receives the secret (structural
  guarantee).
- **Prompt injection through inputs.** The only inputs that reach the model are
  integers and fixed outcome labels, already validated by `parse_guess`. There is no
  free-text channel from the player to the model.
- **Overtrust in the AI.** Every suggestion shows its confidence, sources, mode
  (`accepted` / `repaired` / `fallback`) and full trace. Fallback answers are labelled
  as not coming from the AI.
- **API key handling.** The key is read from the environment only. `.env` files are
  git-ignored, and the key never appears in logs.

## 7. Ethical reflection

The original Module 1 assignment was about an AI that wrote code which *looked* finished
and was quietly wrong. An LLM coach has the same failure mode: a fluent, confident
explanation can be wrong in ways a player can't spot. I tried to answer that with
design rather than trust:

- the facts come from code,
- the model's output is checked against them,
- failures are visible rather than hidden.

If I extended this, I would measure the live model's first-draft pass rate before
claiming anything about its quality. I would also test the explanations with real
players, since a passing guardrail is not the same as a helpful answer.
