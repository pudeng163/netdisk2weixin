#!/usr/bin/env python3
"""补全被中断的推送：为已转存但未分享的资源生成分享、投引流文件、写记录、推送。

背景：2026-10-17 15:39 的推送任务在「转存成功 → 引流清理」阶段被外部超时杀死，
两个资源的转存已落到夸克网盘，但分享/引流投放/DONE 记录/推送都没执行。
本脚本定位这批目录，用项目自身函数把剩余流程补完。

用法：
    python tools/complete_pending_push.py            # 自动定位今日待补全目录并执行
    python tools/complete_pending_push.py --dry-run  # 只定位和打印，不执行
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_config, get_kuake_cli, get_yinliu_files
from adapters.quark import (QuarkPanFileManager, _resolve_fid_to_path,
                            _upload_yinliu_files, SHARE_TOTAL_FILE)
from core.logger import log_print

# 待补全的目录：match=顶层目录名匹配子串，note=资源池原始标题（用于 DONE 记录）
# 合集资源「【】10月15日 AI短剧+真人剧」转存后是两个顶层目录，合成一个分享
PENDING = [
    {"match": "局【台剧】", "note": "【台剧】转角那间心药局.更16"},
    {"match": "10月14日", "note": "【】10月15日 AI短剧+真人剧"},
]


def kuake_list(cli, path):
    """列出网盘目录内容，返回条目 list。"""
    r = subprocess.run([cli, "list", path], capture_output=True, timeout=60,
                       encoding="utf-8", errors="replace")
    t = r.stdout.strip()
    obj = json.loads(t[t.find("{"):]) if t.find("{") >= 0 else {}
    return obj.get("data", {}).get("list", [])


def _done_notes():
    """已推送完成（DONE）记录里的标题集合。"""
    if not os.path.exists(SHARE_TOTAL_FILE):
        return []
    with open(SHARE_TOTAL_FILE, "r", encoding="utf-8") as f:
        return [l.split("|")[0].strip() for l in f if "| DONE" in l]


def find_pending(folder_path):
    """按规则定位待补全目录，返回 [(item, [entries])]。"""
    entries = kuake_list(os.environ["_KUAKE"], folder_path)
    done = _done_notes()
    results = []
    for item in PENDING:
        hits = [e for e in entries
                if e.get("dir") and item["match"] in e.get("file_name", "")]
        already = any(item["note"] in d for d in done if d)
        if already:
            log_print(f"{item['note']} 已有 DONE 记录，跳过", "INFO")
            hits = []
        results.append((item, hits))
    return results


async def complete_one(manager, item, hits, folder_path, folder_fid,
                       kuake_cli, yinliu_files):
    """为一条资源补完：分享 → 投引流文件 → 写 DONE 记录。"""
    note = item["note"]
    names = [h["file_name"] for h in hits]
    fids = [h["fid"] for h in hits]
    log_print(f"[补全] {note} -> {names}", "INFO")

    share = await manager.share_run(fids, folder_id=folder_fid, url_type=1,
                                    expired_type=1, password="",
                                    traverse_depth=0)
    share_url = (share or {}).get("share_url", "")
    if not share_url:
        log_print(f"[补全] 分享失败: {note} - {share}", "ERROR")
        return None
    title = (share or {}).get("title", names[0])
    log_print(f"[补全] 分享成功: {title} - {share_url}", "SUCCESS")

    # 引流文件投放到本轮转存的每一个目录内
    for h in hits:
        _upload_yinliu_files(kuake_cli, yinliu_files,
                             h["file_name"], folder_path)

    with open(SHARE_TOTAL_FILE, "a", encoding="utf-8") as f:
        f.write(f"{note}  |  {share_url}  |  DONE\n")
    log_print(f"[补全] 已写 DONE 记录: {note}", "SUCCESS")

    return {"note": note, "title": title, "share_url": share_url,
            "size": (share or {}).get("size", "")}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = load_config()
    quark = config["quark"]
    cookie = quark.get("cookie", "")
    if not cookie:
        log_print("未配置夸克 Cookie", "ERROR")
        return 1

    kuake_cli = get_kuake_cli(config)
    folder_fid = quark.get("folder_fid", "0") or "0"
    yinliu_files = get_yinliu_files(config)
    os.environ["_KUAKE"] = kuake_cli

    folder_path = _resolve_fid_to_path(kuake_cli, folder_fid)
    log_print(f"转存根目录: {folder_path}", "INFO")
    log_print(f"引流文件: {yinliu_files}", "INFO")

    pending = find_pending(folder_path)
    if args.dry_run:
        for item, hits in pending:
            log_print(f"[dry-run] {item['note']}: "
                      f"{[h['file_name'] for h in hits]}", "INFO")
        return 0

    manager = QuarkPanFileManager(cookie=cookie)
    results = []
    for item, hits in pending:
        if not hits:
            continue
        r = await complete_one(manager, item, hits, folder_path,
                               folder_fid, kuake_cli, yinliu_files)
        if r:
            results.append(r)

    if results:
        webhook_url = config.get("wechat", {}).get("webhook_url", "")
        if webhook_url:
            from core.notifier import send_wechat_notification
            wr = send_wechat_notification(webhook_url, results, "")
            log_print(f"企业微信通知: {wr}", "INFO")
        fs_cfg = config.get("feishu", {})
        if fs_cfg.get("enabled"):
            try:
                from core.feishu_client import batch_write_resources
                items = [{"name": r["title"] or r["note"], "url": r["share_url"],
                          "size": r.get("size", ""), "tag": "夸克",
                          "search": r["title"] or r["note"],
                          "update_time": time.strftime("%Y-%m-%d")}
                         for r in results]
                fr = batch_write_resources(config, items)
                log_print(f"飞书写入: {fr}", "INFO")
            except Exception as e:
                log_print(f"飞书写入异常: {e}", "ERROR")

    log_print(f"补全完成：{len(results)} 个资源", "SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
