"""One place to configure coach logging for the app, the CLI and the evaluator.

Every coach step (plan, retrieve, generate, check, repair, fallback, answer)
is logged at INFO to ``logs/coach.log``, so any answer a player saw can be
traced back to the prompt context and guardrail verdicts that produced it.
"""

from __future__ import annotations

import logging
from pathlib import Path

DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(
    log_dir: Path | str = DEFAULT_LOG_DIR, console: bool = False
) -> Path:
    """Attach a file handler (and optionally a console handler) to ``coach``.

    Safe to call repeatedly -- Streamlit reruns the app script on every click,
    and a second file handler would duplicate every line.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "coach.log"

    logger = logging.getLogger("coach")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(
        getattr(h, "_coach_file", None) == str(log_file) for h in logger.handlers
    ):
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FORMAT))
        fh._coach_file = str(log_file)
        logger.addHandler(fh)

    if console and not any(
        getattr(h, "_coach_console", False) for h in logger.handlers
    ):
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        ch._coach_console = True
        logger.addHandler(ch)

    return log_file
