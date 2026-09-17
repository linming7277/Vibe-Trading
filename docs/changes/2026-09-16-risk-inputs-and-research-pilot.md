# 2026-09-16 风险快照业务输入专项（P2-A）+ 定期研究线首次试点（P2-B）

- 日期：2026-09-16
- 性质：A 为风险快照质量专项（根因修复）；B 为定期研究线启用试点。均为老板拍板的 P2 方向。
- 状态：A 代码完成 + 99 测试全过 + 2 家实测验证；B 调度链路全通 + 首跑投递成功。未 commit。

---

## A. 风险 UNKNOWN 专项（165 家 business_status：149 PARTIAL + 16 MISSING）

### 根因（比 audit-v5 记载更深一层）

1. **READY 数学上不可达**：`risk_research/service.py` 的市占率条件硬编码 `False`，`missing` 永不为空，READY 分支永远走不到——历史上没有任何一家到过 READY。
2. **103 家 PARTIAL 是契约错配**：业务研究提示词的主题枚举不含客户集中度/收入占比，而风险引擎恰恰按「客户集中度/前五名客户/占营业收入」文本判条件——生产侧永远产不出风险引擎要的表述。
3. **16 家 MISSING 是坏缓存永久复用**：08-27 的 SUMMARY_ONLY 件（claims 为空）`analysis_status=COMPLETED`，prep 工作器只看状态就判 READY 复用，每天原样重放。
4. `LOW_VALUE_RISK_DATA_PREPARATION=QUEUED` 是 fire-and-forget 阶段标签（设计如此），prep 实际每天跑完，只是跑完无效。

### 修复（三处，生产侧对齐契约、不放松风险判定）

| 改动 | 文件 | 效果 |
|---|---|---|
| A1 市占率改为**永久披露的非阻塞缺口**（照常出现在 missing 里供披露，不再阻塞 READY） | `risk_research/service.py` | READY 变为可达；000544.SZ 三条件本就齐备，即刻 READY |
| A2 契约对齐：`BUSINESS_TOPICS` 增加 `CUSTOMER_CONCENTRATION`/`PRODUCT_REVENUE_SHARE`；提示词强制——来源含客户集中度资料必须输出「客户集中度（括号白话解释）」或「前五名客户」表述、含产品结构资料必须输出「占营业收入」表述，数字逐字保留；模块版本 v1.1.0→**v1.2.0** | `business_research/service.py` | 未来产出的 claims 天然满足风险引擎判定 |
| A3 版本对齐重跑：prep 只对 `module_version==当前版本` 的 COMPLETED 件复用；版本不匹配的存量件 force 重跑一次，重跑后版本对齐、绝不每天重骰 | `risk_research_preparation/service.py` | 16 家坏缓存与全部旧版本件自愈 |

### 验证

- 测试：`test_risk_research.py`（口径更新：三条件齐备即 READY，missing 仍披露 MARKET_SHARE）、`test_business_research.py` 新增主题接受与提示词断言、`test_risk_research_preparation.py` 假件补 module_version，相关 8 个测试文件 **99 passed**。
- 试点重跑 2 家（真实 LLM）：600987.SH（原 MISSING）新快照已产出合格 claim——「客户集中度（即对少数大客户的依赖程度）：前五名客户销售额 177,285.83 万元…」逐字数字+括号解释；000544.SZ → **READY（系统内第一家）**。两家快照 module_version 均为 v1.2.0（无重复重跑）。
- **全面生效路径**：今晚 16:45 EOD 的 prep 工作器对剩余 ~163 家按新契约逐家 force 重析（后台线程，165 次 LLM 调用、预计 1-3 小时跑完，fail-soft 单家失败不阻断）；明早 16:45 前的快照将逐步由 PARTIAL 转 READY。

## B. 定期研究线首次试点

链路五步全部打通：

1. **serve 进程不加载 .env** → `api_server` lifespan 启动时补 `_ensure_dotenv()`（同时修复 FRED key 等一切 env 门控对该进程的可见性）。
2. `.env` 加 `VIBE_TRADING_ENABLE_SCHEDULER=on`，executor 随 lifespan 启动。
3. 创建试点任务 `pilot-weekly-company-review`：playbook `weekly-company-review`，变量 companies=Focus A 十家（江河集团、浙江医药、嘉化能源、中交设计、中天科技、天能股份、华域汽车、中国铁建、欧派家居、巨星科技），每周六 09:00。
4. **首跑触发成功**：到点自动创建会话 `scheduled-research:pilot-weekly-company-review` 并投递提示词（executor COMPLETED=投递成功，符合设计）。
5. **发现并修复一个更大问题**：会话模型调用 401——`.env` 里的 `OPENROUTER_API_KEY` 是**截断残值（仅 13 字符）**，而会话走的是 env 默认通路。已把默认通路切到智谱 GLM（凭据与 research_lead 一致，实测连通），全部 AI 会话的 401 一并修复。

### 待观察

- 首跑会话（AI 研究 → 会话列表 → `scheduled-research:pilot-weekly-company-review`）的 agent 研究仍在后台运行（10 家公司长任务），完成后回复会入会话；若长期无产出，属派发执行层待排查项，不影响调度与投递。
- 输出质量老板看过之后，可决定：保留周六 09:00 周更 / 调整变量与节奏 / 删除任务（DELETE /scheduled-runs/pilot-weekly-company-review）。

## 未动

风险引擎判定关键词（C 方向的引擎侧放宽）、Focus A=10、确定性日报、其他全部链路。`.env` 的 openrouter 旧 key 已注释保留（残值，恢复需向 openrouter 重新取全量 key）。


---

## 补充（同日）：OpenRouter 全量移除，默认模型统一智谱 GLM

- 老板拍板：去掉 OpenRouter，全部走智谱 GLM。
- `providers/llm_providers.json`：删除 openrouter 目录条目（设置页下拉不再出现；23 个提供方保留，含 zhipu）；代码中剩余的 openrouter 字样均为通用能力注释/示例文本，非配置，保留。
- `~/.vibe-trading/.env`：4 行 OPENROUTER_* 配置（含截断残值 key）删除；现状 `LANGCHAIN_PROVIDER=openai`、`LANGCHAIN_MODEL_NAME=glm-5.3`、`OPENAI_API_KEY/OPENAI_BASE_URL` 指向智谱已验证通路。
- 附带发现（非本次改动引起）：`test_governance.py` 23 个用例在干净基线上即失败（LedgerCorruptionError，账本链校验），已用 git stash 验证与近期全部改动无关，另行排查。
- 验证：重启后 settings 接口 provider=openai / model=glm-5.3、目录无 openrouter；默认通路 ChatLLM 实测连通；前端设置相关 16 测试全过、build 通过。
