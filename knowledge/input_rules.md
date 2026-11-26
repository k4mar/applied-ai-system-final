# Input rules

## Whole numbers only
Guesses must be whole numbers. A decimal like 3.9 is rejected rather than rounded, while
42.0 is accepted as 42. Text such as "fifty" or "abc" is rejected.

## Guesses must be inside the range
A guess below the minimum or above the maximum of the current difficulty is rejected with
an out-of-range message. Rejected input never costs an attempt.

## New Game resets the round
The New Game button picks a new secret and resets attempts, score, status and guess
history. Only the high score table survives a new game.
