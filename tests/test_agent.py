from __future__ import annotations

import os
import sys
import unittest
import tempfile
from argparse import Namespace
from unittest.mock import patch
import xml.etree.ElementTree as ET
from datetime import date, datetime
from unittest.mock import patch
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from paper_agent.ranking import deduplicate_papers, load_feedback_preferences, norm_text, rank_papers
from paper_agent.reporting import build_markdown, markdown_to_html
from paper_agent.sources import _safe_error, allowed_pdf_url, normalise_arxiv, reconstruct_abstract, _publisher_allowed, _normalise_pubmed_xml, filter_by_journal_impact_factor
from paper_agent.summarizer import select_paper, summarize_selected, validate_evidence
import daily_paper_agent as app


class AgentTests(unittest.TestCase):
    def sample_paper(self, **kwargs):
        base = {
            "title": "Equivariant graph neural networks for molecular conformation",
            "abstract": "We propose a graph neural network for molecular conformation prediction. We evaluate on three benchmark datasets.",
            "publication_date": "2026-10-01",
            "authors": ["A. Researcher"], "doi": "10.1234/test.1",
            "url": "https://doi.org/10.1234/test.1", "pdf_url": "",
            "source_ids": ["https://openalex.org/W123"], "sources": ["OpenAlex"],
            "record_type": "article", "venue": "Test Journal", "cited_by_count": 0,
            "is_open_access": False, "abstract_origin": "OpenAlex",
        }
        base.update(kwargs)
        return base

    def test_reconstruct_abstract_orders_tokens(self):
        index = {"world": [2], "Hello": [0], "!": [3], "beautiful": [1]}
        self.assertEqual(reconstruct_abstract(index), "Hello beautiful world !")

    def test_deduplicate_by_title_across_sources(self):
        p1 = self.sample_paper(doi="", sources=["OpenAlex"], source_ids=["https://openalex.org/W123"])
        p2 = self.sample_paper(
            doi="10.1234/test.1", sources=["arXiv"], source_ids=["arxiv:2610.12345"],
            abstract="We propose a graph neural network for molecular conformation prediction. We evaluate on three benchmark datasets and provide additional extensive benchmark details."
        )
        result = deduplicate_papers([p1, p2])
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["sources"]), {"OpenAlex", "arXiv"})
        self.assertTrue(result[0]["id"].startswith("doi:"))
        self.assertIn("additional extensive benchmark details", result[0]["abstract"])

    def test_keyword_and_feedback_scoring(self):
        paper = self.sample_paper()
        cfg = {"keywords": ["equivariant", "GNN"], "negative_keywords": []}
        ranked = rank_papers([paper], cfg, date(2026, 10, 29), {"equivariant": 1.0})
        self.assertGreater(ranked[0]["score_breakdown"]["keyword_title"], 0)
        self.assertGreater(ranked[0]["score_breakdown"]["feedback"], 0)

    def test_feedback_tag_profiles(self):
        weights, tags = load_feedback_preferences([
            {"rating": 5, "tags": ["GNN", "3D structure"]},
            {"rating": 1, "tags": ["survey"]},
        ])
        self.assertGreater(weights[norm_text("GNN")], 0)
        self.assertLess(weights[norm_text("survey")], 0)
        self.assertIn("GNN", tags)

    def test_evidence_quote_audit(self):
        paper = self.sample_paper()
        summary = {"main_findings": [
            {"claim": "Uses GNN", "evidence_quote": "We propose a graph neural network for molecular conformation prediction."},
            {"claim": "Massive improvement", "evidence_quote": "Accuracy improved by 91 percent over all baselines."},
        ], "quantitative_results": [], "limitations": []}
        result = validate_evidence(summary, paper, "")
        self.assertEqual(result["evidence_audit"]["quote_found"], 1)
        self.assertEqual(result["evidence_audit"]["quote_not_found"], 1)

    def test_pdf_allowlist_rejects_untrusted_hosts(self):
        allowlist = ["arxiv.org", "pmc.ncbi.nlm.nih.gov"]
        self.assertTrue(allowed_pdf_url("https://arxiv.org/pdf/2610.12345", allowlist))
        self.assertTrue(allowed_pdf_url("https://export.arxiv.org/pdf/2610.12345", allowlist))
        self.assertFalse(allowed_pdf_url("http://arxiv.org/pdf/2610.12345", allowlist))
        self.assertFalse(allowed_pdf_url("https://example.com/paper.pdf", allowlist))
        self.assertFalse(allowed_pdf_url("https://127.0.0.1/paper.pdf", ["127.0.0.1"]))

    def test_html_escapes_source_text_and_linkifies_url(self):
        html = markdown_to_html("# Title\n\nText <script>alert(1)</script> https://example.com/paper", "Test")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="https://example.com/paper"', html)

    def test_arxiv_version_id_is_stable(self):
        xml = """<entry xmlns="http://www.w3.org/2005/Atom">
          <id>https://arxiv.org/abs/2610.12345v3</id>
          <title>Test molecular model</title>
          <summary>We test a molecular model.</summary>
          <published>2026-10-20T00:00:00Z</published>
          <author><name>A. Author</name></author>
          <link title="pdf" type="application/pdf" href="https://arxiv.org/pdf/2610.12345v3" />
        </entry>"""
        paper = normalise_arxiv(ET.fromstring(xml))
        self.assertEqual(paper["source_ids"], ["arxiv:2610.12345"])
        self.assertIn("v3", paper["pdf_url"])

    def test_api_key_is_redacted_from_source_errors(self):
        err = RuntimeError("request failed https://api.openalex.org/works?api_key=secretvalue&search=test")
        message = _safe_error(err)
        self.assertNotIn("secretvalue", message)
        self.assertIn("[REDACTED]", message)

    def test_missing_api_key_falls_back_without_hallucinated_summary(self):
        paper = self.sample_paper()
        paper["id"] = "doi:10.1234/test.1"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            selected, meta = select_paper({}, [paper], [])
            summary = summarize_selected({}, selected, meta, "", {"status": "no_pdf"})
        self.assertEqual(selected["id"], paper["id"])
        self.assertEqual(meta["status"], "fallback_no_api_key")
        self.assertEqual(summary["status"], "fallback_no_llm_summary")
        self.assertEqual(summary["main_findings"], [])

    def test_report_includes_source_scope_and_evidence_caveat(self):
        paper = self.sample_paper()
        paper["score"] = 5.2
        paper["score_breakdown"] = {"keyword_title": 3.5}
        paper["sources"] = ["OpenAlex"]
        summary = validate_evidence({
            "main_findings": [{"claim": "A reported finding", "evidence_quote": "We evaluate on three benchmark datasets."}],
            "quantitative_results": [], "limitations": [],
            "critical_appraisal": {"validation_strategy": "未说明"}
        }, paper, "")
        report = build_markdown(
            paper, summary,
            {"status": "fallback_no_api_key", "selection_reason": "local rank"},
            {"lookback_days": 45, "PubMed": {"queries_succeeded": 4, "queries_attempted": 4, "records_received": 20},
             "Nature": {"queries_succeeded": 4, "queries_attempted": 4, "records_received": 12}, "ACS Publications": {}, "Wiley Online Library": {}, "errors": []},
            {"status": "no_direct_pdf_link"}, {}, datetime(2026, 10, 29, 8, 0)
        )
        self.assertIn("原文片段已定位", report)
        self.assertIn("不代表它充分支持整条主张", report)
        self.assertIn("PubMed", report)
        self.assertIn("方法学审查", report)

    def test_mocked_end_to_end_run_writes_report_and_history(self):
        paper = self.sample_paper()
        paper["id"] = "doi:10.1234/test.1"
        paper["score"] = 4.0
        paper["score_breakdown"] = {"keyword_title": 3.5}
        cfg = {
            "search_queries": ["molecular model"], "keywords": ["equivariant"],
            "research_focus": "AI for chemistry", "lookback_days": 45,
            "max_candidates_to_llm": 5, "history_retention_days": 730,
            "output_dir": "reports", "open_report_after_run": False,
            "full_text": {"enabled": False},
        }
        source_status = {
            "lookback_days": 45,
            "PubMed": {"queries_succeeded": 1, "queries_attempted": 1, "records_received": 1, "errors": []},
            "Nature": {"queries_succeeded": 1, "queries_attempted": 1, "records_received": 0, "errors": []},
            "ACS Publications": {}, "Wiley Online Library": {},
            "errors": [],
        }
        selection = {"status": "fallback_no_api_key", "selection_reason": "test fallback", "relevance_score": None, "potential_novelty_score": None}
        summary = {
            "status": "fallback_no_llm_summary", "research_question": "not tested",
            "approach": "not tested", "data_and_benchmarks": "not specified",
            "main_findings": [], "quantitative_results": [], "limitations": [],
            "critical_appraisal": {"validation_strategy": "未说明"},
            "reproducibility_checks": ["Check data splits"],
            "questions_for_your_research": ["Could this method transfer?"],
            "abstract_paraphrase": "No model summary", "evidence_note": "Mocked integration test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg["output_dir"] = str(tmp_path / "reports")
            with patch.object(app, "setup_logging", lambda: None), \
                 patch.object(app, "load_config", return_value=cfg), \
                 patch.object(app, "fetch_all_candidates", return_value=([paper], source_status)), \
                 patch.object(app, "select_paper", return_value=(paper, selection)), \
                 patch.object(app, "fetch_open_pdf_text", return_value=("", {"status": "disabled", "pages_read": 0, "chars": 0})), \
                 patch.object(app, "summarize_selected", return_value=summary), \
                 patch.object(app, "HISTORY_PATH", tmp_path / "history.json"), \
                 patch.object(app, "FEEDBACK_PATH", tmp_path / "feedback.jsonl"), \
                 patch.object(app, "LOCK_PATH", tmp_path / ".agent.lock"), \
                 patch.object(app, "LOG_PATH", tmp_path / "agent.log"):
                code = app.run(Namespace(days=None, dry_run=False, no_open=True))
            self.assertEqual(code, 0)
            self.assertTrue((tmp_path / "reports" / "latest.html").exists())
            self.assertTrue((tmp_path / "reports" / "latest.md").exists())
            saved_history = __import__("json").loads((tmp_path / "history.json").read_text(encoding="utf-8"))
            self.assertIn("doi:10.1234/test.1", saved_history)
            report = (tmp_path / "reports" / "latest.md").read_text(encoding="utf-8")
            self.assertIn("Test Journal", report)
            self.assertIn("检索覆盖情况", report)

    def test_publisher_domain_scope_is_strict(self):
        self.assertTrue(_publisher_allowed("https://www.nature.com/articles/test", "Nature"))
        self.assertTrue(_publisher_allowed("https://pubs.acs.org/doi/10.1021/test", "ACS Publications"))
        self.assertTrue(_publisher_allowed("https://onlinelibrary.wiley.com/doi/10.1002/test", "Wiley Online Library"))
        self.assertFalse(_publisher_allowed("https://example.com/nature.com", "Nature"))
        self.assertFalse(_publisher_allowed("https://doi.org/10.1038/test", "Nature"))

    def test_pubmed_xml_normalization(self):
        xml = ET.fromstring("""<PubmedArticle><MedlineCitation><PMID>12345</PMID><Article><ArticleTitle>Test article</ArticleTitle><Journal><Title>Test Journal</Title><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal><AuthorList><Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author></AuthorList><Abstract><AbstractText Label="METHODS">We tested a method.</AbstractText></Abstract></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle>""")
        item = _normalise_pubmed_xml(xml)
        self.assertEqual(item["title"], "Test article")
        self.assertEqual(item["doi"], "10.1000/test")
        self.assertEqual(item["source_ids"], ["pubmed:12345"])
        self.assertIn("METHODS: We tested a method.", item["abstract"])

    def test_report_contains_six_requested_sections(self):
        paper = self.sample_paper()
        summary = {"abstract_summary": "A", "research_background": "B", "scientific_problem": "C",
                   "strategy_experiments_results": "D", "authors_discussion": "E", "conclusion_outlook": "F",
                   "main_findings": [], "quantitative_results": [], "limitations": [],
                   "critical_appraisal": {}, "reproducibility_checks": [], "questions_for_your_research": []}
        report = build_markdown(paper, summary, {"status":"test", "selection_reason":"test"},
            {"lookback_days": 7, "PubMed": {}, "Nature": {}, "ACS Publications": {}, "Wiley Online Library": {}, "errors": []},
            {"status":"abstract_only"}, {}, datetime(2026, 10, 29, 8, 0))
        for heading in ["0. 摘要", "1. 本文的研究背景", "2. 面临的科学问题", "3. 本文提出的研究策略", "4. 作者如何讨论", "5. 全文末尾的总结与展望"]:
            self.assertIn(heading, report)



    def test_dedup_prefers_more_precise_publication_date(self):
        year_only = self.sample_paper(publication_date="2026", publication_date_precision="year", sources=["PubMed"])
        exact = self.sample_paper(publication_date="2026-10-27", publication_date_precision="day", sources=["Nature"])
        merged = deduplicate_papers([year_only, exact])[0]
        self.assertEqual(merged["publication_date"], "2026-10-27")
        self.assertEqual(merged["publication_date_precision"], "day")

    def test_pubmed_year_only_date_does_not_become_january_first(self):
        xml = ET.fromstring("""<PubmedArticle><MedlineCitation><PMID>12345</PMID><Article><ArticleTitle>Year precision test</ArticleTitle><Journal><Title>Test Journal</Title><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle>""")
        item = _normalise_pubmed_xml(xml)
        self.assertEqual(item["publication_date"], "2026")
        self.assertEqual(item["publication_date_precision"], "year")

    def test_pubmed_article_date_prefers_real_electronic_date(self):
        xml = ET.fromstring("""<PubmedArticle><MedlineCitation><PMID>12345</PMID><Article><ArticleTitle>Electronic date test</ArticleTitle><ArticleDate DateType="Electronic"><Year>2026</Year><Month>Oct</Month><Day>27</Day></ArticleDate><Journal><Title>Test Journal</Title><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation></PubmedArticle>""")
        item = _normalise_pubmed_xml(xml)
        self.assertEqual(item["publication_date"], "2026-10-27")
        self.assertEqual(item["publication_date_precision"], "day")

    def test_jif_filter_is_strict_and_year_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = Path(tmp) / "metrics.json"
            metrics_path.write_text('{"metric_year":2025,"journals":{"High Journal":{"jif":12.1,"year":2025},"Low Journal":{"jif":12.0,"year":2025},"Old Metric Journal":{"jif":20,"year":2024}}}', encoding="utf-8")
            cfg = {"minimum_journal_impact_factor": 12, "journal_impact_factor_year": 2025, "journal_metrics_file": str(metrics_path)}
            papers = [
                {"title":"A", "venue":"High Journal"},
                {"title":"B", "venue":"Low Journal"},
                {"title":"C", "venue":"Unknown Journal"},
                {"title":"D", "venue":"Old Metric Journal"},
            ]
            kept, status = filter_by_journal_impact_factor(papers, cfg)
            self.assertEqual([p["title"] for p in kept], ["A"])
            self.assertEqual(status["excluded_unknown_jif"], 1)
            self.assertEqual(status["excluded_below_threshold"], 1)
            self.assertEqual(status["excluded_wrong_metric_year"], 1)
            self.assertEqual(kept[0]["journal_impact_factor"], 12.1)

    def test_v1_history_source_id_prevents_immediate_repeat(self):
        paper = self.sample_paper()
        paper["id"] = "doi:10.1234/test.1"
        history = {"https://openalex.org/W123": "2026-10-25"}
        normalized = app._normalise_history(history, 730)
        self.assertTrue(app._is_seen(paper, normalized))


if __name__ == "__main__":
    unittest.main(verbosity=2)
