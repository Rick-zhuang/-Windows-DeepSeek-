from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
CROSSREF_URL = "https://api.crossref.org/works"
PUBLISHER_DOMAINS = {"Nature": ("nature.com",), "ACS Publications": ("pubs.acs.org",), "Wiley Online Library": ("onlinelibrary.wiley.com", "wiley.com")}
OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def make_session(user_agent: str) -> requests.Session:
    retry = Retry(
        total=4,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "application/json, application/atom+xml, application/xml, text/xml, */*"})
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted_index.items():
        for index in indexes:
            positions[index] = word
    return " ".join(positions[i] for i in sorted(positions)) if positions else ""


def _authors_from_openalex(work: dict[str, Any], limit: int = 8) -> list[str]:
    names = []
    for authorship in (work.get("authorships") or [])[:limit]:
        name = (authorship.get("author") or {}).get("display_name")
        if name:
            names.append(str(name))
    if len(work.get("authorships") or []) > limit:
        names.append("等")
    return names


def _normalise_doi(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.I)
    return value.lower().rstrip(" .")


def _date_or_none(value: str | None) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except (ValueError, TypeError):
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return default


def normalise_openalex(work: dict[str, Any]) -> dict[str, Any] | None:
    title = re.sub(r"\s+", " ", str(work.get("title") or "")).strip()
    if not title:
        return None
    location = work.get("primary_location") or {}
    oa = work.get("open_access") or {}
    doi = _normalise_doi(work.get("doi"))
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    primary_source = location.get("source") or {}
    return {
        "title": title,
        "abstract": abstract,
        "publication_date": _date_or_none(work.get("publication_date")),
        "publication_date_precision": "day" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(work.get("publication_date") or "")) else ("month" if re.fullmatch(r"\d{4}-\d{2}", str(work.get("publication_date") or "")) else ("year" if re.fullmatch(r"\d{4}", str(work.get("publication_date") or "")) else "unknown")),
        "authors": _authors_from_openalex(work),
        "doi": doi,
        "url": (work.get("doi") or location.get("landing_page_url") or work.get("id") or ""),
        "pdf_url": location.get("pdf_url") or "",
        "pdf_urls": [location.get("pdf_url")] if location.get("pdf_url") else [],
        "source_ids": [str(work.get("id") or "")],
        "sources": ["OpenAlex"],
        "record_type": str(work.get("type") or "unknown"),
        "venue": (primary_source.get("display_name") or ""),
        "cited_by_count": _safe_int(work.get("cited_by_count")),
        "is_open_access": bool(oa.get("is_oa")),
        "abstract_origin": "OpenAlex" if abstract else "unavailable",
    }


def normalise_arxiv(entry: ET.Element) -> dict[str, Any] | None:
    title = re.sub(r"\s+", " ", (entry.findtext("atom:title", default="", namespaces=ARXIV_NS) or "")).strip()
    abstract = re.sub(r"\s+", " ", (entry.findtext("atom:summary", default="", namespaces=ARXIV_NS) or "")).strip()
    identifier_url = entry.findtext("atom:id", default="", namespaces=ARXIV_NS) or ""
    arxiv_id = identifier_url.rstrip("/").split("/abs/")[-1]
    stable_arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
    published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS)
    try:
        pubdate = datetime.fromisoformat(published.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, AttributeError):
        pubdate = ""
    authors = [a.findtext("atom:name", default="", namespaces=ARXIV_NS) for a in entry.findall("atom:author", ARXIV_NS)]
    authors = [a for a in authors if a][:8]
    pdf_url = ""
    for link in entry.findall("atom:link", ARXIV_NS):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    if not title:
        return None
    return {
        "title": title,
        "abstract": abstract,
        "publication_date": pubdate,
        "publication_date_precision": "day" if pubdate else "unknown",
        "authors": authors,
        "doi": "",
        "url": identifier_url,
        "pdf_url": pdf_url,
        "pdf_urls": [pdf_url] if pdf_url else [],
        "source_ids": [f"arxiv:{stable_arxiv_id}"],
        "sources": ["arXiv"],
        "record_type": "preprint",
        "venue": "arXiv preprint",
        "cited_by_count": 0,
        "is_open_access": True,
        "abstract_origin": "arXiv" if abstract else "unavailable",
    }


def _safe_error(exc: Exception) -> str:
    # OpenAlex API keys are sent as query parameters; never leak them into logs/reports.
    return re.sub(r"(?i)(api_key=)[^&\s]+", r"\1[REDACTED]", f"{type(exc).__name__}: {exc}")


def fetch_openalex(cfg: dict[str, Any], lookback_days: int, session: requests.Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    today = date.today()
    first_day = today - timedelta(days=lookback_days)
    status: dict[str, Any] = {"queries_attempted": 0, "queries_succeeded": 0, "records_received": 0, "errors": []}
    results: list[dict[str, Any]] = []
    queries = cfg.get("search_queries", [])
    for query in queries:
        status["queries_attempted"] += 1
        params: dict[str, Any] = {
            "search": query,
            "filter": f"from_publication_date:{first_day.isoformat()},to_publication_date:{today.isoformat()}",
            "sort": "publication_date:desc",
            "per_page": int(cfg.get("results_per_query", 30)),
            "select": "id,doi,title,publication_date,type,authorships,primary_location,cited_by_count,abstract_inverted_index,open_access",
        }
        key = os.getenv("OPENALEX_API_KEY", "").strip()
        if key:
            params["api_key"] = key
        try:
            response = session.get(OPENALEX_URL, params=params, timeout=(10, 35))
            response.raise_for_status()
            payload = response.json()
            status["queries_succeeded"] += 1
            for raw in payload.get("results", []):
                item = normalise_openalex(raw)
                if item:
                    results.append(item)
            time.sleep(float(cfg.get("source_pause_seconds", 0.2)))
        except Exception as exc:
            status["errors"].append(f"OpenAlex 查询 {query!r} 失败：{_safe_error(exc)}")
            logging.warning("OpenAlex query failed for query=%r: %s", query, _safe_error(exc))
    status["records_received"] = len(results)
    return results, status


def _arxiv_query_for(term: str) -> str:
    # arXiv's API syntax treats quoted all-field text as a phrase.
    escaped = term.replace('"', "").strip()
    return f'all:"{escaped}"'


def fetch_arxiv(cfg: dict[str, Any], lookback_days: int, session: requests.Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    today = date.today()
    first_day = today - timedelta(days=lookback_days)
    status: dict[str, Any] = {"queries_attempted": 0, "queries_succeeded": 0, "records_received": 0, "errors": []}
    results: list[dict[str, Any]] = []
    queries = cfg.get("arxiv_queries", cfg.get("search_queries", []))
    pause_seconds = max(3.0, float(cfg.get("arxiv_pause_seconds", 3.0)))
    for query_index, query in enumerate(queries):
        status["queries_attempted"] += 1
        params = {
            "search_query": _arxiv_query_for(query),
            "start": 0,
            "max_results": min(int(cfg.get("arxiv_results_per_query", 20)), 2000),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            response = session.get(ARXIV_API_URL, params=params, timeout=(10, 45))
            response.raise_for_status()
            root = ET.fromstring(response.text)
            status["queries_succeeded"] += 1
            for entry in root.findall("atom:entry", ARXIV_NS):
                item = normalise_arxiv(entry)
                if not item or not item["publication_date"]:
                    continue
                pub_date = date.fromisoformat(item["publication_date"])
                if first_day <= pub_date <= today:
                    results.append(item)
        except Exception as exc:
            status["errors"].append(f"arXiv 查询 {query!r} 失败：{_safe_error(exc)}")
            logging.warning("arXiv query failed for query=%r: %s", query, _safe_error(exc))
        finally:
            # arXiv requests that clients pause at least three seconds between legacy API calls.
            if query_index < len(queries) - 1:
                time.sleep(pause_seconds)
    status["records_received"] = len(results)
    return results, status


def _publisher_allowed(url: str, publisher: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in PUBLISHER_DOMAINS.get(publisher, ()))
    except ValueError:
        return False


def _normalise_pubmed_xml(article: ET.Element) -> dict[str, Any] | None:
    def txt(path: str) -> str:
        node = article.find(path)
        return "".join(node.itertext()).strip() if node is not None else ""
    title = re.sub(r"\s+", " ", txt("./MedlineCitation/Article/ArticleTitle"))
    if not title:
        return None
    pmid = txt("./MedlineCitation/PMID")
    abstract_parts = []
    for node in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
        label = node.attrib.get("Label", "")
        value = " ".join("".join(node.itertext()).split())
        if value:
            abstract_parts.append((label + ": " if label else "") + value)
    doi = ""
    for node in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        if node.attrib.get("IdType") == "doi":
            doi = _normalise_doi("".join(node.itertext()))
            break
    authors = []
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author")[:8]:
        collective = author.findtext("CollectiveName")
        name = " ".join(x for x in [author.findtext("ForeName"), author.findtext("LastName")] if x)
        name = collective or name
        if name: authors.append(name)
    # Prefer the explicit electronic/article date, then the issue date. Preserve
    # the precision actually supplied by PubMed; never fabricate January 1 for
    # records that only provide a year.
    pubdate = ""
    precision = "unknown"
    date_candidates = [
        ("./MedlineCitation/Article/ArticleDate/Year", "./MedlineCitation/Article/ArticleDate/Month", "./MedlineCitation/Article/ArticleDate/Day"),
        ("./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year", "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Month", "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Day"),
    ]
    for year_path, month_path, day_path in date_candidates:
        year = txt(year_path)
        if not year.isdigit() or len(year) != 4:
            continue
        month_raw, day_raw = txt(month_path), txt(day_path)
        month_names = {name.lower(): i for i, name in enumerate(
            ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
        month_names.update({name[:3].lower(): i for i, name in enumerate(
            ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)})
        month = int(month_raw) if month_raw.isdigit() else month_names.get(month_raw.lower(), 0)
        try:
            if month and day_raw.isdigit():
                pubdate = date(int(year), month, int(day_raw)).isoformat(); precision = "day"
            elif month:
                pubdate = f"{int(year):04d}-{month:02d}"; precision = "month"
            else:
                pubdate = year; precision = "year"
            break
        except ValueError:
            continue
    if not pubdate:
        medline_date = txt("./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate")
        match = re.search(r"\b(19|20)\d{2}\b", medline_date)
        if match:
            pubdate = match.group(0); precision = "year"
    if not pubdate:
        # DateCompleted is a database-processing date, not publication date.
        pubdate = ""
    journal = txt("./MedlineCitation/Article/Journal/Title")
    return {"title": title, "abstract": "\n".join(abstract_parts), "publication_date": pubdate,
        "publication_date_precision": precision,
        "authors": authors, "doi": doi, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "pdf_url": "", "pdf_urls": [], "source_ids": [f"pubmed:{pmid}"] if pmid else [],
        "sources": ["PubMed"], "record_type": "journal-article", "venue": journal,
        "cited_by_count": 0, "is_open_access": False, "abstract_origin": "PubMed" if abstract_parts else "unavailable"}


def fetch_pubmed(cfg: dict[str, Any], lookback_days: int, session: requests.Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    today = date.today(); first_day = today - timedelta(days=lookback_days)
    status = {"queries_attempted": 0, "queries_succeeded": 0, "records_received": 0, "errors": []}
    results: list[dict[str, Any]] = []
    email = cfg.get("contact_email", "").strip()
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    for query in cfg.get("search_queries", []):
        status["queries_attempted"] += 1
        date_filter = (f'("{first_day:%Y/%m/%d}"[dp] : "{today:%Y/%m/%d}"[dp]) '
                       f'OR ("last {lookback_days} days"[crdt]))')
        params = {"db": "pubmed", "term": f'({query}) AND ({date_filter})', "retmode": "json", "retmax": int(cfg.get("results_per_query", 30)), "sort": "pub date"}
        if email: params["email"] = email
        if api_key: params["api_key"] = api_key
        try:
            response = session.get(PUBMED_ESEARCH_URL, params=params, timeout=(10, 35)); response.raise_for_status()
            ids = response.json().get("esearchresult", {}).get("idlist", [])
            if ids:
                fetch_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
                if email: fetch_params["email"] = email
                if api_key: fetch_params["api_key"] = api_key
                detail = session.get(PUBMED_EFETCH_URL, params=fetch_params, timeout=(10, 45)); detail.raise_for_status()
                root = ET.fromstring(detail.text)
                for node in root.findall(".//PubmedArticle"):
                    item = _normalise_pubmed_xml(node)
                    if item: results.append(item)
            status["queries_succeeded"] += 1
            time.sleep(float(cfg.get("source_pause_seconds", 0.35)))
        except Exception as exc:
            status["errors"].append(f"PubMed 查询 {query!r} 失败：{_safe_error(exc)}")
            logging.warning("PubMed query failed: %s", _safe_error(exc))
    status["records_received"] = len(results)
    return results, status


def fetch_publisher_crossref(cfg: dict[str, Any], lookback_days: int, session: requests.Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use Crossref metadata search, then strictly retain records whose DOI URL belongs to an allowed publisher domain."""
    today = date.today(); first_day = today - timedelta(days=lookback_days)
    status = {name: {"queries_attempted": 0, "queries_succeeded": 0, "records_received": 0, "errors": []}
              for name in PUBLISHER_DOMAINS}
    results: list[dict[str, Any]] = []
    for query in cfg.get("search_queries", []):
        for publisher, domains in PUBLISHER_DOMAINS.items():
            st = status[publisher]; st["queries_attempted"] += 1
            params = {"query.bibliographic": query, "filter": f"from-pub-date:{first_day.isoformat()},until-pub-date:{today.isoformat()},type:journal-article", "rows": min(int(cfg.get("results_per_query", 30)), 100), "select": "DOI,title,author,published,created,URL,link,abstract,container-title,type"}
            email = cfg.get("contact_email", "").strip()
            headers = {"User-Agent": f"DailyResearchPaperAgent/3.1 (mailto:{email})"} if email else {}
            try:
                response = session.get(CROSSREF_URL, params=params, headers=headers, timeout=(10, 35)); response.raise_for_status()
                payload = response.json().get("message", {}).get("items", [])
                st["queries_succeeded"] += 1
                for raw in payload:
                    doi = _normalise_doi(raw.get("DOI"))
                    # Prefer an explicit publisher-hosted landing/content URL from Crossref links.
                    links = raw.get("link") or []
                    publisher_url = next((x.get("URL", "") for x in links if _publisher_allowed(x.get("URL", ""), publisher)), "")
                    doi_prefixes = {"Nature": ("10.1038/", "10.1057/"), "ACS Publications": ("10.1021/",), "Wiley Online Library": ("10.1002/", "10.1111/")}
                    # Crossref often exposes only a doi.org resolver. In that case use the publisher DOI namespace
                    # as a conservative fallback and construct the canonical publisher URL, not a doi.org URL.
                    prefix_match = bool(doi and doi.lower().startswith(doi_prefixes.get(publisher, ())))
                    if not doi or (not publisher_url and not prefix_match):
                        continue
                    if publisher_url:
                        url = publisher_url
                    elif publisher == "Nature":
                        url = f"https://www.nature.com/articles/{doi.split('/', 1)[1]}"
                    elif publisher == "ACS Publications":
                        url = f"https://pubs.acs.org/doi/{doi}"
                    else:
                        url = f"https://onlinelibrary.wiley.com/doi/{doi}"
                    title_list = raw.get("title") or []
                    title = re.sub(r"\s+", " ", str(title_list[0] if title_list else "")).strip()
                    if not title: continue
                    published = raw.get("published", {}).get("date-parts", [[]])[0]
                    precision = "unknown"
                    try:
                        if len(published) >= 3:
                            pubdate = date(int(published[0]), int(published[1]), int(published[2])).isoformat(); precision = "day"
                        elif len(published) == 2:
                            pubdate = f"{int(published[0]):04d}-{int(published[1]):02d}"; precision = "month"
                        elif len(published) == 1:
                            pubdate = f"{int(published[0]):04d}"; precision = "year"
                        else: pubdate = ""
                    except (ValueError, TypeError, IndexError): pubdate = ""
                    authors = [" ".join(x for x in [a.get("given", ""), a.get("family", "")] if x) for a in (raw.get("author") or [])[:8]]
                    abstract = re.sub(r"<[^>]+>", " ", str(raw.get("abstract") or ""))
                    item = {"title": title, "abstract": re.sub(r"\s+", " ", abstract).strip(), "publication_date": pubdate,
                        "publication_date_precision": precision,
                        "authors": [a for a in authors if a], "doi": doi, "url": url, "pdf_url": "", "pdf_urls": [],
                        "source_ids": [f"doi:{doi}"], "sources": [publisher], "record_type": "journal-article",
                        "venue": (raw.get("container-title") or [publisher])[0], "cited_by_count": 0,
                        "is_open_access": False, "abstract_origin": "Crossref" if abstract else "unavailable"}
                    results.append(item); st["records_received"] += 1
                time.sleep(float(cfg.get("source_pause_seconds", 0.35)))
            except Exception as exc:
                st["errors"].append(f"{publisher} 查询 {query!r} 失败：{_safe_error(exc)}")
                logging.warning("%s query failed: %s", publisher, _safe_error(exc))
    return results, status


def _journal_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _load_journal_metrics(cfg: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    """Load journal-level JIF values from a user-maintained, year-labelled JSON file."""
    from pathlib import Path
    import json
    path = Path(__file__).resolve().parent.parent / cfg.get("journal_metrics_file", "journal_metrics.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, str(path)
    except Exception as exc:
        logging.warning("Journal metrics file could not be read: %s", _safe_error(exc))
        return {}, str(path)
    metrics: dict[str, dict[str, Any]] = {}
    # Schema: {"metric_year": 2025, "journals": {"Exact Journal Title": {"jif": 13.2, "year": 2025, "source_url": "..."}}}
    for journal, record in (raw.get("journals", {}) if isinstance(raw, dict) else {}).items():
        if isinstance(record, (int, float)):
            record = {"jif": float(record), "year": raw.get("metric_year"), "source_url": ""}
        if not isinstance(record, dict):
            continue
        try:
            jif = float(record.get("jif"))
            year = int(record.get("year", raw.get("metric_year", 0)))
        except (TypeError, ValueError):
            continue
        if jif > 0 and year > 0:
            normalized = {"jif": jif, "year": year, "source_url": str(record.get("source_url", ""))}
            for alias in [journal, *record.get("aliases", [])]:
                key = _journal_key(str(alias))
                if key:
                    metrics[key] = normalized
    return metrics, str(path)


def filter_by_journal_impact_factor(papers: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    threshold = float(cfg.get("minimum_journal_impact_factor", 12.0))
    metric_year = int(cfg.get("journal_impact_factor_year", 2025))
    metrics, metric_path = _load_journal_metrics(cfg)
    accepted: list[dict[str, Any]] = []
    unknown = 0
    below = 0
    stale = 0
    for paper in papers:
        key = _journal_key(str(paper.get("venue", "")))
        metric = metrics.get(key)
        if not metric:
            unknown += 1
            continue  # strict mode: never assume an unknown JIF exceeds the threshold
        if int(metric["year"]) != metric_year:
            stale += 1
            continue
        paper["journal_impact_factor"] = float(metric["jif"])
        paper["journal_impact_factor_year"] = int(metric["year"])
        paper["journal_impact_factor_source"] = metric.get("source_url", "")
        if float(metric["jif"]) > threshold:
            accepted.append(paper)
        else:
            below += 1
    return accepted, {"minimum_exclusive": threshold, "metric_year": metric_year,
                      "records_after_filter": len(accepted), "excluded_unknown_jif": unknown,
                      "excluded_below_threshold": below, "excluded_wrong_metric_year": stale,
                      "metrics_file": metric_path, "strict_unknown_policy": "exclude"}


def fetch_all_candidates(cfg: dict[str, Any], lookback_days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contact = cfg.get("contact_email", "").strip() or "local-user"
    session = make_session(f"DailyResearchPaperAgent/3.1 (contact: {contact})")
    pubmed, pubmed_status = fetch_pubmed(cfg, lookback_days, session)
    publishers, publisher_status = fetch_publisher_crossref(cfg, lookback_days, session)
    # Exact requested scope only: PubMed plus publisher-domain-verified Nature, ACS, and Wiley records.
    all_records = pubmed + publishers
    deduped: dict[str, dict[str, Any]] = {}
    for item in all_records:
        doi = _normalise_doi(item.get("doi"))
        key = f"doi:{doi}" if doi else (item.get("source_ids") or [item.get("title", "")])[0]
        if key in deduped:
            old = deduped[key]
            old["sources"] = sorted(set(old.get("sources", []) + item.get("sources", [])))
            if len(item.get("abstract", "")) > len(old.get("abstract", "")):
                old["abstract"] = item["abstract"]; old["abstract_origin"] = item.get("abstract_origin", "")
            old["source_ids"] = sorted(set(old.get("source_ids", []) + item.get("source_ids", [])))
        else: deduped[key] = item
    deduped_records = list(deduped.values())
    filtered, impact_status = filter_by_journal_impact_factor(deduped_records, cfg)
    status = {"lookback_days": lookback_days, "PubMed": pubmed_status, **publisher_status,
              "impact_factor_filter": impact_status,
              "records_before_impact_factor_filter": len(deduped_records),
              "errors": pubmed_status["errors"] + [e for st in publisher_status.values() for e in st["errors"]],
              "scope_note": "仅检索 PubMed 与 Nature、ACS Publications、Wiley Online Library 域名核验记录；Crossref 用于元数据发现，不扩大出版商范围。",
              "date_filter_note": "PubMed 按出版日期或近期建档日期检索；日期显示保留来源实际精度，不将仅有年份的记录伪装成 YYYY-01-01。"}
    return filtered, status


def allowed_pdf_url(url: str, allowed_hosts: list[str]) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".local", ".internal")):
            return False
        return any(host == allowed.lower() or host.endswith("." + allowed.lower()) for allowed in allowed_hosts if allowed)
    except ValueError:
        return False


def fetch_open_pdf_text(paper: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    options = cfg.get("full_text", {})
    meta = {"status": "disabled", "pages_read": 0, "chars": 0, "url": ""}
    if not options.get("enabled", True):
        return "", meta
    urls = list(dict.fromkeys([u for u in (paper.get("pdf_urls") or [paper.get("pdf_url", "")]) if u]))
    if not urls:
        meta["status"] = "no_direct_pdf_link"
        return "", meta
    allowlist = options.get("allowed_pdf_hosts", ["arxiv.org", "pmc.ncbi.nlm.nih.gov", "europepmc.org", "biorxiv.org", "medrxiv.org", "chemrxiv.org"])
    allowed_urls = [url for url in urls if allowed_pdf_url(url, allowlist)]
    if not allowed_urls:
        meta["status"] = "host_not_allowlisted"
        return "", meta
    try:
        import io
        from pypdf import PdfReader
    except ImportError:
        meta["status"] = "pypdf_not_installed"
        return "", meta

    max_bytes = int(options.get("max_bytes", 15_000_000))
    timeout = int(options.get("timeout_seconds", 30))
    errors: list[str] = []
    for url in allowed_urls:
        try:
            session = make_session("DailyResearchPaperAgent/2.0 (open-access PDF retrieval)")
            with session.get(url, stream=True, timeout=(10, timeout), allow_redirects=True) as response:
                response.raise_for_status()
                final_url = response.url
                if not allowed_pdf_url(final_url, allowlist):
                    errors.append("redirect_host_not_allowlisted")
                    continue
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    errors.append("pdf_too_large")
                    continue
                chunks = []
                total = 0
                too_large = False
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        too_large = True
                        break
                    chunks.append(chunk)
                if too_large:
                    errors.append("pdf_too_large")
                    continue
            data = b"".join(chunks)
            if not data.startswith(b"%PDF"):
                errors.append("not_a_pdf")
                continue
            reader = PdfReader(io.BytesIO(data), strict=False)
            page_limit = int(options.get("max_pages", 18))
            char_limit = int(options.get("max_chars", 28_000))
            text_parts = []
            pages_processed = 0
            chars_so_far = 0
            for page in reader.pages[:page_limit]:
                pages_processed += 1
                piece = (page.extract_text() or "").strip()
                if piece:
                    text_parts.append(piece)
                    chars_so_far += len(piece)
                if chars_so_far >= char_limit:
                    break
            text = "\n\n".join(text_parts)[:char_limit]
            if not text:
                errors.append("pdf_text_empty")
                continue
            meta.update({"status": "ok", "pages_read": pages_processed, "chars": len(text), "url": final_url})
            return text, meta
        except Exception as exc:
            errors.append(f"{type(exc).__name__}")
            logging.warning("Open PDF extraction failed for %s (%s): %s", paper.get("title"), urlparse(url).hostname, type(exc).__name__)
    meta["status"] = "all_pdf_attempts_failed:" + ",".join(errors[:4])
    return "", meta

