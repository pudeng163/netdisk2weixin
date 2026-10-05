#!/usr/bin/env python3
"""MCP (Model Context Protocol) Server for netdisk2weixin.

让 AI Agent（Claude Desktop / Cursor / Trae 等）通过 MCP 协议调用网盘转存功能。
标准 stdio 传输，零额外依赖。

支持的工具：
  1. run_platform       - 从聚合 API 搜索→转存→生成分享→推送企业微信
  2. run_text_replace   - 识别文本中的网盘链接→转存→替换成自己的链接
  3. preview_resources  - 只预览资源列表，不执行转存
  4. get_share_history  - 读取最近推送历史

用法：
  python mcp_server.py
然后在 MCP 客户端配置里 command=python, args=[mcp_server.py 的完整路径]
"""
import asyncio
import json
import sys
import os
import threading
import queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters import get_adapter, ADAPTERS
from core.config import SHARE_DIR


SERVER_NAME = "netdisk2weixin"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


def _response(id_, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def _tool_response(content_text, is_error=False):
    return {
        "content": [{"type": "text", "text": content_text}],
        "isError": is_error,
    }


def _run_platform_sync(platform, count=None, kw=None, include=None,
                       exclude=None, force=False, preview_only=False):
    if platform == "all":
        platforms = list(ADAPTERS)
    else:
        platforms = [platform]

    all_results = []
    for p in platforms:
        try:
            result = get_adapter(p)(
                count=count, kw=kw, include=include, exclude=exclude,
                force=force, preview_only=preview_only,
            )
            all_results.append(result)
        except Exception as e:
            all_results.append({"code": 500, "message": str(e), "platform": p})

    if len(all_results) == 1:
        return all_results[0]
    return {"code": 200, "platforms": all_results}


def _format_platform_result(result):
    if not isinstance(result, dict):
        return str(result)

    lines = []
    if "platform" in result:
        lines.append(f"【{result['platform']}】")
    lines.append(f"code: {result.get('code', '?')} | {result.get('message', '')}")

    if result.get("preview") and result.get("items"):
        lines.append("\n=== 预览资源 ===")
        for i, item in enumerate(result["items"][:20], 1):
            title = item.get("title", item.get("name", "?"))
            link = item.get("url", item.get("link", ""))
            lines.append(f"  {i}. {title}")
            if link:
                lines.append(f"     {link}")
        total = len(result["items"])
        if total > 20:
            lines.append(f"  ... 还有 {total - 20} 个")

    save_ok = result.get("save_success_count", 0)
    share_ok = result.get("share_success_count", 0)
    if save_ok or share_ok or result.get("share_results"):
        lines.append(f"\n转存成功: {save_ok} | 分享成功: {share_ok}")
        for r in result.get("share_results", [])[:10]:
            note = r.get("note", r.get("title", ""))[:40]
            url = r.get("share_url", r.get("url", ""))
            lines.append(f"  ✅ {note}")
            if url:
                lines.append(f"     {url}")

    wechat = result.get("wechat_result")
    if wechat:
        lines.append(f"\n企业微信: {'✅' if wechat.get('success') else '❌'} {wechat.get('message', '')}")

    return "\n".join(lines)


def _format_text_result(result):
    lines = []
    lines.append(f"code: {result.get('code', '?')} | {result.get('message', '')}")
    lines.append(f"识别链接: {result.get('total', 0)}")

    success = sum(1 for r in result.get("results", []) if r.get("status") == "success")
    fail = sum(1 for r in result.get("results", []) if r.get("status") != "success")
    lines.append(f"成功: {success} | 失败: {fail}")

    for r in result.get("results", []):
        icon = "✅" if r.get("status") == "success" else "❌"
        orig = r.get("original_url", "")[:60]
        new = r.get("new_url", "")
        msg = r.get("message", "")
        lines.append(f"  {icon} [{r.get('platform', '?')}] {orig}")
        if new:
            lines.append(f"     → {new}")
        elif msg:
            lines.append(f"     失败: {msg}")

    replaced = result.get("replaced_text", "")
    if replaced:
        lines.append("\n=== 替换后文本 ===")
        lines.append(replaced)

    return "\n".join(lines)


def _get_share_history(limit=20):
    files = ["share_url_total.txt", "share_history.json"]
    for fname in files:
        fpath = SHARE_DIR / fname
        if fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                lines = content.strip().splitlines()
                tail = lines[-limit:]
                return f"=== {fname} (共 {len(lines)} 条, 显示最近 {limit} 条) ===\n" + "\n".join(tail)
            except Exception as e:
                return f"读取 {fname} 失败: {e}"
    return f"历史目录: {SHARE_DIR}\n未找到历史文件"


async def handle_tools_list():
    return {
        "tools": [
            {
                "name": "run_platform",
                "description": (
                    "从在线聚合站搜索网盘资源，自动转存到自己的网盘，"
                    "生成新的分享链接，上传引流PDF（夸克），推送到企业微信群。"
                    "这是网盘拉新的核心入口。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "platform": {
                            "type": "string",
                            "description": "平台选择",
                            "enum": ["quark", "baidu", "all"],
                        },
                        "count": {
                            "type": "integer",
                            "description": "转存资源数量，默认用 .env 中 COUNT",
                        },
                        "kw": {
                            "type": "string",
                            "description": "API 搜索关键词（多数中文 API 不支持，建议用 include）",
                        },
                        "include": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "标题必须包含的关键词（本地过滤，支持中文，可多个同时匹配）",
                        },
                        "exclude": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "标题必须排除的关键词（本地过滤，可多个）",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "跳过缓存，强制重新请求在线 API",
                        },
                        "preview": {
                            "type": "boolean",
                            "description": "只预览资源列表，不执行转存",
                        },
                    },
                    "required": ["platform"],
                },
            },
            {
                "name": "run_text_replace",
                "description": (
                    "识别一段文本中的夸克/百度网盘分享链接+提取码，"
                    "自动转存到自己的网盘并替换成自己的分享链接。"
                    "用于改写别人发的推广文案。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "要处理的文本（与 text_file 二选一）",
                        },
                        "text_file": {
                            "type": "string",
                            "description": "从文件读取文本（与 text 二选一）",
                        },
                        "push_wechat": {
                            "type": "boolean",
                            "description": "替换完成后推送企业微信，默认 true",
                        },
                    },
                },
            },
            {
                "name": "preview_resources",
                "description": "只预览聚合站返回的资源列表，不执行转存。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "platform": {
                            "type": "string",
                            "enum": ["quark", "baidu", "all"],
                        },
                        "kw": {"type": "string"},
                        "include": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "exclude": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["platform"],
                },
            },
            {
                "name": "get_share_history",
                "description": "读取最近的转存分享历史记录。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "显示最近几条，默认 20",
                        },
                    },
                },
            },
        ],
    }


async def handle_tool_call(name, arguments):
    args = arguments or {}

    if name == "run_platform":
        platform = args.get("platform", "quark")
        result = await asyncio.to_thread(
            _run_platform_sync,
            platform=platform,
            count=args.get("count"),
            kw=args.get("kw"),
            include=args.get("include"),
            exclude=args.get("exclude"),
            force=args.get("force", False),
            preview_only=False,
        )
        text = _format_platform_result(result)
        return _tool_response(text, is_error=(result.get("code", 200) >= 400))

    if name == "preview_resources":
        platform = args.get("platform", "quark")
        result = await asyncio.to_thread(
            _run_platform_sync,
            platform=platform,
            kw=args.get("kw"),
            include=args.get("include"),
            exclude=args.get("exclude"),
            preview_only=True,
        )
        text = _format_platform_result(result)
        return _tool_response(text)

    if name == "run_text_replace":
        text = args.get("text")
        text_file = args.get("text_file")
        push_wechat = args.get("push_wechat", True)

        if text_file and os.path.isfile(text_file):
            with open(text_file, "r", encoding="utf-8") as f:
                text = f.read()
        if not text:
            return _tool_response("需要提供 text 或 text_file", is_error=True)

        from core.text_replacer import run_from_text
        result = await run_from_text(
            text, save_results=True, push_wechat=push_wechat,
        )
        return _tool_response(_format_text_result(result))

    if name == "get_share_history":
        text = await asyncio.to_thread(_get_share_history, args.get("limit", 20))
        return _tool_response(text)

    return _tool_response(f"未知工具: {name}", is_error=True)


async def handle_request(req):
    method = req.get("method")
    id_ = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return _response(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}},
        })

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _response(id_, {})

    if method == "tools/list":
        return _response(id_, await handle_tools_list())

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = await handle_tool_call(name, arguments)
            return _response(id_, result)
        except Exception as e:
            import traceback
            return _response(id_, _tool_response(
                f"执行 {name} 出错: {e}\n{traceback.format_exc()}",
                is_error=True,
            ))

    return _response(id_, error={
        "code": -32601, "message": f"Method not found: {method}",
    })


def _read_stdin_thread(q: queue.Queue, stop_event: threading.Event):
    try:
        for raw in sys.stdin.buffer:
            if stop_event.is_set():
                break
            q.put(raw.strip())
    except Exception:
        pass
    finally:
        q.put(None)


async def stdio_main():
    line_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    t = threading.Thread(target=_read_stdin_thread, args=(line_queue, stop_event),
                         daemon=True)
    t.start()

    loop = asyncio.get_event_loop()

    def _get_line():
        while True:
            item = line_queue.get()
            if item is None:
                return None
            decoded = item.decode("utf-8").strip()
            if decoded:
                return decoded

    try:
        while True:
            req_line = await loop.run_in_executor(None, _get_line)
            if req_line is None:
                break
            try:
                req_line = req_line.lstrip("\ufeff")
                req = json.loads(req_line)
                resp = await handle_request(req)
                if resp is not None:
                    sys.stdout.buffer.write(
                        (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                    sys.stdout.buffer.flush()
            except Exception as e:
                import traceback
                err_resp = _response(None, error={
                    "code": -32700,
                    "message": f"Parse error: {e}\n{traceback.format_exc()}",
                })
                sys.stdout.buffer.write(
                    (json.dumps(err_resp) + "\n").encode("utf-8")
                )
                sys.stdout.buffer.flush()
    finally:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(stdio_main())
