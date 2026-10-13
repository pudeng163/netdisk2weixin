#!/usr/bin/env python3
"""导出空关键词（默认搜索）的全量资源清单，供 AI 阅读挑选。

用法：
    python tools/list_resources.py                 # 拉取最新全量结果并打印编号清单
    python tools/list_resources.py --refresh       # 强制刷新（忽略缓存）
    python tools/list_resources.py --json out.json # 同时导出 JSON（含 url，便于后续锁定）

输出格式（每行一条，便于 AI 逐条阅读判断类型）：
    [序号] 标题
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cache


def _clean_note(note: str) -> str:
    """清洗 note：剥离内嵌 URL、电报频道、@mentions、emoji，只留纯标题。

    PanSou 插件源返回的 note 往往包含来源链接（如 "电影合集 - https://t.me/xxx | ..."）
    或 @guomanpindao 之类的频道标识，以及 emoji 尾巴。这些全部去掉。
    """
    s = note or ""
    s = re.sub(r'https?://[^\s]+', ' ', s)
    s = re.sub(r'@[\w\u4e00-\u9fff_-]+', ' ', s)
    s = re.sub(r'[\U00010000-\U0010ffff]', '', s)
    # 去掉 emoji 营销尾巴
    _NOISE = [
        r"📜介绍：.*$", r"💾夸克网盘.*$", r"💾百度网盘.*$", r"💽UC网盘.*$",
        r"💿迅雷网盘.*$", r"📁\s*大小：.*$", r"🏷\s*标签：.*$", r"⬇️.*$",
        r"🔍网盘专搜.*$", r"🧰.*$", r"🛍.*$", r"链接：.*$",
    ]
    for pat in _NOISE:
        s = re.sub(pat, "", s)
    s = re.sub(r"[\s\u3000]+", " ", s).strip()
    return s


def _deduplicate(items):
    """url + 清洗后 note 双重去重，同一个标题只留第一条。"""
    seen_urls = set()
    seen_notes = set()
    unique = []
    for it in items:
        url = it.get("url", "")
        note = it.get("note", "")
        if url and url in seen_urls:
            continue
        if note and note in seen_notes:
            continue
        if url:
            seen_urls.add(url)
        if note:
            seen_notes.add(note)
        unique.append(it)
    return unique


def main():
    ap = argparse.ArgumentParser(description="导出空关键词全量资源清单")
    ap.add_argument("--refresh", action="store_true", help="强制刷新，忽略本地缓存")
    ap.add_argument("--json", type=str, default=None, help="同时导出含 url 的 JSON 到指定路径")
    ap.add_argument("--platform", type=str, default="quark", help="平台：quark / baidu")
    ap.add_argument("--from-pool", action="store_true",
                    help="从本地资源池读取（默认强制走 PanSou API，用于刷新资源池）")
    args = ap.parse_args()

    if args.from_pool:
        items = cache.load_local_pool(args.platform) or []
    else:
        import os
        os.environ["LOCAL_POOL_ENABLED"] = "false"
        items = cache.fetch_api_data(args.platform, "", force_refresh=args.refresh)

    if not items:
        print("（无结果。若走 API 请确认 PanSou 已启动：curl http://localhost:8888/api/health）")
        return 1

    # 统一清洗 note + 双重去重
    cleaned = []
    for it in items:
        raw_note = it.get("note", "") or ""
        url = (it.get("url", "") or "").strip()
        if not url:
            continue
        clean_note = _clean_note(raw_note) or raw_note[:60]
        cleaned.append({"note": clean_note, "url": url})
    cleaned = _deduplicate(cleaned)

    print(f"# 结果：{len(cleaned)} 条（platform={args.platform}，来源={'本地池' if args.from_pool else 'API'}）")
    print("#" * 60)
    for i, it in enumerate(cleaned, 1):
        print(f"[{i}] {it['note']}")

    if args.json:
        payload = [{"index": i, "note": it["note"], "url": it["url"]}
                   for i, it in enumerate(cleaned, 1)]
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n# 已导出 JSON: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
