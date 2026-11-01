from __future__ import annotations

import hashlib
import math
import re
from datetime import date
from typing import Any


def norm_text(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _canonical_id(paper: dict[str, Any]) -> str:
    doi = (paper.get("doi") or "").lower().strip()
    if doi:
        return "doi:" + doi
    for source_id in paper.get("source_ids", []):
        if source_id.startswith("arxiv:"):
            return source_id
    for source_id in paper.get("source_ids", []):
        if source_id.startswith("https://openalex.org/"):
            return "openalex:" + source_id.rsplit("/", 1)[-1]
    digest = hashlib.sha1(norm_text(paper.get("title", "")).encode("utf-8")).hexdigest()[:16]
    return "title:" + digest


def deduplicate_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge source records using DOI first and normalized exact title as fallback."""
    merged: list[dict[str, Any]] = []
    by_doi: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for paper in papers:
        doi_key = (paper.get("doi") or "").lower().strip()
        title_key = norm_text(paper.get("title", ""))
        existing = by_doi.get(doi_key) if doi_key else None
        if existing is None and title_key:
            existing = by_title.get(title_key)
        if existing is None:
            existing = dict(paper)
            existing["authors"] = list(paper.get("authors", []))
            existing["sources"] = list(dict.fromkeys(paper.get("sources", [])))
            existing["source_ids"] = list(dict.fromkeys(paper.get("source_ids", [])))
            merged.append(existing)
        else:
            # Prefer the richer record while retaining the useful fields from both sources.
            for key, value in paper.items():
                if value not in (None, "", [], 0, False) and existing.get(key) in (None, "", [], 0, False):
                    existing[key] = value
            existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + paper.get("sources", [])))
            existing["pdf_urls"] = list(dict.fromkeys(existing.get("pdf_urls", []) + paper.get("pdf_urls", []) + ([paper.get("pdf_url")] if paper.get("pdf_url") else [])))
            existing["source_ids"] = list(dict.fromkeys(existing.get("source_ids", []) + paper.get("source_ids", [])))
            # When duplicate sources disagree on date precision, preserve the most precise
            # supplied publication date (e.g. PubMed year-only + Crossref full date).
            precision_rank = {"unknown": 0, "year": 1, "month": 2, "day": 3}
            incoming_precision = precision_rank.get(str(paper.get("publication_date_precision", "unknown")), 0)
            existing_precision = precision_rank.get(str(existing.get("publication_date_precision", "unknown")), 0)
            if incoming_precision > existing_precision and paper.get("publication_date"):
                existing["publication_date"] = paper["publication_date"]
                existing["publication_date_precision"] = paper.get("publication_date_precision", "unknown")
            if len(paper.get("abstract", "")) > len(existing.get("abstract", "")):
                existing["abstract"] = paper["abstract"]
                existing["abstract_origin"] = paper.get("abstract_origin", "")
            if not existing.get("pdf_url") and paper.get("pdf_url"):
                existing["pdf_url"] = paper["pdf_url"]
            if existing.get("pdf_urls") and not existing.get("pdf_url"):
                existing["pdf_url"] = existing["pdf_urls"][0]
            if not existing.get("doi") and paper.get("doi"):
                existing["doi"] = paper["doi"]
            if not existing.get("url") and paper.get("url"):
                existing["url"] = paper["url"]
        if doi_key:
            by_doi[doi_key] = existing
        if title_key:
            by_title[title_key] = existing
        # Register aliases if the record got merged after an earlier source.
        if existing.get("doi"):
            by_doi[existing["doi"].lower()] = existing
        if norm_text(existing.get("title", "")):
            by_title[norm_text(existing["title"])] = existing

    for paper in merged:
        paper["id"] = _canonical_id(paper)
        paper["sources"] = sorted(set(paper.get("sources", [])))
        paper["source_ids"] = sorted(set(paper.get("source_ids", [])))
    return merged


def load_feedback_preferences(feedback_records: list[dict[str, Any]]) -> tuple[dict[str, float], list[str]]:
    weights: dict[str, float] = {}
    recent_tags: list[str] = []
    for record in feedback_records[-120:]:
        try:
            rating = int(record.get("rating", 3))
        except (TypeError, ValueError):
            continue
        if rating == 3:
            continue
        direction = 1.0 if rating >= 4 else -1.0
        strength = min(abs(rating - 3) / 2.0, 1.0)
        for raw_tag in record.get("tags", []):
            tag = norm_text(str(raw_tag))
            if not tag:
                continue
            weights[tag] = weights.get(tag, 0.0) + direction * strength
            if direction > 0:
                recent_tags.append(str(raw_tag).strip())
    return weights, list(dict.fromkeys(recent_tags))[-15:]


def score_paper(paper: dict[str, Any], cfg: dict[str, Any], today: date, feedback_weights: dict[str, float] | None = None) -> tuple[float, dict[str, float]]:
    title = norm_text(paper.get("title", ""))
    abstract = norm_text(paper.get("abstract", ""))
    text = f"{title} {abstract}"
    breakdown = {"keyword_title": 0.0, "keyword_abstract": 0.0, "recency": 0.0, "abstract_available": 0.0, "citation_tiebreaker": 0.0, "feedback": 0.0, "negative_terms": 0.0}
    keywords = cfg.get("keywords", [])
    for term in keywords:
        term_n = norm_text(str(term))
        if not term_n:
            continue
        if term_n in title:
            breakdown["keyword_title"] += 3.5
        elif term_n in abstract:
            breakdown["keyword_abstract"] += 1.0
    for term in cfg.get("negative_keywords", []):
        term_n = norm_text(str(term))
        if term_n and term_n in text:
            breakdown["negative_terms"] -= 2.5 if term_n in title else 1.0
    date_text = paper.get("publication_date", "")
    try:
        # Support year-only and year-month metadata without rewriting it as a fake date.
        if re.fullmatch(r"\d{4}", date_text):
            parsed_date = date(int(date_text), 7, 1)  # midpoint only for ranking, not displayed as publication date
        elif re.fullmatch(r"\d{4}-\d{2}", date_text):
            year, month = map(int, date_text.split("-"))
            parsed_date = date(year, month, 15)  # midpoint only for ranking
        else:
            parsed_date = date.fromisoformat(date_text[:10])
        age = max((today - parsed_date).days, 0)
        breakdown["recency"] = max(0.0, 3.0 - age / 20.0)
    except (ValueError, TypeError):
        pass
    if paper.get("abstract"):
        breakdown["abstract_available"] = 1.2
    # A deliberately weak tie-breaker; citation counts tend to privilege older papers.
    breakdown["citation_tiebreaker"] = min(math.log1p(max(int(paper.get("cited_by_count", 0) or 0), 0)) * 0.04, 0.35)
    for tag, weight in (feedback_weights or {}).items():
        if tag and tag in text:
            multiplier = 1.5 if tag in title else 0.5
            breakdown["feedback"] += max(-3.0, min(3.0, weight * multiplier))
    total = sum(breakdown.values())
    paper["score"] = round(total, 4)
    paper["score_breakdown"] = {k: round(v, 3) for k, v in breakdown.items()}
    return total, paper["score_breakdown"]


def rank_papers(papers: list[dict[str, Any]], cfg: dict[str, Any], today: date, feedback_weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    unique = deduplicate_papers(papers)
    for paper in unique:
        score_paper(paper, cfg, today, feedback_weights)
    return sorted(unique, key=lambda p: (p.get("score", 0), p.get("publication_date", "")), reverse=True)
