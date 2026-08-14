# Hengzhi Windows launcher

This is a lightweight Windows service controller, not an Electron desktop app.

- Backend: `http://127.0.0.1:8899`
- Frontend Vite: `http://127.0.0.1:5899/today`
- Runtime state and logs: `.launcher/`

Double-click `Hengzhi-Launcher.cmd` in the repository root. Run
`Install-Hengzhi-Launcher.cmd` once to create a desktop shortcut.

Headless operations are also available:

```powershell
powershell -ExecutionPolicy Bypass -File launcher/windows/HengzhiLauncher.ps1 -Action status
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
