import httpx, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch(url):
    with httpx.Client(timeout=20, follow_redirects=True, trust_env=False, headers=UA) as c:
        return c.get(url)

print("===== MIIT zwgk home =====")
r = fetch("https://www.miit.gov.cn/zwgk/")
html = r.text
print("HTTP", r.status_code, "len", len(html))
links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S)
seen=set(); n=0
for href, txt in links:
    t = re.sub(r'<[^>]+>', '', txt).strip()
    if not t or t in seen: continue
    seen.add(t)
    if "miit.gov.cn" in href:
        print(f"- {t[:40]:42s} [{href[:75]}]")
        n+=1
        if n>30: break
