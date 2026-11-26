"""Retrieval step of the coach: BM25 search over the markdown knowledge base.

Each ``## `` section of each file in ``knowledge/`` becomes one passage. The
coach retrieves the passages that best match the current game situation and
hands them to the model as the only facts it is allowed to cite.

Pure Python on purpose -- no vector database or embedding API -- so retrieval is
deterministic, fast, testable and works offline. With ~30 short passages, BM25
is more than good enough, and the evaluation harness measures that claim
(see ``coach/evaluation.py::evaluate_retrieval``).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("coach.retriever")

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "then",
        "there",
        "this",
        "to",
        "up",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(tok: str) -> str:
    """Strip English plural endings so "guesses" matches "guess".

    (A first version dropped "es" only when the word did NOT end in "ses",
    which turned "guesses" into "guesse" -- the tokenizer test caught it.)
    """
    if len(tok) <= 3:
        return tok
    if tok.endswith(("sses", "xes", "zes", "ches", "shes")):
        return tok[:-2]
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords, crude-stem plurals."""
    tokens = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if tok in _STOPWORDS:
            continue
        tok = _stem(tok)
        tokens.append(tok)
    return tokens


@dataclass(frozen=True)
class Passage:
    """One retrievable chunk of the knowledge base."""

    id: str  # e.g. "binary_search#opening-move"
    source: str  # file name, e.g. "binary_search.md"
    title: str  # the "## " heading
    text: str  # the section body


@dataclass(frozen=True)
class RetrievedPassage:
    """A passage plus the score it was retrieved with."""

    passage: Passage
    score: float

    @property
    def id(self) -> str:
        return self.passage.id


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def load_passages(knowledge_dir: Path | str = DEFAULT_KNOWLEDGE_DIR) -> list[Passage]:
    """Split every ``*.md`` file in ``knowledge_dir`` into ``## `` sections.

    Raises:
        FileNotFoundError: if the directory is missing or holds no passages.
            A coach with no knowledge base must not silently run ungrounded.
    """
    knowledge_dir = Path(knowledge_dir)
    if not knowledge_dir.is_dir():
        raise FileNotFoundError(f"Knowledge directory not found: {knowledge_dir}")

    passages: list[Passage] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        title, body = None, []
        for line in path.read_text(encoding="utf-8").splitlines() + ["## "]:
            if line.startswith("## "):
                if title and " ".join(body).strip():
                    passages.append(
                        Passage(
                            id=f"{path.stem}#{_slug(title)}",
                            source=path.name,
                            title=title,
                            text=" ".join(s.strip() for s in body if s.strip()),
                        )
                    )
                title, body = line[3:].strip(), []
            elif title is not None and not line.startswith("# "):
                body.append(line)

    if not passages:
        raise FileNotFoundError(f"No '## ' sections found in {knowledge_dir}")
    logger.debug("Loaded %d passages from %s", len(passages), knowledge_dir)
    return passages


class Retriever:
    """Okapi BM25 over passage title + text.

    The title is counted twice, so a passage *about* a topic outranks one that
    merely mentions it.
    """

    def __init__(self, passages: list[Passage], k1: float = 1.5, b: float = 0.75):
        if not passages:
            raise ValueError("Retriever needs at least one passage")
        self.passages = passages
        self.k1, self.b = k1, b
        self._docs = [tokenize(f"{p.title} {p.title} {p.text}") for p in passages]
        self._tf = [Counter(d) for d in self._docs]
        self._avg_len = sum(len(d) for d in self._docs) / len(self._docs)
        df = Counter(tok for d in self._docs for tok in set(d))
        n = len(self._docs)
        self._idf = {
            tok: math.log(1 + (n - f + 0.5) / (f + 0.5)) for tok, f in df.items()
        }

    @classmethod
    def from_directory(cls, knowledge_dir: Path | str = DEFAULT_KNOWLEDGE_DIR):
        return cls(load_passages(knowledge_dir))

    def ids(self) -> set[str]:
        return {p.id for p in self.passages}

    def search(self, query: str, k: int = 3) -> list[RetrievedPassage]:
        """Return the top ``k`` passages for ``query``, best first.

        Passages with a score of 0 (no shared terms) are never returned, so an
        unrelated query yields an empty list rather than random context.
        """
        q_terms = tokenize(query)
        scored = []
        for i, tf in enumerate(self._tf):
            doc_len = len(self._docs[i])
            score = 0.0
            for term in q_terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                norm = self.k1 * (1 - self.b + self.b * doc_len / self._avg_len)
                score += self._idf[term] * f * (self.k1 + 1) / (f + norm)
            if score > 0:
                scored.append(RetrievedPassage(self.passages[i], round(score, 3)))
        scored.sort(key=lambda r: (-r.score, r.passage.id))
        results = scored[:k]
        logger.info(
            "retrieve query=%r -> %s",
            query,
            [(r.id, r.score) for r in results],
        )
        return results
