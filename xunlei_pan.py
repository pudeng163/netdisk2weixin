#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迅雷网盘 CLI（薄包装层，核心实现见 adapters/xunlei.py）。

用法：
    python xunlei_pan.py info
    python xunlei_pan.py ls /
    python xunlei_pan.py save "https://pan.xunlei.com/s/VNxxxx?pwd=abcd" --dir /电影
    python xunlei_pan.py mkshare /我的转存/base.apk --expire forever

获取 refresh_token：
    浏览器登录 https://pan.xunlei.com → F12 Network → 筛选 auth/token → 复制 refresh_token
    然后写入 .env 的 XUNLEI_REFRESH_TOKEN，或 data/xunlei_token.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让脚本可以从任意目录运行：把项目根加到 sys.path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from adapters.xunlei import (  # noqa: E402
    XunleiError,
    XunleiClient,
    TokenStore,
    _get_default_token_file,
    fmt_size,
    fmt_time,
)


# ==================== 旧版 webhook 工具函数（CLI 用） ====================

def _load_webhooks():
    """优先读 .env 的 FEISHU_WEBHOOK / WECOM_WEBHOOK，兼容旧版 webhook_config.json。"""
    webhook_file = _HERE / "webhook_config.json"
    saved: Dict[str, Any] = {}
    if webhook_file.exists():
        try:
            saved = json.loads(webhook_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "feishu": os.getenv("FEISHU_WEBHOOK") or str(saved.get("feishu") or ""),
        "wecom": os.getenv("WECOM_WEBHOOK") or str(saved.get("wecom") or ""),
    }


def _notify(webhooks: Dict[str, str], title: str, lines: List[str]) -> None:
    """推送到飞书 / 企微（CLI 专用，用 requests 直接打 webhook）。"""
    import requests as _req

    def _post(url: str, payload: Dict[str, Any], name: str) -> bool:
        try:
            resp = _req.post(url, json=payload, timeout=10)
            data = resp.json() if resp.content else {}
        except Exception as exc:
            print(f"[notify] {name} 推送失败: {exc}", file=sys.stderr)
            return False
        code = data.get("code", data.get("errcode", 0))
        if code not in (0, None):
            print(f"[notify] {name} 错误: {data}", file=sys.stderr)
            return False
        print(f"[notify] 已推送到{name}")
        return True

    if webhooks.get("feishu"):
        text = "\n".join(([title] if title else []) + lines)
        _post(webhooks["feishu"], {"msg_type": "text", "content": {"text": text}}, "飞书")
    if webhooks.get("wecom"):
        body = ([f"**{title}**"] if title else []) + lines
        _post(webhooks["wecom"], {"msgtype": "markdown", "markdown": {"content": "\n".join(body)}}, "企微")


def _print_items(items: List[Dict[str, Any]]) -> None:
    if not items:
        print("（空）")
        return
    print(f"{'类型':<6}{'大小':>12}  {'修改时间':<17}名称")
    print("-" * 78)
    for it in items:
        kind = "目录" if it.get("dir") else "文件"
        print(f"{kind:<6}{fmt_size(it.get('size')):>12}  {fmt_time(it.get('updated_at')):<17}{it.get('file_name', '')}")


# ==================== CLI 入口 ====================

def build_client(args: argparse.Namespace) -> XunleiClient:
    token_file = Path(getattr(args, "token_file", None)) if getattr(args, "token_file", None) else None
    store = TokenStore(token_file)
    token = args.token or os.getenv("XUNLEI_REFRESH_TOKEN") or store.load()
    if not token:
        raise XunleiError(
            "没有可用的 refresh_token。\n"
            "  方式一：--token <refresh_token>\n"
            "  方式二：设置环境变量 XUNLEI_REFRESH_TOKEN\n"
            "  方式三：写入 data/xunlei_token.json\n"
            "获取方式：浏览器登录 pan.xunlei.com → F12 Network 筛 auth/token → 复制 refresh_token。"
        )
    return XunleiClient(token, token_store=store)


def cmd_info(client: XunleiClient, _args: argparse.Namespace) -> None:
    info = client.info()
    print("登录成功")
    print(f"  昵称    : {info['nickname']}")
    print(f"  账号    : {info['username'] or '-'}")
    print(f"  用户 ID : {info['user_id'] or '-'}")
    print(f"  会员    : {info['member_type'] or '普通用户'}")
    if info["total_space"]:
        print(f"  空间    : {fmt_size(info['used_space'])} / {fmt_size(info['total_space'])}")
    else:
        print(f"  已用空间: {fmt_size(info['used_space'])}")


def cmd_ls(client: XunleiClient, args: argparse.Namespace) -> None:
    fid = client.resolve_fid(args.path)
    _print_items(client.ls(fid))


def cmd_mkdir(client: XunleiClient, args: argparse.Namespace) -> None:
    client.mkdir(args.path)


def cmd_rename(client: XunleiClient, args: argparse.Namespace) -> None:
    fid = client.resolve_fid(args.path)
    client._check(
        client._request("PATCH", f"https://api-pan.xunlei.com/drive/v1/files/{fid}",
                        body={"name": args.new_name})
    )
    print(f"[rename] {args.path} -> {args.new_name}")


def cmd_rm(client: XunleiClient, args: argparse.Namespace) -> None:
    fids = [client.resolve_fid(p) for p in args.paths]
    if not fids:
        raise XunleiError("不允许删除根目录")
    client._check(
        client._request(
            "POST",
            "https://api-pan.xunlei.com/drive/v1/files:batchDelete",
            body={"ids": fids, "space": ""},
        )
    )
    print(f"[rm] 已删除 {len(fids)} 项")


def cmd_share(client: XunleiClient, args: argparse.Namespace) -> None:
    share_id, passcode, sub_fid = XunleiClient.parse_share_url(args.url)
    passcode = args.pwd or passcode
    stoken, share_info = client.get_stoken(share_id, passcode)
    print(f"[share] {share_info.get('share_name') or share_info.get('name') or share_id}")
    items = client.list_share(share_id, stoken, sub_fid, recursive=args.recursive)
    _print_items(items)


def cmd_save(client: XunleiClient, args: argparse.Namespace) -> None:
    summary = client.transfer(
        url=args.url, target_dir=args.dir, passcode=args.pwd or "",
        only=args.only, list_only=args.list,
    )
    if summary:
        _notify(
            _load_webhooks(),
            "迅雷网盘 · 转存完成",
            [
                f"分享：{summary['share_name']}",
                f"目标：{summary['target_dir']}",
                f"数量：{summary['count']} 项",
                f"结果：{summary['message']}",
            ],
        )


def cmd_mkshare(client: XunleiClient, args: argparse.Namespace) -> None:
    expire_days = "-1" if args.expire == "forever" else str(args.expire)
    result = client.create_share(args.paths, expire_days=expire_days)
    print("分享创建成功")
    print(f"  链接    : {result['share_url']}")
    print(f"  提取码  : {result['pass_code'] or '无'}")
    print(f"  有效期  : {'永久' if args.expire == 'forever' else args.expire + ' 天'}")
    _notify(
        _load_webhooks(),
        "迅雷网盘 · 分享创建成功",
        [
            f"内容：{'、'.join(args.paths)}",
            f"链接：{result['share_url']}",
            f"提取码：{result['pass_code'] or '无'}",
        ],
    )


def cmd_notify(_client, args: argparse.Namespace) -> None:
    webhooks = _load_webhooks()
    if not (webhooks["feishu"] or webhooks["wecom"]):
        raise XunleiError("没有配置 webhook，请在 .env 设置 FEISHU_WEBHOOK / WECOM_WEBHOOK")
    _notify(webhooks, args.title or "迅雷网盘脚本", [args.content])


def add_webhook_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--feishu", help="飞书群机器人 webhook（覆盖 .env）")
    parser.add_argument("--wecom", help="企业微信群机器人 webhook（覆盖 .env）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xunlei_pan.py",
        description="迅雷网盘 CLI（核心实现已迁入 adapters/xunlei.py）",
    )
    parser.add_argument("--token", help="refresh_token（不传则读 .env 或 data/xunlei_token.json）")
    parser.add_argument("--token-file", help="token 文件路径（默认 data/xunlei_token.json）")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="登录并查看账号信息")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("ls", help="列出目录")
    p.add_argument("path", nargs="?", default="/")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("mkdir", help="新建目录")
    p.add_argument("path")
    p.set_defaults(func=cmd_mkdir)

    p = sub.add_parser("rename", help="重命名")
    p.add_argument("path")
    p.add_argument("new_name")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("rm", help="删除文件/目录")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("share", help="解析分享链接")
    p.add_argument("url")
    p.add_argument("--pwd")
    p.add_argument("--recursive", action="store_true")
    p.set_defaults(func=cmd_share)

    p = sub.add_parser("save", help="转存分享内容")
    p.add_argument("url")
    p.add_argument("--dir", default="/")
    p.add_argument("--pwd")
    p.add_argument("--only", nargs="+")
    p.add_argument("--list", action="store_true")
    add_webhook_args(p)
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("mkshare", help="创建分享链接")
    p.add_argument("paths", nargs="+")
    p.add_argument("--expire", default="forever", choices=["1", "7", "30", "forever"])
    add_webhook_args(p)
    p.set_defaults(func=cmd_mkshare)

    p = sub.add_parser("notify", help="测试 webhook 推送")
    p.add_argument("content", nargs="?", default="这是一条测试消息")
    p.add_argument("--title", default="")
    add_webhook_args(p)
    p.set_defaults(func=cmd_notify, need_client=False)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        need_client = getattr(args, "need_client", True)
        client = build_client(args) if need_client else None
        args.func(client, args)
        return 0
    except XunleiError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
