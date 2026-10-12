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

# 去掉搜索结果标题里常见的营销尾巴/引流噪声，让标题更干净、便于阅读判断
_NOISE_PATTERNS = [
    r"📜介绍：.*$", r"💾夸克网盘.*$", r"💾百度网盘.*$", r"💽UC网盘.*$",
    r"💿迅雷网盘.*$", r"📁\s*大小：.*$", r"🏷\s*标签：.*$", r"⬇️.*$",
    r"🔍网盘专搜.*$", r"🧰.*$", r"🛍.*$", r"链接：.*$",
]


def _clean(note: str) -> str:
    s = (note or "").replace("\n", " ").strip()
    for pat in _NOISE_PATTERNS:
        s = re.sub(pat, "", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    ap = argparse.ArgumentParser(description="导出空关键词全量资源清单")
    ap.add_argument("--refresh", action="store_true", help="强制刷新，忽略本地缓存")
    ap.add_argument("--json", type=str, default=None, help="同时导出含 url 的 JSON 到指定路径")
    ap.add_argument("--platform", type=str, default="quark", help="平台：quark / baidu")
    ap.add_argument("--from-pool", action="store_true",
                    help="从本地资源池读取（默认强制走 PanSou API，用于刷新资源池）")
    args = ap.parse_args()

    # 默认走 API 拉取最新数据（本工具是"生成资源池"的入口，不能被本地池短路）；
    # 若只想查看当前本地池内容，加 --from-pool。
    if args.from_pool:
        items = cache.load_local_pool(args.platform) or []
    else:
        import os
        os.environ["LOCAL_POOL_ENABLED"] = "false"  # 临时关闭本地池，强制走 API
        items = cache.fetch_api_data(args.platform, "", force_refresh=args.refresh)

    if not items:
        print("（无结果。若走 API 请确认 PanSou 已启动：curl http://localhost:8888/api/health）")
        return 1

    print(f"# 结果：{len(items)} 条（platform={args.platform}，来源={'本地池' if args.from_pool else 'API'}）")
    print("#" * 60)
    for i, it in enumerate(items, 1):
        note = _clean(it.get("note") or "")
        print(f"[{i}] {note}")

    if args.json:
        payload = [{"index": i, "note": _clean(it.get("note", "")), "url": it.get("url", "")}
                   for i, it in enumerate(items, 1)]
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n# 已导出 JSON: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
