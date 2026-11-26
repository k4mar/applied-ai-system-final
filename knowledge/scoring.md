# Scoring rules

## Winning points decay with attempts
A win on the first guess is worth 100 points. Each extra attempt used before the win
costs 10 points, with a floor of 10, so winning on attempt 4 is worth 70 points.

## Wrong guesses cost five points
Every wrong guess, too high or too low, costs the same 5 points. The running score never
drops below zero, so an early streak of misses cannot leave you negative.

## Invalid input is free
Typing something that is not a whole number inside the range shows an error and does not
use an attempt or change the score. Only real guesses count.

## High scores
High scores are kept per difficulty for the whole session. A record is beaten by a
higher score, or by the same score reached in fewer attempts. Fewer guesses is always the
way to a better score, which is why the coach optimises for halving the interval.
