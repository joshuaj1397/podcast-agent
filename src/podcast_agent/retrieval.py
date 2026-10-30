"""Classic IR: BM25 / TF-IDF search over the knowledge base and the mock web corpus.

Retrieval is deterministic and offline, so the same claim always finds the same
evidence. TF-IDF is kept as the baseline that BM25 is compared against in eval.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

STOPWORDS = set(
    """a an and are as at be been but by can could did do does for from had has have he her his how i if in into is it
    its just me more most my no not of on or our out over said she so some such than that the their them then there
    these they this those to too up us very was we were what when where which while who why will with would you your
    yes yeah well really actually also about going get got lot lots thing things think know""".split()
)


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9$%]+(?:\.[0-9]+)?", text.lower().replace("’", "'"))
    return [_stem(t) for t in tokens if t not in STOPWORDS and len(t) > 1]


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


@dataclass(frozen=True)
class Doc:
    id: str
    kind: str  # "kb" | "web"
    title: str
    text: str
    source: str
    date: str
    reliability: str  # "kb" for curated entries, else high | medium | low
    url: str | None = None

    def brief(self, max_chars: int = 320) -> dict:
        """What a search tool returns to the model."""
        text = self.text if len(self.text) <= max_chars else self.text[:max_chars].rsplit(" ", 1)[0] + "..."
        return {"id": self.id, "title": self.title, "text": text, "source": self.source, "date": self.date,
                "reliability": self.reliability}


def load_docs(kb_dir: Path) -> list[Doc]:
    docs = []
    for row in _jsonl(kb_dir / "facts.jsonl"):
        docs.append(Doc(row["id"], "kb", row["title"], row["text"], row.get("source", "knowledge base"),
                        row.get("as_of", ""), "kb"))
    for row in _jsonl(kb_dir / "search_corpus.jsonl"):
        docs.append(Doc(row["id"], "web", row["title"], row["snippet"], _domain(row.get("url", "")),
                        row.get("published", ""), row.get("reliability", "medium"), row.get("url")))
    return docs


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _domain(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", url) or "web"


class Index:
    """One searchable collection (e.g. the KB) with a BM25 and a TF-IDF scorer."""

    def __init__(self, docs: list[Doc]):
        self.docs = docs
        corpus = [tokenize(f"{d.title} {d.title} {d.text}") for d in docs]  # title counted twice
        self.bm25 = BM25Okapi(corpus) if docs else None
        self.tfidf = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2))
        self.matrix = self.tfidf.fit_transform([f"{d.title} {d.text}" for d in docs]) if docs else None

    def search(self, query: str, k: int = 3, method: str = "bm25") -> list[tuple[Doc, float]]:
        if not self.docs or not tokenize(query):
            return []
        if method == "bm25":
            scores = self.bm25.get_scores(tokenize(query))
        else:
            scores = (self.matrix @ self.tfidf.transform([query]).T).toarray().ravel()
        top = np.argsort(-scores, kind="stable")[:k]
        return [(self.docs[i], float(scores[i])) for i in top if scores[i] > 0]


class Retriever:
    def __init__(self, docs: list[Doc]):
        self.by_id = {d.id: d for d in docs}
        self.kb = Index([d for d in docs if d.kind == "kb"])
        self.web = Index([d for d in docs if d.kind == "web"])
        self.all = Index(docs)


@lru_cache(maxsize=4)
def get_retriever(kb_dir: str) -> Retriever:
    return Retriever(load_docs(Path(kb_dir)))


def top_terms(texts: list[str], background: list[str], n: int = 10) -> list[str]:
    """Distinctive 1-2 word terms of `texts` against a background corpus (topic-tag hints)."""
    vec = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2), sublinear_tf=True)
    matrix = vec.fit_transform([" ".join(texts), *background])
    row = matrix[0].toarray().ravel()
    terms = vec.get_feature_names_out()
    return [terms[i] for i in np.argsort(-row)[:n] if row[i] > 0]


def rank_quotable(texts: list[str]) -> list[int]:
    """Indices of `texts` ordered by how much distinctive vocabulary they carry (quote shortlist)."""
    if not texts:
        return []
    vec = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, sublinear_tf=True)
    matrix = vec.fit_transform(texts)
    scores = np.asarray(matrix.sum(axis=1)).ravel()
    return list(np.argsort(-scores, kind="stable"))
