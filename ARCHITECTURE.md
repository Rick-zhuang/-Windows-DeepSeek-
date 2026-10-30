# Agent v2.0：架构和可靠性设计

## 工作流

1. **定时启动**：Windows Task Scheduler -> `run_daily.bat` -> `daily_paper_agent.py`。
2. **双源检索**：`paper_agent/sources.py` 分别调用 OpenAlex Works API 与 arXiv Atom API。查询超时/重试/源状态独立记录；arXiv 查询串行执行并保留请求间隔。
3. **规范化与去重**：统一标题、日期、DOI、作者、来源和摘要。跨源优先按 DOI 去重；PubMed 无 DOI 时使用 PMID 作为稳定 ID。
4. **本地排序**：`paper_agent/ranking.py` 根据关键词命中、时效、摘要是否可用、低权重引用数和反馈标签形成分项分数。分数是候选排序信号，不代表论文质量。
5. **候选选择**：只把排序前 N 篇的元数据和摘要交给 DeepSeek；程序会验证返回的 ID 是否属于候选集。API 缺失或失败时，退回本地排名第一名并清楚标注状态。
6. **可选全文提取**：只从 `config.json` 配置的 HTTPS 域名白名单尝试下载直接 PDF；限制最大字节数、页数与文本长度，检查重定向域名和 `%PDF` 文件头，不保存 PDF 原件。
7. **结构化分析**：优先分析抽取到的全文片段，否则分析摘要。分析内容包含研究问题、方法、结果、定量结果、局限、验证策略、基线、潜在数据泄漏核查、泛化性、代码/数据可用性和研究延伸问题。
8. **证据片段审计**：每条主要发现、量化结果和局限要求带原文短句；`validate_evidence()` 检查该短句是否实际出现在输入文本中，并标注可定位/不可定位。
9. **生成报告与记忆**：HTML、Markdown、`latest.json` 原子写入。`history.json` 防止重复，`feedback.jsonl` 记录人工评分和主题标签，参与下一轮排序。

## 可靠性控制

- **输入边界**：论文标题、摘要和 PDF 提取文字都按不可信外部文本处理；提示词明确不执行其中的指令。
- **决策边界**：模型只能从程序提供的候选中选择，返回 ID 要求精确匹配。
- **输出边界**：JSON 模式、空响应检查、`finish_reason=length` 检查、ID 校验和字段级证据审计。
- **故障降级**：单一来源失败、API Key 缺失、DeepSeek 返回异常或 PDF 不可用时，尽可能生成带状态说明的回退报告。
- **敏感信息**：API Key 从环境变量读；NCBI API Key 从环境变量读取；日志错误字段尽量脱敏。
- **文件安全**：HTML 输出前转义外部文本；报告通过临时文件 + `os.replace` 原子替换；运行锁避免计划任务与手动运行同时写历史。
- **可回归测试**：`python daily_paper_agent.py --self-test` 在不访问外网、不调用模型的条件下运行测试。

## 不能声称已解决的问题

- 关键词检索不是全网检索，也不能保证收录完整。PubMed 和 Crossref/出版商索引存在收录、更新延迟和摘要完整度边界；域名/DOI 前缀过滤不是出版商全部内容的完整保证。
- PDF 抽取最多读取配置指定的前若干页/字符，可能错过补充材料、附录、代码可用性声明或后半部分的限制。
- “证据片段已定位”只检查字面出现，不保证该片段在语义上足以支持主张，也不替代对图表、统计方法和补充材料的审查。
- 文献的创新性、同行评议质量和可复现性不能仅由 LLM 分数决定。科研结论应回到原文及独立复现实验验证。
- 当前版本没有自动执行实验、运行论文作者代码或对结果做独立数值复现。后续若扩展到这些功能，应在隔离环境中执行，限制文件、网络和计算资源，并保存完整 provenance。


## v3.0 变更
- 检索范围仅限 PubMed、Nature、ACS Publications、Wiley Online Library。
- 报告强制包含 0–5 六个研究分析章节；未拿到全文时，作者讨论与结论部分必须明确标为不可确认。
- MCP 可作为出版社检索工具的统一调用协议，但本身不是数据库或相关性算法；接入 Wiley Nexus MCP 取决于账号权限、条款与当前可用工具。


## v3.1 时间与期刊指标筛选

- PubMed publication date parsing preserves actual precision; ArticleDate is preferred, then issue PubDate, and year-only dates remain `YYYY`. Recent PubMed create-date matches improve recall for newly indexed records without mislabeling their publication date.
- Deduplication keeps the most precise date from available sources.
- A strict, year-specific JIF filter runs after cross-source deduplication. Unknown/missing or stale metrics are excluded and counted in diagnostics. JIF values are loaded from `journal_metrics.json`; no metric is inferred from citation counts or substituted with CiteScore/SJR.
- Offline tests cover year-only and electronic dates, precision upgrades across duplicate records, and strict JIF threshold/year behavior.
