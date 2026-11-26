"""AI Game Coach: retrieval-augmented, self-checking advice for the guessing game.

Pipeline (see diagrams/architecture.mmd):

    GameObservation -> planner -> retriever -> LLM provider -> guardrails
                                      ^                            |
                                      +---- repair feedback <------+
                                                                   |
                                                fallback <---------+
"""

from coach.agent import CoachAdvice, CoachAgent
from coach.planner import GameObservation, make_plan

__all__ = ["CoachAdvice", "CoachAgent", "GameObservation", "make_plan"]
