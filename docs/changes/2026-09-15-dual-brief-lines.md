# 2026-09-15 盘前简报双线决策：确定性线 + 定期 LLM 研究线并存

- 日期：2026-09-15
- 性质：产品决策落地 + 模板目录换血。不改执行器、不改调度开关、不改任何生产链路。
- 状态：已实现 + 测试通过；playbook 目录为运行时读取，**已实时生效（无需重启）**。未 commit。

---

## 决策

老板确认要**两条线并存**：

1. **确定性简报线**（生产）：早盘 08:00 卡（零 LLM 规则）+ 收盘 16:45 日报链（零 LLM）。按既有路线继续迭代。
2. **定期 LLM 研究线**（后续）：`scheduled_research` 框架整体保留（数据模型 + 崩溃安全存储 + executor + REST/CLI/斜杠三入口），作为将来"无人值守、定时自动跑 LLM 研究"的基础设施。

依据：LLM 研究能力本身已由飞书主管会话覆盖（问了才研究）；scheduled_research 的独特价值是**定时**（没人问也自己跑）。老板确认这个能力要留。

## 模板目录换血（本变更的实体动作）

原 5 份 playbook（premarket-brief / a-share-money-flow / earnings-season-tracker / institutional-holdings-diff / portfolio-checkup）是通用工具箱时代的遗产：变量全是占位符、从未建过任务、2 份纯美股场景、1 份依赖本系统没有的数据源、1 份与收盘日报重叠。**全部删除**（git 历史可找回）。

新增 1 份贴合本系统的种子模板：

- `playbooks/weekly-company-review.md`（Weekly Company Review）
- 场景：给定一组公司，每周六 09:00（Asia/Shanghai）自动出周复核——价格相对价值区间位置（引区间原值与 as-of）、周内公告与新闻（逐条带时间戳）、基本面/估值快照的数据新鲜度标注。
- 变量：`companies`（默认 "(no company list configured)"，空清单时只回一行说明，不编造标的）。
- 严格继承原目录的质量红线（测试钉死）：数据能力用自然语言描述、正文零工具名、运行时解析日期、"数据缺失要明说"节、Output/Boundaries 节、禁投资建议与交易动作。

## 未动

- `VIBE_TRADING_ENABLE_SCHEDULER` 保持默认关——定期线仍是"存好任务、到点不执行"的休眠状态，启用时机由老板定。
- 前端 `/ai/scheduled` 页、`POST /scheduled-runs` 裸提示词建任务入口、`VIBE_TRADING_PLAYBOOK_DIR` 外部目录覆盖机制：全部保留。
- 确定性简报线的任何代码：零改动。

## 测试

- `test_research_playbooks.py`：目录钉死改为 `{"weekly-company-review"}`；"覆盖不止美股市场"改为"覆盖主场 cn"；slug/变量引用同步。
- `test_playbooks_surface.py`：`SAMPLE` 换新模板；REST/CLI/斜杠三入口的变量代换样例从 `home_market`/`watchlist` 改为 `companies`。
- 结果：playbook + surface + routes + store + executor 共 **193 passed**；ruff 干净。

## 后续（启用定期线时）

设 `VIBE_TRADING_ENABLE_SCHEDULER=on` 后 executor 才会真正派发任务；建任务可用 `POST /scheduled-runs/playbooks/weekly-company-review`（带 `variables.companies`）或 CLI `/playbook`。首次真实运行时建议先 `--dry-run` 看渲染出的提示词。
