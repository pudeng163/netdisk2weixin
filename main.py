#!/usr/bin/env python3
"""网盘拉新统一入口。

用法：
    python main.py baidu        # 只跑百度网盘
    python main.py quark        # 只跑夸克网盘
    python main.py all          # 两个都跑，合并结果统一推送
    python main.py web          # 启动 Web 管理界面 (默认端口 5003)

PanSou 服务管理：
    python main.py pansou start     # 自动选二进制并后台启动
    python main.py pansou status    # 查看运行状态与插件数
    python main.py pansou stop      # 停止
    python main.py diagnose         # 环境诊断（平台→二进制→服务）

文本替换模式：
    python main.py --text "xxx https://pan.quark.cn/s/abc 提取码: 1234 xxx"
    python main.py --text-file input.txt --output output.txt
"""
import argparse
import asyncio
import sys

from adapters import get_adapter, ADAPTERS
from core.logger import log_print


def run_platform(platform, count, kw=None, include=None, exclude=None,
                 force=False, preview_only=False, check_links=None):
    if platform not in ADAPTERS:
        log_print(f"未知平台: {platform}", "ERROR")
        return None
    log_print("=" * 60)
    extras = []
    if kw:
        extras.append(f"关键词: {kw}")
    if include:
        extras.append(f"包含: {include}")
    if exclude:
        extras.append(f"排除: {exclude}")
    if force:
        extras.append("强制刷新")
    if preview_only:
        extras.append("预览模式")
    if check_links is True:
        extras.append("链接预检")
    elif check_links is False:
        extras.append("跳过预检")
    suffix = " (" + ", ".join(extras) + ")" if extras else ""
    log_print(f"开始执行平台: {platform}{suffix}")
    try:
        adapter_kwargs = dict(count=count, kw=kw, include=include,
                              exclude=exclude, force=force,
                              preview_only=preview_only)
        # check_links 只有 quark 适配器支持预检
        if platform == "quark":
            adapter_kwargs["check_links"] = check_links
        result = get_adapter(platform)(**adapter_kwargs)
    except Exception as e:
        log_print(f"平台 {platform} 执行异常: {e}", "ERROR")
        result = {"code": 500, "message": str(e), "share_results": []}
    # 预览模式：把随机抽中的条目打印出来，便于在执行前确认
    if preview_only and isinstance(result, dict) and result.get("items"):
        items = result["items"]
        print("=" * 60)
        print(f"随机预览：共 {len(items)} 条（未转存）")
        print("-" * 60)
        for it in items:
            print(f"[{it.get('index', '')}] {it.get('note', '无标题')}")
            print(f"     {it.get('url', '')}")
        print("=" * 60)
    log_print(f"平台 {platform} 结束: {result.get('message', '')}")
    return result


def run_pansou(action="start", wait=60, foreground=False):
    """PanSou 服务管理：start / stop / status / restart。"""
    from core import pansou_launcher as pl

    if action == "start":
        r = pl.start(foreground=foreground, wait=wait)
        return 0 if r["ok"] else 1
    if action == "stop":
        r = pl.stop()
        return 0 if r["ok"] else 1
    if action == "restart":
        pl.stop()
        r = pl.start(wait=wait)
        return 0 if r["ok"] else 1
    # status
    st = pl.status()
    print("=" * 60)
    print("PanSou 状态")
    print("-" * 60)
    print(f"  运行中    {'是' if st['running'] else '否'}")
    print(f"  地址      {st['url']}")
    if st["running"]:
        print(f"  插件数    {st['plugin_count']}")
        if st["plugin_count"] == 0:
            print("  ⚠ 插件数为 0 —— 搜索将永远返回 0 条！检查 ENABLED_PLUGINS")
    print(f"  PID       {st['pid']}（存活={st['pid_alive']}）")
    print(f"  日志      {st['log']}")
    print("=" * 60)
    return 0 if st["running"] and st["plugin_count"] > 0 else 1


def main():
    parser = argparse.ArgumentParser(description="网盘拉新统一入口")
    parser.add_argument("platform", nargs="?", default="all",
                        choices=list(ADAPTERS) + ["all", "web", "pansou", "diagnose"],
                        help="baidu / quark / all / web / pansou / diagnose")
    # pansou 子动作（仅当 platform=pansou 时有意义）
    parser.add_argument("action", nargs="?", default="start",
                        choices=["start", "stop", "status", "restart"],
                        help="当 platform=pansou 时指定动作，默认 start")
    parser.add_argument("--foreground", action="store_true", default=False,
                        help="PanSou 前台启动（日志直接打到终端，Ctrl+C 退出）")
    parser.add_argument("--wait", type=int, default=60,
                        help="PanSou 启动后等待就绪的秒数，默认 60")
    parser.add_argument("--count", type=int, default=None, help="覆盖转存数量")
    parser.add_argument("--kw", type=str, default=None, help="API 搜索关键词")
    parser.add_argument("--include", type=str, action="append", default=None,
                        help="标题包含关键词（可多次指定，本地过滤）")
    parser.add_argument("--exclude", type=str, action="append", default=None,
                        help="标题排除关键词（可多次指定，本地过滤）")
    parser.add_argument("--force", action="store_true", default=False,
                        help="强制跳过缓存，重新请求在线 API")
    parser.add_argument("--preview", action="store_true", default=False,
                        help="只预览资源列表，不执行转存")
    # 链接有效性预检（默认开启，见 .env 的 PANSOU_CHECK_ENABLED）
    parser.add_argument("--check-links", dest="check_links", action="store_true",
                        default=None, help="转存前强制启用 PanSou 链接有效性预检")
    parser.add_argument("--no-check-links", dest="check_links", action="store_false",
                        default=None, help="转存前跳过链接有效性预检")
    parser.add_argument("--port", type=int, default=5003, help="Web 界面端口")
    parser.add_argument("--text", type=str, default=None,
                        help="直接传入文本，自动识别网盘链接→转存→替换")
    parser.add_argument("--text-file", type=str, default=None,
                        help="从文件读取文本进行链接替换")
    parser.add_argument("--output", type=str, default=None,
                        help="替换后的文本输出到文件（不指定则打印到控制台）")
    parser.add_argument("--save-history", action="store_true", default=True,
                        help="文本替换结果也写入推送历史")
    parser.add_argument("--push-wechat", action="store_true", default=True,
                        help="替换后推送企业微信（摘要 + 完整文本）")
    args = parser.parse_args()

    # PanSou 服务管理与环境诊断（不涉及资源转存）
    if args.platform == "pansou":
        return run_pansou(args.action, wait=args.wait,
                          foreground=args.foreground)
    if args.platform == "diagnose":
        from tools.diagnose import main as diagnose_main
        return diagnose_main()

    if args.text or args.text_file:
        from core.text_replacer import run_from_text
        text = args.text
        if args.text_file:
            with open(args.text_file, "r", encoding="utf-8") as f:
                text = f.read()
        if not text:
            log_print("输入文本为空", "ERROR")
            return
        result = asyncio.run(run_from_text(
            text, save_results=args.save_history, push_wechat=args.push_wechat
        ))
        log_print(f"替换完成: {result.get('message', '')}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result["replaced_text"])
            log_print(f"已写入: {args.output}", "SUCCESS")
        else:
            print("\n" + "=" * 60)
            print("===== 替换后的文本 =====")
            print("=" * 60)
            print(result["replaced_text"])
            print("=" * 60)
            for r in result.get("results", []):
                status_icon = "✅" if r["status"] == "success" else "❌"
                print(f"{status_icon} [{r['platform']}] {r['original_url'][:60]}")
                if r["new_url"]:
                    print(f"    → {r['new_url']}")
                elif r["message"]:
                    print(f"    失败: {r['message']}")
        return

    if args.platform == "web":
        from web.web_app import start_web
        start_web(port=args.port)
        return

    if args.platform == "all":
        platforms = list(ADAPTERS)
    else:
        platforms = [args.platform]

    for p in platforms:
        run_platform(p, args.count, kw=args.kw, include=args.include,
                     exclude=args.exclude, force=args.force,
                     preview_only=args.preview,
                     check_links=args.check_links)

    log_print("全部任务完成")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
