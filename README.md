# 每日前沿文献 Agent v3.2（Windows + DeepSeek）

这是一个面向个人科研工作流的本地 Python 项目。默认领域为 AI × 化学/分子建模，重点包括 GNN、三维构象与结构生成、等变网络、扩散模型、AI 酶设计/催化和 G-quadruplex（G4）结构建模。

它是一个“可审计的科研文献筛选助手”，不是能保证论文结论正确的自主科学家。它会保留检索来源、区分元数据/摘要/全文片段、检查模型给出的引用片段是否真实出现在输入文本中，并在 API 失败时降级，而不是把失败伪装成成功。

## v2.0 有哪些改进

- **双源检索**：PubMed + Nature + ACS Publications + Wiley Online Library。每个来源、每个查询分别统计成功/失败；一个来源失败不会自动让另一个来源的结果丢失。出版商站点 多次请求之间强制至少等待 3 秒，遵守其公开 API 使用建议。
- **去重与日期窗口**：优先按 DOI 去重，缺少 DOI 时按规范化标题去重；对近期窗口内已推荐的论文不重复推送，必要时最多扩大到配置的历史窗口。
- **可解释排序**：相关关键词、标题命中、摘要命中、日期新鲜度、摘要是否存在、轻量引用数 tie-breaker 和用户反馈都有单独分数。引用数权重故意很低，避免老论文仅因引用多而获胜。
- **个性化反馈**：执行 `feedback.bat`，给论文打 1–5 分并添加主题标签；后续排序会提高喜欢的主题、降低不喜欢的主题。
- **开放全文可选解析**：在允许的 PDF 域名白名单内，尝试下载不超过指定大小的 PDF，最多提取指定页数/字符数后交给模型。PDF 不会保存到磁盘。其他域名默认不自动下载，避免任意 URL 抓取。
- **证据片段审计**：主要发现、量化结果和局限都要求模型提供输入原文中的连续片段；代码会检查片段是否能在输入文本中定位。注意：片段存在不代表它在语义上充分支持整条主张，仍要人工判断。
- **故障降级**：DeepSeek Key 缺失或 API 调用失败时，仍能用本地排序生成保守的基础报告；来源查询失败会写入日志和报告。不会编造“今日论文”。
- **运行稳定性**：请求重试/超时、原子写文件、滚动日志、并发锁、有限历史记录、离线单元测试。
- **本地输出**：`reports/YYYY-MM-DD_daily_paper.html`、`.md`、`latest.html`、`latest.md` 和 `latest.json`。

## 1. 环境与安装

1. 安装 Python 3.11 或更新版本：<https://www.python.org/downloads/windows/>。安装时勾选 **Add Python to PATH**。
2. 将此项目解压到一个长期保留的目录，例如：
   `C:\Users\你的用户名\Documents\daily_paper_agent`
3. 双击 `setup_windows.bat`。脚本会创建 `.venv` 并安装依赖。
4. 在 DeepSeek 开放平台创建 API Key：<https://platform.deepseek.com/>。
5. 打开 PowerShell，执行：

   ```powershell
   setx DEEPSEEK_API_KEY "你的真实API_Key"
   ```

   关闭并重新打开终端，让环境变量生效。不要把 Key 写进 `config.json`、代码、笔记截图或公开 Git 仓库。

PubMed / Crossref 元数据索引 不提供 API Key 也可以开始使用；如果你有 PubMed / Crossref 元数据索引 Key，可设置 `OPENALEX_API_KEY` 提高可用额度。

## 2. 首次测试

**先做离线测试**（不会访问网络，也不会调用 DeepSeek）：

```powershell
cd "C:\Users\你的用户名\Documents\daily_paper_agent"
.venv\Scripts\python.exe daily_paper_agent.py --self-test
```

接着执行一次只检索和排序的网络测试，不调用 DeepSeek、不写入推荐历史：

```powershell
.venv\Scripts\python.exe daily_paper_agent.py --dry-run
```

如果能显示候选论文，再执行完整流程：双击 `run_now.bat`，或者运行：

```powershell
.venv\Scripts\python.exe daily_paper_agent.py --no-open
```

正常情况下会在 `reports` 文件夹中生成阅读卡片。若没有配置 DeepSeek API Key，或接口暂时失败，系统会退回本地排序并在报告中注明；只有成功的 DeepSeek 分析才会标记为 `deepseek_ok`。

## 3. 设置每天固定时间运行

1. 按 `Win + R`，输入 `taskschd.msc`。
2. 选择“创建基本任务”，例如命名为 `Daily Research Paper Agent`。
3. 触发器选择“每天”，设置期望的本地时间，例如 08:30。
4. 操作选择“启动程序”。程序/脚本填写 `cmd.exe`。
5. “添加参数”填写（需要根据你的实际目录修改）：

   ```text
   /c "C:\Users\你的用户名\Documents\daily_paper_agent\run_daily.bat"
   ```

6. “起始于”填写项目目录：

   ```text
   C:\Users\你的用户名\Documents\daily_paper_agent
   ```

7. 建议选择“仅当用户登录时运行”，以便报告可以打开到浏览器。电脑关机时任务无法执行；任务计划程序可配置“错过计划启动时间后尽快运行”。

`run_daily.bat` 会把调度输出追加到 `scheduler.log`，而应用日志会写到 `agent.log`。计划任务下即使浏览器没有打开，HTML 和 Markdown 文件仍会写入本机。

## 4. 每日阅读和反馈闭环

- 阅读 `reports/latest.html` 或 `reports/latest.md`。
- 双击 `feedback.bat`，对当天推荐打 1–5 分，并填写关键词标签，例如 `GNN, diffusion, G4, enzyme design`。
- 评分 4–5 的标签会提升之后相关候选的排序；评分 1–2 的标签会降低匹配内容的排序。标签由你填写，所以比自动猜测兴趣更可控。
- 反馈写入 `feedback.jsonl`；历史推荐写入 `history.json`，都会保存在本机。

## 5. 自定义领域

编辑 `config.json`：

- `research_focus`：给 DeepSeek 的研究背景。
- `search_queries`：PubMed / Crossref 元数据索引 检索词。
- `target_journals`：PubMed 期刊限定检索清单。对每个 `search_queries` 主题词，程序会额外为清单中的每本期刊执行一次带 `[ta]`（Journal Title）字段限定的 PubMed 检索；原有不限期刊的主题查询仍会完整保留。设为 `[]` 即关闭期刊限定检索。期刊可使用全称、NLM 缩写或 ISSN；建议优先填写全称。
- `arxiv_queries`：出版商站点 检索词；建议使用可检索的短语，不要写整句问题。
- `keywords`：本地排序用的正向关键词。
- `negative_keywords`：命中后扣分的词，适合过滤勘误/撤稿等情况；务必谨慎，避免误伤正常论文。
- `lookback_days` / `maximum_lookback_days`：初始和最大回溯窗口。
- `minimum_journal_impact_factor`：严格筛选阈值，默认 12.0，采用大于而不是大于等于。
- `journal_impact_factor_year`：影响因子数据年份，当前设为 2025（对应 2026 年 JCR 发布的 2025 数据）。
- `journal_metrics_file`：期刊影响因子映射文件，默认 `journal_metrics.json`。只有有明确、同年份 JIF 记录的期刊才会通过；未知指标不会被当成达标。
- `max_candidates_to_llm`：送给 DeepSeek 比较的候选数量；越大通常上下文与费用越高。
- `deepseek_model`：需要与你的 DeepSeek 账户当前可用模型一致。
- `full_text.enabled`：是否尝试开放 PDF 分析。
- `full_text.allowed_pdf_hosts`：开放 PDF 下载白名单。只添加你信任且允许访问的学术仓储/出版社域名；程序只允许 HTTPS，并再次检查重定向域名。

如果研究方向改变，建议同时修改 `search_queries`、`arxiv_queries` 和 `keywords`，不要只改标题提示词。

### 目标期刊清单示例

`config.json` 中目前预置了：

```json
"target_journals": [
  "Nucleic Acids Research"
]
```

以后增加期刊只需编辑该列表，例如加入 `RNA` 或 `Nature Structural & Molecular Biology`，不需要修改 Python 源代码。期刊限定检索会与常规主题检索并行执行，不会把常规检索限制在目标期刊内。相同文章可能被两类查询同时命中，程序会在后续阶段去重。

JIF 匹配使用 `journal_metrics.json` 中的期刊名称和 `aliases` 别名。新增目标期刊如果尚无指标记录，程序会在 `agent.log`、`--dry-run` 输出及报告中明确列出，并按严格规则排除；这时需要在 `journal_metrics.json` 添加经官方来源核验、与 `journal_impact_factor_year` 同年份的 **2-year JIF**，并在 `aliases` 中补充 PubMed 可能返回的 NLM 缩写或 ISSN。程序不会凭空推测 JIF，也不会用 CiteScore、SJR 或 5-year JIF 替代。当前 `Nucleic Acids Research` 的 2025 2-year JIF 记录为 15.0，来源为 Oxford Academic 官方期刊指标页。

## 6. 成本与数据边界

- 通常每次完整运行最多调用 DeepSeek 两次：一次在候选中选文，一次分析选定论文；PDF 不可用时第二次只基于摘要。无 Key 或 API 失败时会降级。
- PubMed、Nature、ACS Publications 与 Wiley Online Library 的覆盖范围不同；本项目不保证覆盖所有会议、期刊、预印本平台或当天最新研究。
- 模型写出的新颖性分、相关性分只是辅助判断，不是同行评议结论。
- PDF 只从白名单中的直接开放链接尝试获取，设置了下载大小/页数/字符限制；PDF 内容只在内存中提取，不保存原文件。请自行遵守来源网站的使用条款和版权规定。
- 报告只会给出可复查的原文链接与有限摘要/证据片段，不会将整篇付费论文全文写入报告。
- 提取的证据片段存在只证明文本定位成功，不能证明整条主张一定成立；重要数值、实验条件、统计显著性与结论必须回到论文原文及补充材料核查。

## 7. 常见排错

**没有论文：** 先运行 `--dry-run`。看看 `agent.log` 中 PubMed / Crossref 元数据索引/出版商站点 各查询是否成功；调整检索词或增大时间窗。若报告说“没有未推荐论文”，系统不会默默把旧论文伪装成新推荐。

**DeepSeek 调用失败：** 检查 `DEEPSEEK_API_KEY` 是否在新终端里生效，确认账户 API 可用性、余额/额度、`deepseek_model` 是否当前可调用；在 `agent.log` 找失败原因。系统会产生回退报告，而不是把回退结果标成模型分析成功。

**没有全文分析：** 看报告中的“全文提取状态”。常见情况是该论文无直接 PDF 链接、域名不在白名单、下载失败、PDF 无法解析或文本为空。此时报告会按摘要分析并标注依据，不会虚称读过全文。可以在确认合法且可信后，把相应域名加入 `allowed_pdf_hosts`。

**任务计划程序执行了但看不到报告：** 报告默认在 `reports` 内；检查 `scheduler.log`、`agent.log`，并确认计划任务“起始于”目录正确。无用户登录时浏览器可能无法显示，但文件仍会生成。

**离线测试：** `python daily_paper_agent.py --self-test`

**只检索/排序：** `python daily_paper_agent.py --dry-run`

**不自动打开浏览器：** `python daily_paper_agent.py --no-open`

## v3.0 检索范围与报告结构

检索限定在 PubMed、Nature、ACS Publications、Wiley Online Library。PubMed 通过 NCBI E-utilities 搜索并取回元数据；Nature、ACS 和 Wiley 通过 Crossref 元数据搜索候选，再严格按 DOI/落地 URL 的出版商域名筛选。Crossref 在这里是元数据索引，不作为额外的目标出版商来源。由于出版商 API、索引延迟、访问权限及元数据完整性不同，这不保证覆盖对应网站的全部文章。

报告新增固定六节：0 摘要；1 研究背景；2 科学问题；3 研究策略、实验和结果；4 作者讨论；5 结尾总结与展望。若只有摘要/元数据可用，系统必须明确标记摘要级分析，不推断作者在全文讨论或结论中的内容。建议设置 `NCBI_API_KEY`（可选）和 `contact_email`，并遵守 NCBI E-utilities 使用规则。

## v3.1 日期与影响因子筛选修复

- **日期问题根因：** PubMed XML 中部分记录只提供 `Year`，旧代码把它强行转换成 `YYYY-01-01`，因此报告看起来都在 1 月 1 日发表。现在优先使用 ArticleDate（电子/文章日期），其次使用期刊期次日期；只给年份时显示 `YYYY`，只给年月时显示 `YYYY-MM`，不伪造具体日。跨来源去重时优先保留日期精度更高的记录。
- **时间窗：** PubMed 检索同时考虑出版日期范围和最近建档日期，帮助发现刚进入 PubMed、出版日期元数据不完整的记录。建档日期只用于召回，不会被显示成出版日期。
- **JIF 门槛：** 默认要求期刊 2-year Journal Impact Factor 严格大于 12，指标年份固定为 2025。未知 JIF、低于或等于 12、或指标年份不匹配的期刊都会被排除；报告会显示每类排除数量。期刊影响因子是期刊级指标，不代表单篇论文质量。
- **指标文件：** `journal_metrics.json` 已放入少量 Nature Portfolio、ACS 和 Wiley 期刊的起始记录，附官方指标页面链接。该表不是所有期刊的完整目录；未录入的 ACS/Wiley/PubMed 期刊会被严格排除。请依据当前 JCR/出版商官方页面核对并补齐目标期刊，不要用 CiteScore、SJR 或 5-year JIF 冒充 2-year JIF。
- **排错：** 先运行 `python daily_paper_agent.py --dry-run`。如果没有候选，查看输出的 `excluded_unknown_jif` 与 `excluded_below_threshold`；前者过高通常意味着 `journal_metrics.json` 缺少期刊条目，而不是日期检索失败。

## v3.2 目标期刊检索

- 新增 `target_journals` 配置项；PubMed 对每个主题词同时执行不限期刊查询和逐本目标期刊的 `[ta]` 限定查询。
- 目标期刊限定只作用于 PubMed，不会改变 Nature、ACS Publications、Wiley 的 Crossref 元数据发现流程。
- JIF 匹配支持 `journal_metrics.json` 中的 `aliases`；未录入指标的目标期刊会明确告警并继续遵守严格排除规则。
- PubMed 状态拆分统计常规主题查询与目标期刊限定查询的成功数、原始记录数。

## 8. 设计参考

- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [PubMed 帮助：出版日期和日期精度](https://pubmed.ncbi.nlm.nih.gov/help/)
- [NCBI E-utilities 官方指南](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html)
- [Clarivate Web of Science Journals API（需许可）](https://developer.clarivate.com/apis/wos-journal)
- [Nature Portfolio 官方期刊指标](https://www.nature.com/nature-portfolio/about-journals/journal-metrics)
- [ACS Publications 官方期刊指标](https://pubs.acs.org/journal-metrics)
- [Wiley Journal Metrics 说明](https://authorservices-ppd.wiley.com/author-resources/Journal-Authors/find-a-journal/journal-metrics.html)

### MCP 是否会提升检索效果？
MCP 是让智能体以统一协议调用外部检索工具的接口，不是新的文献数据库。若接入出版社官方 MCP（例如 Wiley Nexus 在你的账号和权限下可用），可能获得更好的结构化结果、相关段落和检索工具调用体验；但不会自动提高来源覆盖率，也不能绕过订阅/授权。PubMed 官方 E-utilities 通常更直接可控。Nature 和 ACS 是否有适合你账号的官方 MCP/API，需按当前供应商文档确认；不要把未经授权的网页抓取包装成 MCP。
