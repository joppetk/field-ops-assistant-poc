import re
from typing import Dict, List

from sqlmodel import Session, select

from app.models import DocumentChunk


STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "shall", "will",
    "are", "was", "were", "you", "your", "can", "not", "all", "any",
    "into", "then", "than", "have", "has", "had", "but", "or", "if",
    "in", "on", "of", "to", "a", "an", "is", "be", "by", "as", "at",
    "it", "its", "may", "must", "should"
}


def tokenize(text: str) -> List[str]:
    text = text.lower()
    tokens = re.findall(r"[a-zA-Z0-9_\-/\.]+", text)

    return [
        token for token in tokens
        if len(token) >= 2 and token not in STOPWORDS
    ]


def score_chunk(query: str, chunk: DocumentChunk) -> int:
    """
    Simple keyword scoring.

    Higher score means the chunk is more relevant.
    """
    query_lower = query.lower()
    chunk_text_lower = chunk.chunk_text.lower()
    keywords_lower = chunk.keywords.lower() if chunk.keywords else ""

    query_tokens = tokenize(query)

    score = 0

    # Strong score if full phrase appears
    if query_lower in chunk_text_lower:
        score += 5

    # Score matching words in chunk text
    for token in query_tokens:
        if token in chunk_text_lower:
            score += 2

        if token in keywords_lower:
            score += 1

    return score


def make_snippet(text: str, query: str, max_chars: int = 350) -> str:
    """
    Return a short snippet around the first matching query token.
    """
    if len(text) <= max_chars:
        return text

    query_tokens = tokenize(query)
    lower_text = text.lower()

    first_pos = -1

    for token in query_tokens:
        pos = lower_text.find(token.lower())
        if pos != -1:
            first_pos = pos
            break

    if first_pos == -1:
        return text[:max_chars] + "..."

    start = max(0, first_pos - 100)
    end = min(len(text), start + max_chars)

    snippet = text[start:end]

    if start > 0:
        snippet = "..." + snippet

    if end < len(text):
        snippet = snippet + "..."

    return snippet


def search_chunks(
    session: Session,
    query: str,
    limit: int = 5,
    min_score: int = 1
) -> List[Dict]:
    """
    Search all chunks and rank them by keyword score.

    This is intentionally simple for the 7-day POC.
    Later, this can be replaced with vector similarity search.
    """
    statement = select(DocumentChunk)
    chunks = session.exec(statement).all()

    scored_results = []

    for chunk in chunks:
        score = score_chunk(query, chunk)

        if score >= min_score:
            scored_results.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "source": chunk.source_filename,
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "score": score,
                    "text": chunk.chunk_text,
                    "snippet": make_snippet(chunk.chunk_text, query),
                    "keywords": chunk.keywords,
                }
            )

    scored_results.sort(key=lambda item: item["score"], reverse=True)

    return scored_results[:limit]
