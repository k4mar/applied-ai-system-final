# Manual review of coach output

Automated tests check that advice is *safe* (inside the live interval, no leaked
secret, real citations). They cannot check whether an explanation is *clear and
honest to a player*. For that, each position below was run through the coach
(`python -m coach.cli --difficulty D --history H`, offline provider) and the
output was read and graded against four criteria:

- **Correct**: the suggested guess is the midpoint (or an equally good middle) of the live interval.
- **Honest**: the explanation does not over-promise (for example, it does not say a win is guaranteed when it isn't).
- **Clear**: a player who has never heard of binary search could follow it.
- **Grounded**: the cited passages are actually about this situation.

## Round 1 (before fixes)

| # | Difficulty / history | Coach output | Correct | Honest | Clear | Grounded | Result |
|---|---|---|---|---|---|---|---|
| 1 | Easy, no guesses | 10 (0.99): "Nothing is ruled out yet, so open in the middle of 1-20..." | ✔ | ✔ | ✔ | ✔ `binary_search#opening-move` | Pass |
| 2 | Normal, `50:high` | 25 (0.99): "50 was too high, so the secret is below it. 49 numbers remain (1-49)..." | ✔ | ✔ | ✔ | ✔ | Pass |
| 3 | Hard, `100:low,150:high,125:low` | 137 (0.99): "...24 numbers remain (126-149); 137 splits them in half." | ✔ | ✔ | ✔ | ✔ | Pass |
| 4 | Normal, six hints that pin the number to 66 | 66 (0.99): "Only 66 is left... Halving every turn finishes within **1 guesses**." | ✔ | ✔ | ✘ grammar, and the halving sentence is pointless when solved | ✔ | **Fail** |
| 5 | Easy, `10:high,10:high,3:low,1:low` (two wasted guesses) | 6 (0.92): "**1 was too low, so the secret is above it.** 6 numbers remain (4-9)..." | ✔ | ✔ | ✘ reasons from a guess that was already ruled out; true but misleading | ✔ `recovery#after-a-wasted-guess` | **Fail** |
| 6 | Hard, `200,199,198,197,196` all high (linear search) | 98 (0.92): "...A win is no longer guaranteed (195 numbers, 3 attempts), but the midpoint is still your best odds." | ✔ | ✔ admits the round is likely lost | ✔ | ✔ `recovery#when-a-win-is-no-longer-guaranteed` | Pass |
| 7 | Normal, `1..7` all low (last attempt) | 54 (0.92): "...Last attempt: any number inside the interval is a fair shot." | ✔ | ✔ | ✔ | ✔ `recovery#last-attempt` | Pass |
| 8 | Normal, `40:low,30:high` (contradiction) | no guess (0.00): "These hints contradict each other... a bug in the game, not your play." | ✔ refuses to guess | ✔ | ✔ | n/a (no model call) | Pass |

**Round 1: 6 / 8 pass.** Both failures were in explanation wording, not in the
guess itself. The guardrails and tests had passed for both, which shows why
this manual review was needed.

## Fixes

- **#4**: the "halving finishes within N guesses" sentence is skipped once the
  interval is a single number, and `guess`/`guesses` is pluralised correctly.
- **#5**: when the last guess was already ruled out, the explanation now says
  so ("1 was already ruled out, so it taught nothing new. The secret is still in
  4-9...") instead of reasoning from it.

Both are pinned by regression tests in `tests/test_coach.py`
(`test_offline_explanation_grammar_when_solved`,
`test_offline_explanation_does_not_reason_from_a_wasted_guess`).

## Round 2 (after fixes)

| # | Coach output now | Result |
|---|---|---|
| 4 | 66: "Only 66 is left -- the hints have pinned it down." | Pass |
| 5 | 6: "1 was already ruled out, so it taught nothing new. The secret is still in 4-9 (6 numbers); 6 splits them in half. Stay inside the interval... A win is no longer guaranteed (6 numbers, 2 attempts), but the midpoint is still your best odds." | Pass |

**Round 2: 8 / 8 pass.**

## Not covered here

This review graded the offline provider, which is what the committed logs use.
Answers from the live LLM (`COACH_PROVIDER=openai`) will be worded
differently each time, so this review needs repeating against live output. The
guardrails apply either way, but they only check safety, not wording quality.
