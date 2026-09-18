#!/usr/bin/env python3
"""网盘拉新统一入口。

用法：
    python main.py baidu        # 只跑百度网盘
    python main.py quark        # 只跑夸克网盘
    python main.py all          # 两个都跑，合并结果统一推送
    python main.py web          # 启动 Web 管理界面 (默认端口 5003)

也可用 --count N 覆盖数量。
"""
import argparse
import sys

from adapters import get_adapter, ADAPTERS
from core.logger import log_print


def run_platform(platform, count, kw=None, include=None, exclude=None,
                 force=False, preview_only=False):
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
    suffix = " (" + ", ".join(extras) + ")" if extras else ""
    log_print(f"开始执行平台: {platform}{suffix}")
    try:
        adapter_kwargs = dict(count=count, kw=kw, include=include,
                              exclude=exclude, force=force,
                              preview_only=preview_only)
        result = get_adapter(platform)(**adapter_kwargs)
    except Exception as e:
        log_print(f"平台 {platform} 执行异常: {e}", "ERROR")
        result = {"code": 500, "message": str(e), "share_results": []}
    log_print(f"平台 {platform} 结束: {result.get('message', '')}")
    return result


def main():
    parser = argparse.ArgumentParser(description="网盘拉新统一入口")
    parser.add_argument("platform", nargs="?", default="all",
                        choices=list(ADAPTERS) + ["all", "web"],
                        help="baidu / quark / all / web")
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
    parser.add_argument("--port", type=int, default=5003, help="Web 界面端口")
    args = parser.parse_args()

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
                     preview_only=args.preview)

    log_print("全部任务完成")


if __name__ == "__main__":
    main()
