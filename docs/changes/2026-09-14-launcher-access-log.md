# 2026-09-14 启动器新增「实时访问日志」窗口

- 日期：2026-09-14
- 性质：启动器工具增强（launcher/windows 三个文件）。后端服务代码、启动/停止逻辑、日志文件本身均未改动；日志窗口为**只读跟随**（Get-Content -Wait），不影响服务。

---

## 功能

独立控制台窗口实时显示后端 HTTP 访问行（来自 uvicorn access log）：

```
[16:34:43] 127.0.0.1（本机）     GET   /api/value/low-value-leaders 200
[16:34:43] 192.168.1.20          GET   /value/...                    200
[16:35:02] 10.0.0.8              POST  /api/research/...             500
```

- 数据源：`.launcher\logs\backend.log`（`Get-Content -Wait -Tail 200 -Encoding UTF8` 实时跟随）。
- 状态码着色：2xx 绿、3xx 青、4xx 黄、5xx 红。
- 本机 127.0.0.1/::1 标注「（本机）」；Ctrl+C 退出（关窗口亦可，不影响服务）。
- 非访问行（启动横幅、应用日志）自动过滤，只显示 IP/页面/状态码行。

## 三种打开方式

1. 桌面入口：`Hengzhi-Launcher.cmd log`（新增转发分支）；
2. 启动器监视窗口：按 **V**（菜单更新为 `[S]启动 [T]停止 [R]重启 [O]打开工作台 [V]实时访问日志 [L]日志目录 [Q]退出`）；
3. 命令行：`powershell -File launcher\windows\HengzhiLauncher.ps1 -Action log`（立即返回并弹独立窗口）。

## 改动文件

| 文件 | 改动 |
|---|---|
| `launcher/windows/Show-AccessLog.ps1`（新增，UTF-8 BOM） | 实时跟随 + 正则解析 + 着色渲染；`-Once` 参数一次性渲染现有尾部后退出（自检用） |
| `launcher/windows/HengzhiLauncher.ps1` | Action ValidateSet 加 `log`；console 菜单加 `[V]实时访问日志` 按键；`-Action log` 分发分支（弹独立窗口后退出） |
| `Hengzhi-Launcher.cmd` | 加 `log` 参数转发 |

## 验证

- `Show-AccessLog.ps1 -Once`：对现行 backend.log 尾部渲染正确（见上格式样例）；
- `-Action log`：立即返回（exit 0）并弹出独立窗口；
- 两个 ps1 均通过 PowerShell Parser 语法检查；Show-AccessLog.ps1 带 UTF-8 BOM（Windows PowerShell 5.1 中文兼容）。

## 未改什么

服务启动/停止/重启逻辑、backend 代码与日志写法（仍单文件 backend.log）、Focus/价值线/早盘/CIO 全部业务代码、`HZ_MORNING_MACRO` 与 `HZ_CIO_BLOCK_WORKER` 状态。未 commit/push、未发飞书。

## 风险

1. 时间戳为「渲染时刻」而非请求时刻（uvicorn access 行不含时间）——实时跟随下基本即时；`-Once` 模式下历史行都会显示当前时间。
2. backend.log 长期增长会让 `-Tail 200` 启动稍慢（仅首次），后续实时跟随不受影响；日志轮转不在本任务范围。
3. 窗口用 `Get-Content -Wait` 占用一个文件句柄（只读共享），与服务写日志互不干扰。


## 追加（同日）：访问日志合并进服务监视器

按用户要求，实时访问不再只存在于独立窗口——服务监视器（console 模式）每 2 秒重绘时，在「最近访问设备」下方内嵌**「实时访问（最近 8 条）」**区块：`IP  方法 路径 状态码`（状态着色同独立窗口）。

- `HengzhiLauncher.ps1`：新增 `Get-RecentAccessEntries(-Count)`（解析 backend.log 尾部 access 行，本机标注「（本机）」）；console 循环组装 `$lines` 时插入该区块。
- `[V]实时访问日志` 独立滚动窗口**保留**（监视器内嵌区只显示最近 8 条快照；要看连续滚动流按 V 弹独立窗口）。
- 验证：`Get-RecentAccessEntries` 内联实测返回最近 8 条真实访问（IP/方法/路径/状态解析正确）；PS1 Parser 通过、UTF-8 BOM 保持。


## 追加（同日）：监视器自动刷新间隔 2 秒 → 30 分钟

按用户要求，服务监视器画面自动刷新间隔改为 **30 分钟**（`$deadline` AddSeconds(2) → AddMinutes(30)）。按键交互不受影响：[S]/[T]/[R]/[O]/[V]/[L] 按下后画面立即重绘并重新计时；「实时访问」区随每次重绘更新。需要实时刷新访问流时按 V 弹独立滚动窗口。
