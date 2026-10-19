# -*- coding: utf-8 -*-
"""
联动分享：二号盘从「小学资料（2026年最新）」目录选资源分享，
每个分享链接同时打包「防失联各类小学资料考研影视等」目录（fid: 4bc25d8f3d7c4d3d8686e227cb53147d）。

用法:
    python 联动分享.py                # 随机推 6 个（默认）
    python 联动分享.py 10             # 随机推 10 个
    python 联动分享.py --seed 42      # 固定随机种子，结果可复现
    python 联动分享.py --names "名称1" "名称2"   # 指定名称推送
"""
import argparse
import os
import subprocess
import sys

# 预设参数：二号盘 + 小学资料目录 + 联动打包的防失联目录 fid
ACCOUNT = "2"
FOLDER = "/小学资料（2026年最新）"
WITH_FID = "4bc25d8f3d7c4d3d8686e227cb53147d"

ROOT = os.path.dirname(os.path.abspath(__file__))
SHARE_LOCAL = os.path.join(ROOT, "tools", "share_local.py")


def main():
    ap = argparse.ArgumentParser(description="二号盘联动分享：小学资料 + 防失联目录打包")
    ap.add_argument("count", nargs="?", type=int, default=6, help="随机推送数量（默认 6）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    ap.add_argument("--names", nargs="+", help="指定资源名称推送（优先于随机）")
    args = ap.parse_args()

    cmd = [sys.executable, SHARE_LOCAL,
           "--account", ACCOUNT, "--folder", FOLDER, "--with-fid", WITH_FID,
           "--no-yinliu"]
    if args.names:
        cmd += ["--names"] + args.names
    else:
        cmd += ["--random", str(args.count)]
        if args.seed is not None:
            cmd += ["--seed", str(args.seed)]

    sys.exit(subprocess.run(cmd, cwd=ROOT).returncode)


if __name__ == "__main__":
    main()
