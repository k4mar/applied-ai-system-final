# Binary search strategy

## Guess the middle of what is left
The fastest way to find a hidden number is to guess the middle of the numbers that are
still possible. Every "too high" or "too low" answer then rules out half of the remaining
candidates, no matter where the secret is. Guessing anywhere else lets an unlucky answer
rule out less than half, which wastes attempts.

## How many guesses a perfect search needs
A range with n possible numbers needs at most ceil(log2(n)) guesses with binary search:
20 numbers need 5 guesses, 100 numbers need 7, and 200 numbers need 8. Each guess doubles
how many numbers you can cover, so the attempt budget only has to grow slowly as the
range gets wider.

## Opening move
On the first guess nothing has been ruled out yet, so the best opening is the midpoint
of the whole range: 10 on Easy (1-20), 50 on Normal (1-100) and 100 on Hard (1-200).
Opening with a lucky number or a favourite number is the most common way to fall behind
the budget on the very first turn.

## Even-sized ranges have two middles
When an even number of candidates is left there are two middle values, and either one
splits the range as evenly as possible. The coach rounds down to the lower middle so its
advice is consistent, but picking the upper middle is just as good.
