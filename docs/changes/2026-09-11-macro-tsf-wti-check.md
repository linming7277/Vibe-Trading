# 2026-09-11 社融增量 / WTI 宏观序列核查

## 结论
- WTI：修复可用。FRED_API_KEY 已在 `C:\Users\Administrator\.vibe-trading\.env` 配置（32 位、无引号空格，本文不记录值）。
  该 .env 不会被裸进程的 EnvConfig 自动加载——按设置写入路径把 .env 应用到进程后 key_status=PRESENT，
  走现有 `refresh_fred_series` 刷新成功：序列最新观测 2026-09-09（FRED DCOILWTICO，取回 11 行、新增 5 行）。
- 社融增量：失败。akshare `macro_china_shrzgm` 上游为 data.mofcom.gov.cn，两次直连均
  SSLError（UNEXPECTED_EOF_WHILE_READING），库内 social_financing_increment 仍 0 行。
  未编数入库，未改适配器。

## 刷新前后对比
| 序列 | 刷新前最新 | 刷新后最新 | 源 |
|---|---|---|---|
| social_financing_increment | 无数据（0 行） | 无数据（0 行，akshare 上游 SSL EOF ×2） | akshare macro_china_shrzgm（未成功） |
| wti_spot | 2026-09-01 | 2026-09-09 | fred.dcoilwtico（FRED_API_KEY 生效） |

## 环境句
`get_macro_line_summary("2026-09-11")` 原文仍含：**"资料还缺社融增量，判断宜保守。"**（WTI 无缺失表述）

## 环境变量
- FRED_API_KEY：有（运行目录 .env 活跃行，32 位；进程需先应用 .env 才可见——服务器重启后即自动可见）
- TUSHARE_TOKEN：无（社融主路径走 akshare，不依赖）

## 未改什么
未改代码。本次仅运行了现有刷新与只读检查：未改 macro_data 适配器/刷新器、价值线、Focus、
早盘开关、MCP、bitable；未 commit；未发飞书；未打印任何 token/key 值。

## 后续建议（未执行）
1. 社融：data.mofcom.gov.cn 对非浏览器客户端不稳定，可给 akshare 请求加 UA/重试或等站点恢复后重跑同一刷新。
2. 后端下次重启后，服务器进程将应用 .env，FRED 通道在其进程内即为 PRESENT（当前若未重启则其进程内仍 MISSING）。
