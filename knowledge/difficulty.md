# Difficulty levels and attempt budgets

## Easy
Easy uses the range 1 to 20 with 6 attempts. Binary search needs at most 5, so Easy
leaves one spare attempt for a mistake.

## Normal
Normal uses the range 1 to 100 with 8 attempts. Binary search needs at most 7, so Normal
also leaves exactly one spare attempt.

## Hard
Hard uses the range 1 to 200 with 8 attempts. Binary search needs exactly 8, so Hard has
no spare attempts at all: a single guess that does not halve the interval can make the
round impossible to guarantee.

## Changing difficulty restarts the round
Switching difficulty starts a new round with a new secret inside the new range. Progress,
score and history from the previous round do not carry over, but high scores do.
