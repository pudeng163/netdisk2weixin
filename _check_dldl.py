import httpx, sys, json
sys.stdout.reconfigure(encoding="utf-8")

r = httpx.get("https://so.252035.xyz/api/search",
              params={"kw": "????", "cloud_types": "quark", "page": 1},
              timeout=15)
items = r.json().get("data", {}).get("merged_by_type", {}).get("quark", [])
print(f"API total={len(items)}")

live = []
for i, it in enumerate(items):
    url = it.get("url", "")
    note = it.get("note", "")
    try:
        chk = httpx.get(url, timeout=10, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"})
        body = chk.text[:500]
        dead_keywords = ["??", "??", "?????", "???", "???", "????"]
        status = "LIVE"
        for k in dead_keywords:
            if k in body:
                status = f"DEAD:{k}"
                break
        if status == "LIVE":
            live.append(it)
    except Exception as e:
        status = f"ERR:{type(e).__name__}"
    print(f"[{i+1}] {status}")
    print(f"    {note}")
    print(f"    {url}")

print(f"\n=== ?? {len(live)}/{len(items)} ===")
for it in live:
    print(f"  {it['url']}  {it['note']}")
