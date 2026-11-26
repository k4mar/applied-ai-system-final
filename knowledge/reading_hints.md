# Reading the hints

## Too high means every number at or above the guess is ruled out
"Too high -- go LOWER" means the secret is strictly smaller than your guess. The guess
itself and every number above it can never be the answer, so the new upper bound is the
guess minus one.

## Too low means every number at or below the guess is ruled out
"Too low -- go HIGHER" means the secret is strictly larger than your guess. The guess and
everything below it are eliminated, so the new lower bound is the guess plus one.

## Keep track of the live interval
After a few hints the possible secrets always form a single unbroken interval: the
largest "too low" guess plus one up to the smallest "too high" guess minus one. Write that
interval down (the coach shows it to you) and only ever guess inside it.

## Never repeat or guess outside the interval
A guess outside the live interval, or a repeat of an earlier guess, gives you no new
information but still costs an attempt and five points. It is the single most expensive
mistake in the game, because the attempt budget assumes every guess halves the interval.

## Contradictory hints mean something is wrong
If the hints ever leave no possible numbers -- for example "too low" on 60 and then
"too high" on 55 -- the game, not the player, has a bug. The direction hints in this game
are covered by tests, so the coach treats contradictions as an error to report rather than
something to reason around.
