from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from paper_agent.ranking import load_feedback_preferences, rank_papers
from paper_agent.reporting import build_markdown, markdown_to_html
from paper_agent.sources import fetch_all_candidates, fetch_open_pdf_text
from paper_agent.storage import RunLock, atomic_write_json, atomic_write_text, load_feedback, load_json
from paper_agent.summarizer import select_paper, summarize_selected, validate_evidence

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
HISTORY_PATH = BASE_DIR / "history.json"
FEEDBACK_PATH = BASE_DIR / "feedback.jsonl"
LOG_PATH = BASE_DIR / "agent.log"
LOCK_PATH = BASE_DIR / ".agent.lock"


def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(console)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"找不到配置文件：{CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        cfg = json.load(file)
    for required in ("search_queries", "keywords"):
        if not isinstance(cfg.get(required), list) or not cfg[required]:
            raise ValueError(f"config.json 中 {required} 必须是非空列表")
    target_journals = cfg.get("target_journals", [])
    if target_journals is None:
        target_journals = []
    if not isinstance(target_journals, list) or any(not isinstance(x, str) or not x.strip() for x in target_journals):
        raise ValueError("config.json 中 target_journals 必须是字符串列表；不需要期刊限定时设为 []")
    cfg["target_journals"] = list(dict.fromkeys(x.strip() for x in target_journals))
    cfg.setdefault("lookback_days", 45)
    cfg.setdefault("max_candidates_to_llm", 12)
    cfg.setdefault("output_dir", "reports")
    cfg.setdefault("open_report_after_run", True)
    cfg.setdefault("full_text", {"enabled": True})
    return cfg


def _output_dir(cfg: dict[str, Any]) -> Path:
    value = Path(cfg.get("output_dir", "reports"))
    return value if value.is_absolute() else BASE_DIR / value


def _is_seen(paper: dict[str, Any], history: dict[str, Any]) -> bool:
    if paper.get("id") in history:
        return True
    return any(source_id in history for source_id in paper.get("source_ids", []))


def _normalise_history(history: dict[str, Any], retention_days: int) -> dict[str, Any]:
    cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
    cleaned: dict[str, Any] = {}
    for key, value in history.items():
        if isinstance(value, str):
            record_date = value
        elif isinstance(value, dict):
            record_date = str(value.get("date", ""))
        else:
            record_date = ""
        if not record_date or record_date >= cutoff:
            # Preserve v1 source-ID keys so prior recommendations are not immediately repeated.
            cleaned[key] = {"date": record_date} if isinstance(value, str) else value
    return cleaned


def _safe_summary_from_failure() -> dict[str, Any]:
    return {"status": "no_candidate", "relevance_score": None, "potential_novelty_score": None,
            "selection_reason": "检索未返回可用论文，因此未调用模型。"}


def _write_report(output_dir: Path, date_name: str, paper: dict[str, Any] | None,
                  summary: dict[str, Any], selection: dict[str, Any], source_status: dict[str, Any],
                  fulltext_meta: dict[str, Any], cfg: dict[str, Any], generated: datetime) -> tuple[Path, Path]:
    if paper:
        markdown = build_markdown(paper, summary, selection, source_status, fulltext_meta, cfg, generated)
        latest = {
            "id": paper.get("id", ""), "title": paper.get("title", ""),
            "publication_date": paper.get("publication_date", ""), "doi": paper.get("doi", ""),
            "url": paper.get("url", ""), "abstract": paper.get("abstract", ""),
            "sources": paper.get("sources", []), "report_date": date_name,
        }
        atomic_write_json(output_dir / "latest.json", latest)
    else:
        errors = source_status.get("errors", [])
        lines = [f"# 每日前沿文献｜{date_name}", "", "## 今日未生成推荐", "没有发现符合筛选条件的候选论文，或所有检索源均不可用。", "",
                 f"检索时间窗：最近 {source_status.get('lookback_days', cfg.get('lookback_days', 45))} 天", ""]
        impact = source_status.get("impact_factor_filter", {})
        if impact:
            lines += ["## 影响因子筛选诊断",
                      f"- 要求 JIF > {impact.get('minimum_exclusive')}（指标年份 {impact.get('metric_year')}）",
                      f"- 筛选前去重候选：{source_status.get('records_before_impact_factor_filter', '未知')} 篇",
                      f"- 未知 JIF 排除：{impact.get('excluded_unknown_jif', '未知')} 篇",
                      f"- JIF 未超过阈值排除：{impact.get('excluded_below_threshold', '未知')} 篇",
                      f"- 指标年份不匹配排除：{impact.get('excluded_wrong_metric_year', '未知')} 篇",
                      f"- 指标文件：{impact.get('metrics_file', 'journal_metrics.json')}",
                      "未知影响因子的期刊按严格模式排除；请补充并核验 journal_metrics.json 后重试。", ""]
            missing_targets = impact.get("target_journals_missing_metrics", [])
            if missing_targets:
                lines += ["### target_journals 中缺少 JIF 记录"]
                lines += [f"- {name}" for name in missing_targets]
                lines += ["这些期刊已参与 PubMed 检索，但由于 journal_metrics.json 缺少对应指标，不会通过 JIF 筛选。", ""]
            wrong_year_targets = impact.get("target_journals_wrong_metric_year", [])
            if wrong_year_targets:
                lines += ["### target_journals 中的 JIF 年份不匹配"]
                lines += [f"- {name}" for name in wrong_year_targets]
                lines += [f"这些期刊的指标年份不是 {impact.get('metric_year')}，因此不会通过 JIF 筛选。请更新 journal_metrics.json。", ""]
            unknown_names = impact.get("unknown_journal_counts", {})
            if unknown_names:
                lines += ["### 未匹配到 JIF 的候选期刊"]
                lines += [f"- {name}：{count} 篇" for name, count in sorted(unknown_names.items())]
                lines += [""]
        pubmed_status = source_status.get("PubMed", {})
        if pubmed_status:
            target_names = pubmed_status.get("target_journals", [])
            lines += ["## PubMed 检索情况",
                      f"- 常规主题查询：{pubmed_status.get('topic_queries_succeeded', 0)}/{pubmed_status.get('topic_queries_attempted', 0)} 成功",
                      f"- 期刊限定查询：{pubmed_status.get('target_journal_queries_succeeded', 0)}/{pubmed_status.get('target_journal_queries_attempted', 0)} 成功",
                      f"- 目标期刊：{', '.join(target_names) if target_names else '未配置'}", ""]
        if errors:
            lines += ["## 检索错误"] + [f"- {x}" for x in errors]
        lines += ["", "请检查网络、API 状态、检索词和 agent.log。系统没有用旧论文冒充今日新文献。"]
        markdown = "\n".join(lines)
        atomic_write_json(output_dir / "latest.json", {"id": "", "title": "", "report_date": date_name})
    md_path = output_dir / f"{date_name}_daily_paper.md"
    html_path = output_dir / f"{date_name}_daily_paper.html"
    atomic_write_text(md_path, markdown)
    html_content = markdown_to_html(markdown, f"每日文献｜{date_name}")
    atomic_write_text(html_path, html_content)
    atomic_write_text(output_dir / "latest.md", markdown)
    atomic_write_text(output_dir / "latest.html", html_content)
    return html_path, md_path


def run(args: argparse.Namespace) -> int:
    setup_logging()
    cfg = load_config()
    output_dir = _output_dir(cfg)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().astimezone()
    date_name = generated.strftime("%Y-%m-%d")

    with RunLock(LOCK_PATH):
        lookback = int(args.days if args.days else cfg.get("lookback_days", 45))
        history = load_json(HISTORY_PATH, {})
        if not isinstance(history, dict):
            history = {}
        history = _normalise_history(history, int(cfg.get("history_retention_days", 730)))
        feedback_records = load_feedback(FEEDBACK_PATH)
        feedback_weights, preference_tags = load_feedback_preferences(feedback_records)

        logging.info("任务开始；时间窗=%s 天；PubMed 期刊限定=%s；常规主题检索仍保留；来源=PubMed、Nature、ACS Publications、Wiley Online Library；JIF > %s（数据年 %s）",
                     lookback, cfg.get("target_journals", []), cfg.get("minimum_journal_impact_factor", 12.0), cfg.get("journal_impact_factor_year", 2025))
        raw, source_status = fetch_all_candidates(cfg, lookback)
        ranked = rank_papers(raw, cfg, date.today(), feedback_weights)
        # Prefer papers with abstracts because evidence-grounded analysis needs source text.
        with_abstract = [p for p in ranked if p.get("abstract", "").strip()]
        if with_abstract:
            ranked = with_abstract
        unseen = [p for p in ranked if not _is_seen(p, history)]

        if not unseen and lookback < int(cfg.get("maximum_lookback_days", 365)):
            expanded = max(180, lookback * 3)
            expanded = min(expanded, int(cfg.get("maximum_lookback_days", 365)))
            if expanded > lookback:
                logging.info("当前时间窗没有未推荐论文，扩大到 %d 天后重试。", expanded)
                raw2, status2 = fetch_all_candidates(cfg, expanded)
                raw = raw2
                # Keep source errors from both attempts, but use the expanded query stats for counts.
                source_status = status2
                ranked2 = rank_papers(raw2, cfg, date.today(), feedback_weights)
                with_abstract2 = [p for p in ranked2 if p.get("abstract", "").strip()]
                if with_abstract2:
                    ranked2 = with_abstract2
                unseen = [p for p in ranked2 if not _is_seen(p, history)]
                lookback = expanded
        source_status["lookback_days"] = lookback

        if args.dry_run:
            print("\nDry run：未调用 DeepSeek，不写推荐历史。")
            for i, paper in enumerate(unseen[:10], 1):
                print(f"{i:2}. [score={paper.get('score', 0):.2f}; JIF={paper.get('journal_impact_factor', 'NA')} ({paper.get('journal_impact_factor_year', 'NA')})] {paper.get('publication_date')} | {paper.get('venue', '')} | {paper.get('title')} | {', '.join(paper.get('sources', []))}")
            f = source_status.get("impact_factor_filter", {})
            print(f"JIF 筛选：阈值 > {f.get('minimum_exclusive', cfg.get('minimum_journal_impact_factor', 12))}; 数据年={f.get('metric_year', cfg.get('journal_impact_factor_year', 2025))}; 未知 JIF 排除={f.get('excluded_unknown_jif', 'NA')}; 低于阈值排除={f.get('excluded_below_threshold', 'NA')}")
            if f.get("target_journals"):
                print("PubMed 期刊限定：", ", ".join(f["target_journals"]))
            if f.get("target_journals_missing_metrics"):
                print("警告：以下目标期刊尚未录入 JIF，将被严格筛选排除：", ", ".join(f["target_journals_missing_metrics"]))
            if f.get("target_journals_wrong_metric_year"):
                print("警告：以下目标期刊的 JIF 年份与配置不符，将被严格筛选排除：", ", ".join(f["target_journals_wrong_metric_year"]))
            if f.get("unknown_journal_counts"):
                print("未匹配到 JIF 的期刊：", "; ".join(f"{name} ({count} 篇)" for name, count in sorted(f["unknown_journal_counts"].items())))
            pm = source_status.get("PubMed", {})
            print(f"PubMed 查询：常规主题 {pm.get('topic_queries_succeeded', 0)}/{pm.get('topic_queries_attempted', 0)}；期刊限定 {pm.get('target_journal_queries_succeeded', 0)}/{pm.get('target_journal_queries_attempted', 0)}")
            print("检索状态：", json.dumps({k: {"queries_succeeded": v.get("queries_succeeded"), "queries_attempted": v.get("queries_attempted"), "records_received": v.get("records_received")} for k, v in source_status.items() if isinstance(v, dict)}, ensure_ascii=False))
            return 0

        if not unseen:
            no_selection = _safe_summary_from_failure()
            html_path, md_path = _write_report(output_dir, date_name, None, {}, no_selection, source_status,
                                               {"status": "not_attempted"}, cfg, generated)
            logging.warning("没有可推荐的新论文。HTML：%s", html_path)
            if not raw and source_status.get("errors"):
                return 2
            if cfg.get("open_report_after_run", True) and not args.no_open:
                webbrowser.open(html_path.resolve().as_uri(), new=2)
            return 0

        candidates = unseen[: int(cfg.get("max_candidates_to_llm", 12))]
        print(f"\n检索得到 {len(raw)} 条源记录、{len(ranked)} 篇去重候选；将评估 {len(candidates)} 篇。")
        selected, selection = select_paper(cfg, candidates, preference_tags)
        full_text, fulltext_meta = fetch_open_pdf_text(selected, cfg)
        summary = summarize_selected(cfg, selected, selection, full_text, fulltext_meta)
        summary = validate_evidence(summary, selected, full_text)
        summary["analysis_basis"] = "开放 PDF 提取片段" if full_text else "标题与摘要"

        html_path, md_path = _write_report(output_dir, date_name, selected, summary, selection, source_status,
                                           fulltext_meta, cfg, generated)
        history[selected["id"]] = {
            "date": date_name, "title": selected.get("title", ""),
            "doi": selected.get("doi", ""), "sources": selected.get("sources", []),
        }
        # Keep history bounded so it won't grow forever.
        cutoff = (date.today() - timedelta(days=int(cfg.get("history_retention_days", 730)))).isoformat()
        history = {key: value for key, value in history.items()
                   if not isinstance(value, dict) or value.get("date", "9999-99-99") >= cutoff}
        atomic_write_json(HISTORY_PATH, history)

        logging.info("今日推荐：%s", selected.get("title"))
        logging.info("分析状态：%s；全文提取状态：%s", summary.get("status"), fulltext_meta.get("status"))
        logging.info("HTML 报告：%s", html_path.resolve())
        logging.info("Markdown 报告：%s", md_path.resolve())
        print(f"\n完成！\n标题：{selected.get('title')}\nHTML：{html_path.resolve()}\nMarkdown：{md_path.resolve()}\n分析状态：{summary.get('status')}")
        if cfg.get("open_report_after_run", True) and not args.no_open:
            try:
                webbrowser.open(html_path.resolve().as_uri(), new=2)
            except Exception as exc:
                logging.warning("报告已生成，但自动打开浏览器失败：%s", exc)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="每日科研文献 Agent v3.2")
    parser.add_argument("--days", type=int, default=None, help="覆盖 config.json 的检索时间窗")
    parser.add_argument("--dry-run", action="store_true", help="只检索和排序候选，不调用 DeepSeek、不写推荐历史")
    parser.add_argument("--no-open", action="store_true", help="生成报告但不自动打开浏览器")
    parser.add_argument("--self-test", action="store_true", help="运行本地单元测试，不访问外网")
    args = parser.parse_args()
    if args.self_test:
        import unittest
        from pathlib import Path as _Path
        tests_dir = BASE_DIR / "tests"
        suite = unittest.defaultTestLoader.discover(str(tests_dir))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    try:
        return run(args)
    except Exception as exc:
        setup_logging()
        logging.exception("Agent 运行失败：%s", exc)
        print(f"运行失败：{exc}\n请查看 agent.log 并参考 README.md。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
