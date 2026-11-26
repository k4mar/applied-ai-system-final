#!/usr/bin/env bash
# Regenerate every file in docs/sample_runs/ from the current code.
# Usage: bash scripts/record_sample_runs.sh   (run from the repo root)
set -u
D=docs/sample_runs
mkdir -p "$D"
run() { f=$1; shift; { echo "\$ $*"; "$@" 2>&1; } > "$D/$f"; }

run 01_opening_advice.txt python -m coach.cli --difficulty Normal
run 02_after_two_hints.txt python -m coach.cli --difficulty Normal --history "60:high,20:low"
run 03_wasted_guesses_and_behind.txt python -m coach.cli --difficulty Hard --history "100:low,120:low,110:low,105:low"
run 04_contradictory_hints.txt python -m coach.cli --difficulty Normal --history "60:low,55:high"
run 05_autoplay_hard.txt python -m coach.cli --difficulty Hard --secret 137 --autoplay
run 06_guardrail_repair.txt python scripts/guardrail_demo.py already_ruled_out
run 07_guardrail_secret_leak.txt python scripts/guardrail_demo.py claims_to_know_secret
run 08_guardrail_fallback_on_crash.txt python scripts/guardrail_demo.py --no-repair provider_crash
run 09_pytest.txt python -m pytest -v
run 10_evaluate.txt python scripts/evaluate.py

# A short, readable sample of logs/coach.log (the evaluator writes thousands of lines).
rm -f logs/coach.log
python -m coach.cli --history "60:high,20:low" > /dev/null
python scripts/guardrail_demo.py already_ruled_out > /dev/null
python scripts/guardrail_demo.py --no-repair provider_crash > /dev/null
cp logs/coach.log "$D/coach_log_sample.log"
echo "sample runs written to $D/"
