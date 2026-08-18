# Hengzhi Windows launcher

This is a lightweight Windows service controller, not an Electron desktop app.

- Backend: 由前端代理使用本机 `8899` 端口，不需要同事直接访问。
- Frontend Vite（局域网）: `http://hzstock:5899/value`（电脑换 IP 后地址不变）

前端监听 `0.0.0.0:5899`，同一局域网的同事可使用 `hzstock` 访问。需要将 Windows 计算机名或局域网 DNS 别名设置为 `hzstock`；如果名称无法解析，请在 Windows 网络发现中启用名称解析，并放行 TCP 5899。后端 API 仍由前端代理，不需要直接暴露 8899。
- Runtime state and logs: `.launcher/`

Double-click `Hengzhi-Launcher.cmd` in the repository root. Run
`Install-Hengzhi-Launcher.cmd` once to create a desktop shortcut.

Headless operations are also available:

```powershell
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action status
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action print-url
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action restart
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action smoke
```

`smoke` starts missing project services, then verifies the launcher state,
backend health, TDX bridge, quote and fundamental caches, data APIs, and the
frontend `/data` SPA route. It exits non-zero on the first failed check.

The launcher only stops a port owner when its command line belongs to this
repository. An unrelated process on port 8899 or 5899 is reported as a conflict.

## Domestic data routing

Codex/OpenAI can continue using an overseas proxy.  The Value Research sources
from Chinese government and financial institutions should use a domestic direct
route.  Add the entries in
[`clash-domestic-data-direct-rules.yaml`](clash-domestic-data-direct-rules.yaml)
above your Clash `MATCH`/proxy catch-all rule.  For V2Ray, create equivalent
`domain:gov.cn`, `domain:pbc.gov.cn`, `domain:safe.gov.cn`,
`domain:chinamoney.com.cn`, `domain:ndrc.gov.cn`, `domain:miit.gov.cn`,
`domain:shibor.org`, and `domain:tushare.pro` rules with the `direct` outbound.

The launcher also passes these domains as `NO_PROXY` to the backend, covering
SDKs such as Tushare and AKShare. The application-owned CFETS and policy HTTP
clients independently ignore `HTTP_PROXY`/`HTTPS_PROXY`. TUN mode is lower in
the network stack, so it still needs the proxy-client rules above.
