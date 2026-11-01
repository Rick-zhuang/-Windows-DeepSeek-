from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


def _md_list(items: Any, default: str = "- 暂无可靠信息") -> list[str]:
    if not isinstance(items, list) or not items:
        return [default]
    lines = []
    for item in items:
        if isinstance(item, dict):
            claim = str(item.get("claim", "未提供主张")).strip()
            quote = str(item.get("evidence_quote", "")).strip()
            status = str(item.get("evidence_status", "证据未审计"))
            kind = str(item.get("kind", ""))
            lines.append(f"- **{kind or '条目'}：** {claim}")
            lines.append(f"  - **证据检查：** {status}")
            if quote:
                lines.append(f"  - **原文片段：** “{quote}”")
        else:
            lines.append(f"- {str(item).strip()}")
    return lines


def build_markdown(paper: dict[str, Any], summary: dict[str, Any], selection: dict[str, Any],
                   source_status: dict[str, Any], fulltext_meta: dict[str, Any], cfg: dict[str, Any],
                   generated: datetime) -> str:
    abstract = paper.get("abstract", "").strip() or "检索源未提供摘要。请打开原文核查。"
    url = paper.get("url", "")
    doi = paper.get("doi", "")
    paper_url = url if url.startswith("http") else (f"https://doi.org/{doi}" if doi else "未提供")
    authors = ", ".join(paper.get("authors", [])) or "数据库未提供"
    audit = summary.get("evidence_audit", {})
    source_summary = []
    for name in ("PubMed", "Nature", "ACS Publications", "Wiley Online Library"):
        item = source_status.get(name, {})
        source_summary.append(
            f"- **{name}：** 查询成功 {item.get('queries_succeeded', 0)}/{item.get('queries_attempted', 0)}；"
            f"原始记录 {item.get('records_received', 0)} 篇"
        )
    if source_status.get("impact_factor_filter"):
        f = source_status["impact_factor_filter"]
        source_summary += [
            "", f"- **JIF 筛选：** 要求 JIF > {f.get('minimum_exclusive')}，数据年 {f.get('metric_year')}；筛后 {f.get('records_after_filter')} 篇；未知 JIF 排除 {f.get('excluded_unknown_jif')} 篇；低于阈值排除 {f.get('excluded_below_threshold')} 篇。"
        ]
    if source_status.get("errors"):
        source_summary += ["", "**检索告警：**"]
        source_summary.extend(f"- {err}" for err in source_status["errors"][:8])
    score_lines = [f"- `{key}`：{value}" for key, value in paper.get("score_breakdown", {}).items()]
    lines = [
        f"# 每日前沿文献｜{generated:%Y-%m-%d}",
        "",
        f"## {paper.get('title', '无标题')}",
        "",
        f"- **出版/提交日期：** {paper.get('publication_date') or '未提供'}（精度：{paper.get('publication_date_precision', '未标注')}；年份级记录不代表 1 月 1 日发表）",
        f"- **作者：** {authors}",
        f"- **期刊/来源：** {paper.get('venue') or '数据库未提供'}",
        f"- **期刊影响因子（JIF）：** {paper.get('journal_impact_factor', '未核验')}（数据年：{paper.get('journal_impact_factor_year', '未提供')}；仅为期刊级指标，不代表单篇论文质量）",
        f"- **JIF 数据来源：** {paper.get('journal_impact_factor_source') or '本地 journal_metrics.json；请核对记录与来源链接'}",
        f"- **记录类型：** {paper.get('record_type') or '未知'}",
        f"- **来源数据库：** {', '.join(paper.get('sources', [])) or '未知'}",
        f"- **DOI：** {doi or '未提供'}",
        f"- **原文链接：** {paper_url}",
        f"- **开放 PDF 链接：** {paper.get('pdf_url') or '没有检测到直接 PDF 链接'}",
        f"- **OpenAlex 被引次数：** {paper.get('cited_by_count', 0)}（会随数据库更新，不用于证明论文质量）",
        "",
        "## 为什么推荐这篇",
        f"- **本地相关性分数：** {paper.get('score', '未知')}（启发式排序分，不是论文质量分）",
        *score_lines,
        f"- **DeepSeek 筛选状态：** {selection.get('status', 'unknown')}",
        f"- **相关性评分：** {selection.get('relevance_score') if selection.get('relevance_score') is not None else '未评分'}/5",
        f"- **潜在新颖性信号：** {selection.get('potential_novelty_score') if selection.get('potential_novelty_score') is not None else '未评分'}/5（模型判断，不是已验证的创新性）",
        selection.get("selection_reason", "未提供选择理由"),
        "",
        "## 0. 摘要（中文概述）",
        str(summary.get("abstract_summary", summary.get("abstract_paraphrase", "未生成"))),
        "",
        "## 1. 本文的研究背景",
        str(summary.get("research_background", "未说明；请阅读全文核查。")),
        "",
        "## 2. 面临的科学问题",
        str(summary.get("scientific_problem", summary.get("research_question", "未说明；请阅读全文核查。"))),
        "",
        "## 3. 本文提出的研究策略、实验与结果",
        str(summary.get("strategy_experiments_results", summary.get("approach", "未说明；请阅读全文核查。"))),
        "",
        "### 数据集、基线与评价设置",
        str(summary.get("data_and_benchmarks", "未说明")),
        "",
        "## 作者报告的主要发现",
        *_md_list(summary.get("main_findings")),
        "",
        "## 量化结果",
        *_md_list(summary.get("quantitative_results")),
        "",
        "## 4. 作者如何讨论这些结果",
        str(summary.get("authors_discussion", "未说明；当前可用文本不足以确认作者讨论。")),
        "",
        "## 5. 全文末尾的总结与展望",
        str(summary.get("conclusion_outlook", "未说明；若未获取全文，不能可靠总结结尾部分。")),
        "",
        "## 局限与需核查风险",
        *_md_list(summary.get("limitations")),
        "",
        "## 方法学审查（不是论文作者结论）",
        *([f"- **{key}：** {value}" for key, value in (summary.get("critical_appraisal", {}) or {}).items()] if isinstance(summary.get("critical_appraisal", {}), dict) else ["- 未提供方法学审查"]),
        "",
        "## 可复现性检查清单",
        *_md_list(summary.get("reproducibility_checks")),
        "",
        "## 与我的研究可能的联系（研究建议，不是论文结论）",
        *_md_list(summary.get("questions_for_your_research")),
        "",
        "## 证据审计",
        f"- **主张引用中可定位到原文的片段：** {audit.get('quote_found', 0)} 条",
        f"- **未能定位的片段：** {audit.get('quote_not_found', 0)} 条",
        f"- **可审计范围：** {audit.get('source_scope', '未说明')}",
        "- 片段被定位只说明字符串出现在输入文本中，不代表它充分支持整条主张；需要人工判断语义与上下文。",
        "",
        "## 中文概述",
        str(summary.get("abstract_paraphrase", "未生成")),
        "",
        "## 原文摘要（检索源提供）",
        abstract,
        "",
        "## 全文提取状态",
        f"- 状态：`{fulltext_meta.get('status', 'unknown')}`",
        f"- 读取页数：{fulltext_meta.get('pages_read', 0)}；提取字符数：{fulltext_meta.get('chars', 0)}",
        f"- 分析依据：{summary.get('analysis_basis', '摘要或元数据')}",
        "",
        "## 检索覆盖情况",
        f"- 检索时间窗：最近 {source_status.get('lookback_days', cfg.get('lookback_days', 45))} 天",
        *source_summary,
        "",
        "## 证据与可靠性说明",
        str(summary.get("evidence_note", "本报告是辅助筛选工具，不替代人工阅读全文。")),
        "- 检索范围限定为 PubMed、Nature、ACS Publications 与 Wiley Online Library；元数据通过各自可用的 API/索引发现，并进行出版商域名核验。并非这些网站的全部文章都一定可被接口检出。",
        "- 如使用全文分析，仅尝试配置中允许的开放 PDF 来源；不会保存下载的 PDF 文件。",
        "- 数值结果、实验条件、基线和因果结论应回到论文原文及补充材料逐项核验。",
        "",
        f"**生成时间：** {generated.isoformat(timespec='seconds')}",
        "",
        "> Daily Research Paper Agent · PubMed + Nature + ACS Publications + Wiley Online Library + DeepSeek · 自动筛选结果不能保证绝对新颖、完整或正确。",
    ]
    return "\n".join(lines)


def markdown_to_html(markdown_text: str, title: str) -> str:
    # Small safe renderer: all external/source text is HTML-escaped before display.
    body_parts: list[str] = []
    in_list = False
    in_quote = False
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.startswith("# ") or line.startswith("## "):
            if in_list:
                body_parts.append("</ul>"); in_list = False
            if in_quote:
                body_parts.append("</blockquote>"); in_quote = False
            level = 1 if line.startswith("# ") else 2
            body_parts.append(f"<h{level}>{_inline(line[level + 1:])}</h{level}>")
        elif line.startswith("> "):
            if in_list:
                body_parts.append("</ul>"); in_list = False
            if not in_quote:
                body_parts.append("<blockquote>"); in_quote = True
            body_parts.append(f"<p>{_inline(line[2:])}</p>")
        elif line.startswith("- "):
            if in_quote:
                body_parts.append("</blockquote>"); in_quote = False
            if not in_list:
                body_parts.append("<ul>"); in_list = True
            body_parts.append(f"<li>{_inline(line[2:])}</li>")
        elif line.startswith("  - "):
            if in_quote:
                body_parts.append("</blockquote>"); in_quote = False
            if not in_list:
                body_parts.append("<ul>"); in_list = True
            body_parts.append(f"<li>{_inline(line[4:])}</li>")
        elif not line.strip():
            if in_list:
                body_parts.append("</ul>"); in_list = False
            if in_quote:
                body_parts.append("</blockquote>"); in_quote = False
        else:
            if in_list:
                body_parts.append("</ul>"); in_list = False
            if in_quote:
                body_parts.append("</blockquote>"); in_quote = False
            body_parts.append(f"<p>{_inline(line)}</p>")
    if in_list:
        body_parts.append("</ul>")
    if in_quote:
        body_parts.append("</blockquote>")
    body = "\n".join(body_parts)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{color-scheme:light dark}}body{{font-family:system-ui,-apple-system,'Microsoft YaHei',sans-serif;line-height:1.72;max-width:960px;margin:36px auto;padding:0 24px}}h1{{border-bottom:2px solid #888;padding-bottom:.45em}}h2{{margin-top:1.65em;border-bottom:1px solid #888;padding-bottom:.2em}}a{{overflow-wrap:anywhere}}li{{margin:.35em 0}}blockquote{{border-left:4px solid #888;margin:1em 0;padding:.2em 1em;opacity:.9}}p{{overflow-wrap:anywhere}}code{{font-size:.93em}}.note{{color:#888;font-size:.85em}}
</style></head><body>{body}<p class="note">Daily Research Paper Agent v2.0</p></body></html>"""


def _inline(value: str) -> str:
    # Escape first, then handle only a tiny markdown subset. URLs remain clickable only as http(s).
    text = html.escape(value, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    url_pattern = re.compile(r"(https?://[^\s<>]+)")

    def linkify(match: re.Match[str]) -> str:
        escaped_url = match.group(1).rstrip(".,)")
        trailing = match.group(1)[len(escaped_url):]
        # url already HTML escaped; quote escapes are added for the attribute context.
        safe_attr = escaped_url.replace('"', "%22").replace("'", "%27")
        return f'<a href="{safe_attr}" target="_blank" rel="noopener noreferrer">{escaped_url}</a>{trailing}'

    text = url_pattern.sub(linkify, text)
    return text
