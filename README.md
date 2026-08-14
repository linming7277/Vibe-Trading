# 恒值投资

恒值投资是面向个人的中文智能投资研究工作台，聚焦长期价值研究、公司基本面分析、估值、策略回测与投资组合管理。项目采用 FastAPI + React/Vite，本地优先运行。

## 核心功能

- 今日决策与市场总览
- 宏观环境、行业排名与龙头筛选
- 投资委员会与交易计划
- 投资组合、策略回测和研究报告
- 智能体、定时研究、Alpha 因子与相关性分析
- 通达信全 A 股行情、板块与基本面缓存
- Yahoo 港美股公司资料与估值快照
- Agent 研究结果结构化回写，保留来源与数据日期

## 当前支持边界

- 界面语言：仅简体中文。
- 消息通道：个人版仅支持飞书与微信。
- 交易能力：当前版本不提供实盘连接、自动下单、交易授权或常驻交易运行器；产品定位是研究与组合管理。
- A 股数据：本机通达信只读桥；基本面全量更新通过 90% 完整性门槛后才会原子替换旧缓存。
- 港美股数据：公司研究使用 Yahoo 公开资料；请求失败时保留上一份真实数据，不以示例数据冒充最新结果。
- 专业财务：依赖通达信 `vipdoc/cw/gpcw*.dat`。未下载时明确标记不可用。

## 本地运行

Windows 推荐双击仓库根目录的 `Hengzhi-Launcher.cmd`，或使用无界面命令：

```powershell
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action start
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action status
```

- 工作台：`http://127.0.0.1:5899/value`
- 数据中心：`http://127.0.0.1:5899/data`
- 后端 API：`http://127.0.0.1:8899`

首次运行前需准备 Python 3.11–3.13、Node.js 22，并安装依赖：

```powershell
uv sync --extra dev
cd frontend
npm ci
```

不使用启动器时也可直接运行后端：

```powershell
.\.venv\Scripts\vibe-trading.exe serve --port 8899
```

## 数据更新与验证

数据中心可分别更新行情、榜单、指数、板块、基金、公式、历史和基本面。也可以直接调用只读 API：

```powershell
Invoke-RestMethod http://127.0.0.1:8899/tdx/status
Invoke-RestMethod -Method Post -ContentType application/json `
  -Body '{"module":"fundamental"}' http://127.0.0.1:8899/tdx/update
```

启动器提供端到端冒烟检查，会验证服务状态、后端健康、TDX 桥、行情/基本面接口和前端数据页：

```powershell
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action smoke
```

开发验证：

```powershell
uv run pytest agent/tests/test_research_workspace.py agent/tests/test_research_refresh_route.py agent/tests/test_tdx_data_service.py -q
cd frontend
npm run test:run
npm run build
```

> 为兼容既有数据与配置，Python 包名、`vibe-trading` 命令和 `.vibe-trading` 运行目录暂时保留；用户界面与产品名称统一为“恒值投资”。

## 声明

本项目仅用于投资研究与分析，不构成投资建议。授权与归属要求见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
