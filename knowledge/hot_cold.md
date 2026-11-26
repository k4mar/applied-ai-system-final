# Hot and cold proximity labels

## What the labels measure
The proximity label (Bullseye, Blazing hot, Hot, Warm, Cold, Freezing) shows how far the
last guess was from the secret as a fraction of the whole difficulty range. Hot means
within 15 percent of the range, so "Hot" on Hard (1-200) can still be 30 numbers away.

## Direction beats temperature
The direction hint (too high or too low) is exact; the temperature label is coarse. Always
narrow the interval using the direction first. Temperature is useful as a sanity check,
not as a reason to abandon the midpoint.

## Warmer and colder trends
"Warmer" means this guess was closer than the previous one and "colder" means it was
further away. A colder result after a correct halving step is normal: binary search
sometimes jumps across the secret, and the interval still got smaller.

## Do not chase a hot streak
Players who see "Blazing hot" often start guessing one number at a time next to their last
guess. That is linear search and it can burn every remaining attempt. Keep halving the
interval; a small interval halves just as fast as a large one.
