import socket, ssl, urllib.request, ssl as _ssl

urls = [
    ("gov-search", "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?t=zhengcelibrary"),
    ("ndrc", "https://zfxxgk.ndrc.gov.cn/web/dirlist.jsp"),
    ("miit", "https://www.miit.gov.cn/zwgk/"),
]

print("=== 1) raw TLS handshake (no proxy, via direct socket) ===")
for name, url in urls:
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        with socket.create_connection((u.hostname, 443), timeout=8) as s:
            with ctx.wrap_socket(s, server_hostname=u.hostname) as ts:
                print(f"{name}: TLS OK cert={ts.getpeercert()['subject']}")
    except Exception as e:
        print(f"{name}: TLS FAIL -> {type(e).__name__}: {e}")

print("=== 2) HTTP GET via urllib with explicit proxy bypass ===")
for name, url in urls:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=12) as r:
            body = r.read(300)
            print(f"{name}: HTTP {r.status} len~{len(r.read(1))} preview={body[:60]!r}")
    except Exception as e:
        print(f"{name}: HTTP FAIL -> {type(e).__name__}: {e}")
