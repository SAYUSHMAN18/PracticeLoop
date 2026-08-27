from __future__ import annotations

import re

from app.jobs.sources import RawListing

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}")

# Words too common to say anything about fit -- filtered from both sides
# before scoring, or nearly every listing/resume pair would share them.
_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "are", "will", "have",
    "this", "that", "from", "who", "job", "role", "work", "team", "years",
    "experience", "skills", "ability", "strong", "including", "using",
    "such", "into", "all", "can", "has", "not", "but", "any", "may", "more",
}  # fmt: skip


def _tokenize(text: str) -> set[str]:
    # The token pattern allows a trailing "." so real terms like "Node.js"
    # match whole -- but that also means a keyword sitting right before a
    # sentence-ending period (e.g. "...led migration to Kubernetes.") comes
    # out as "kubernetes." instead of "kubernetes", silently failing to
    # match the same word written without trailing punctuation elsewhere.
    # A real term never *ends* in ".", so stripping only trailing dots (not
    # internal ones) fixes that without breaking "node.js"-style tokens.
    words = (w.rstrip(".") for w in _TOKEN_RE.findall(text.lower()))
    return {w for w in words if w and w not in _STOPWORDS}


def extract_keywords(text: str) -> set[str]:
    """Public entry point for the same stopword-filtered tokenizer the fit
    score uses, so other deterministic (no-LLM) features -- resume
    tailoring's fallback mode -- stay consistent with how "keyword overlap"
    is defined everywhere else in the app instead of reimplementing it."""
    return _tokenize(text)


def keyword_fit_score(listing: RawListing, resume_text: str) -> int:
    """Deterministic fallback fit score -- no LLM key required, so this is
    what a self-hoster with no AI provider configured still gets.

    Scored against the *listing's* vocabulary size, not the resume's: a
    long, padded resume would otherwise inflate its own score just by
    containing more distinct words overall, regardless of relevance.
    """
    listing_words = _tokenize(f"{listing.title} {listing.description}")
    if not listing_words:
        return 0

    resume_words = _tokenize(resume_text)
    overlap = listing_words & resume_words
    return round(100 * len(overlap) / len(listing_words))
