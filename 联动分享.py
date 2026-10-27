# -*- coding: utf-8 -*-
"""
联动分享：二号盘从「小学资料（2026年最新）」目录选资源分享，
每个分享链接同时打包「防失联各类小学资料考研影视等」目录（fid: 4bc25d8f3d7c4d3d8686e227cb53147d）。

本脚本负责：
  1. 调用 tools/share_local.py 完成「创建分享链接」（企业微信推送仍由 share_local.py 负责）。
  2. 把每条资源的【资源名称 + 分享链接】显式推送到飞书（群机器人 + 多维表格），
     做到「企业微信 + 飞书」双通道送达。

用法:
    python 联动分享.py                # 随机推 6 个（默认）
    python 联动分享.py 10             # 随机推 10 个
    python 联动分享.py --seed 42      # 固定随机种子，结果可复现
    python 联动分享.py --names "名称1" "名称2"   # 指定名称推送
    python 联动分享.py --no-feishu    # 不推送飞书（飞书也由 share_local.py 兜底推送）

    # 可选覆盖（不改默认预设；默认仍是二号盘 + 小学资料目录 + 防失联目录打包）
    python 联动分享.py --account 1 --folder /自动转存        # 用账号1、自动转存目录
    python 联动分享.py --account 1 --folder /自动转存 --no-with-fid  # 不打包额外 fid
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SHARE_LOCAL = os.path.join(ROOT, "tools", "share_local.py")

# 让本脚本能 import 项目内的 core 模块
sys.path.insert(0, ROOT)

from core.post_share import send_feishu_bot, write_feishu_bitable

# 预设参数：二号盘 + 小学资料目录 + 联动打包的防失联目录 fid
ACCOUNT = "2"
FOLDER = "/小学资料（2026年最新）"
WITH_FID = "4bc25d8f3d7c4d3d8686e227cb53147d"


def push_to_feishu(config, shares):
    """把分享结果（资源名称 + 分享链接）统一推送到飞书：群机器人 + 多维表格。

    走 core.post_share 统一收口，受 FEISHU_ENABLED 单一开关控制，tag 统一为「夸克」。
    企微已由 share_local.py 推送，这里只补飞书，避免重复。
    """
    fs = config.get("feishu", {}) or {}
    if not fs.get("enabled"):
        print("飞书未启用（FEISHU_ENABLED != true），跳过飞书推送")
        return

    send_feishu_bot(config, shares, kw="网盘自有资源")
    write_feishu_bitable(config, shares, platform="quark")


def _extract_json(text):
    """从 share_local.py 的 stdout 中稳健提取末尾的 JSON 结果。

    share_local.py 会先打印一堆日志（log_print → stdout），最后才
    print(json.dumps(...)) 一条 JSON。直接用 json.loads 整段解析会失败，
    因此从末尾往前找第一个以 '{' 开头的行，把它到结尾的片段当作 JSON 解析。
    """
    if not text:
        return None
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith("{"):
            candidate = "\n".join(lines[i:])
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


def main():
    ap = argparse.ArgumentParser(
        description="网盘联动分享：创建分享链接 + 飞书推送（默认二号盘 + 小学资料目录）")
    ap.add_argument("count", nargs="?", type=int, default=6, help="随机推送数量（默认 6）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    ap.add_argument("--names", nargs="+", help="指定资源名称推送（优先于随机）")
    ap.add_argument("--no-feishu", action="store_true",
                    help="不在此处推送飞书（改由 share_local.py 兜底推送飞书）")
    # 可选覆盖：默认用脚本顶部的预设常量（二号盘 + 小学资料目录 + 防失联 fid），
    # 传入后临时覆盖，不影响 GitHub Actions 默认行为。
    ap.add_argument("--account", default=None,
                    help="夸克账号编号（默认用脚本预设 ACCOUNT）")
    ap.add_argument("--folder", default=None,
                    help="资源所在目录（默认用脚本预设 FOLDER）")
    ap.add_argument("--with-fid", default=None,
                    help="额外打包进每个分享链接的 fid（默认用脚本预设 WITH_FID）")
    ap.add_argument("--no-with-fid", action="store_true",
                    help="不打包额外 fid（例如切换账号1时）")
    args = ap.parse_args()

    # 默认由本脚本负责飞书推送，因此让 share_local.py 跳过飞书（避免重复推送）
    no_feishu_to_share = not args.no_feishu

    # 解析覆盖；未传则用预设常量
    account = args.account or ACCOUNT
    folder = args.folder or FOLDER
    with_fid = "" if args.no_with_fid else (args.with_fid if args.with_fid is not None else WITH_FID)

    cmd = [sys.executable, SHARE_LOCAL,
           "--account", account, "--folder", folder]
    if with_fid:
        cmd += ["--with-fid", with_fid]
    cmd += ["--no-yinliu"]
    if no_feishu_to_share:
        cmd += ["--no-feishu"]
    if args.names:
        cmd += ["--names"] + args.names
    else:
        cmd += ["--random", str(args.count)]
        if args.seed is not None:
            cmd += ["--seed", str(args.seed)]

    # 捕获输出，便于本脚本解析分享结果并自行推送飞书
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        sys.exit(result.returncode)

    # 解析 share_local.py 输出的 JSON，取出分享结果推送到飞书
    if not args.no_feishu:
        payload = _extract_json(result.stdout)
        shares = (payload or {}).get("shares") or []
        if not shares:
            print("无法解析 share_local.py 输出，跳过飞书推送")
        else:
            from core.config import load_config
            push_to_feishu(load_config(), shares)

    sys.exit(0)


if __name__ == "__main__":
    main()
