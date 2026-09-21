#!/usr/bin/env python3
"""网盘拉新统一入口。

用法：
    python main.py baidu        # 只跑百度网盘
    python main.py quark        # 只跑夸克网盘
    python main.py all          # 两个都跑，合并结果统一推送
    python main.py web          # 启动 Web 管理界面 (默认端口 5003)

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
    parser.add_argument("--text", type=str, default=None,
                        help="直接传入文本，自动识别网盘链接→转存→替换")
    parser.add_argument("--text-file", type=str, default=None,
                        help="从文件读取文本进行链接替换")
    parser.add_argument("--output", type=str, default=None,
                        help="替换后的文本输出到文件（不指定则打印到控制台）")
    parser.add_argument("--save-history", action="store_true", default=False,
                        help="文本替换结果也写入推送历史")
    parser.add_argument("--push-wechat", action="store_true", default=False,
                        help="替换后推送企业微信（摘要 + 完整文本）")
    args = parser.parse_args()

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
                     preview_only=args.preview)

    log_print("全部任务完成")


if __name__ == "__main__":
    main()
