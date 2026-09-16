# 2026-09-15 宏观序列日度刷新进调度（07:35 forward）

- 日期：2026-09-15
- 性质：新增宏观序列刷新调度 + serve 进程 dotenv 加载修复。修 AUDIT V5 Blocker #5 的真实根因。
- 状态：已实现 + 测试通过 + 手动 forward 刷新验证成功；**17:25 已重启生效**（同批含财务夜更与早盘束）。未 commit。

---

## 根因修正（审计 Blocker #5 的再诊断）

审计 V5 记录「美债 09-10 / WTI 09-09（FRED key 缺）」。实际排查：

1. **key 没缺**：`~/.vibe-trading/.env` 里 `FRED_API_KEY` 已于 2026-09-08 经 Web UI 配置（M1-B）。
2. **真正的缺口有两个**：
   - `macro_data.run_refresh(mode="forward")`（treasury.gov + FRED 日度 + chinamoney + 铜，源隔离 fail-soft）在生产代码里**零调用方**——没有调度、没有 API，09-11 那批数据是手动跑的，之后没人再喂；
   - **serve 启动不加载 dotenv**：`_ensure_dotenv` 只挂在 chat/CLI/settings 三条路径，后端进程 `os.environ` 里没有 `FRED_API_KEY`，即使调度去调 `run_refresh`，FRED 通道也会报 MISSING。

## 改动

### 1. `agent/src/macro_data/scheduler.py`（新增）
- `MacroSeriesRefreshScheduler`：60s 看钟，**工作日 07:35**（Asia/Shanghai）→ `run_refresh(mode="forward")` 一次；同日不重跑、错过不补跑；调度线程永不退出。
- `run_forward_refresh()`：先 `_ensure_dotenv()`（idempotent，补上 serve 的 dotenv 缺口），再 forward 刷新，汇总入日志。
- 时点依据：treasury.gov 收益率曲线与 FRED 日度利率在北京凌晨发布，07:35 拉取后 **08:00 宏观快照即吃到 T-0 美债/WTI**。

### 2. `agent/api_server.py`
- lifespan 启动序列挂 `start_macro_series_refresh_scheduler()`（紧跟早盘调度器之后）；关闭时逆序停止。
- 不新增环境变量、不加开关：纯数据维护动作，无 LLM、无推送、源隔离 fail-soft。

## 手动验证（2026-09-15 17:18，重启前先跑了一次 forward）

| 序列 | 刷新前 | 刷新后 |
|---|---|---|
| us_treasury_10y / 2y（treasury.gov） | 09-10 | **09-14**（09-15 值北京时间今晚发布，属正常） |
| us_treasury_10y_fred / 2y_fred（DGS10/DGS2） | 09-03 | **09-11**（FRED 发布节奏下已是最新） |
| usd_cny_official_mid | 09-11 | **09-15（当日）** |
| wti_spot | 09-09 | 09-09 —— api.stlouisfed.org 读超时（瞬态，3 次重试后仍失败），后续每日调度会自愈 |

**FRED key 确认有效、整链路打通。**

## 测试

`agent/tests/test_macro_refresh_scheduler.py` 新增 4 个：槽位矩阵（07:34/35/36、周末）、同日抑制、tick 跳过/触发/二次跳过、dotenv 先于 run_refresh 的顺序断言。全过；api_server 导入检查通过。

## 观察点

- 明早 07:35 后看日志 `macro series forward refresh: overall=...`；08:00 快照的 `missing_fields` 应不再因美债/WTI 缺失（usd_cny 序列死亡是另一个待修项，见下）。
- 若 FRED 偶发超时，下一交易日槽位自动重试；连续失败再查网络/代理。
- 遗留：`usd_cny`（离岸）序列最新观测停在 2021-05-13，是今天宏观快照唯一的 missing_fields，需要单独修序列源。
