import httpx, traceback

urls = [
    ("gov-search", "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?t=zhengcelibrary"),
    ("ndrc", "https://zfxxgk.ndrc.gov.cn/web/dirlist.jsp"),
    ("miit", "https://www.miit.gov.cn/zwgk/"),
]

print("=== httpx trust_env=False (exactly what policy_data uses) ===")
for name, url in urls:
    try:
        with httpx.Client(timeout=15, follow_redirects=True, trust_env=False,
                          headers={"User-Agent": "hzstock-value-research/1.0"}) as client:
            r = client.get(url)
            print(f"{name}: HTTP {r.status_code} len={len(r.content)}")
    except Exception as e:
        print(f"{name}: FAIL -> {type(e).__name__}: {e}")
