import re
import os
import time
import random
import asyncio
import urllib.parse

from core.config import load_config, get_kuake_cli, get_yinliu_files, SHARE_DIR
from core.logger import log_print


LINK_PATTERNS = [
    ("quark", re.compile(
        r"(https?://"
        r"(?:pan\.quark\.cn|share\.quark\.cn|pan\.quark\.com|share\.quark\.com)"
        r"/s/[a-zA-Z0-9]+"
        r"(?:[?&][^\s，。、\)\]】]*)?)",
        re.IGNORECASE
    )),
    ("baidu", re.compile(
        r"(https?://"
        r"(?:pan\.baidu\.com|yun\.baidu\.com)"
        r"/(?:s/[a-zA-Z0-9_-]+|share/init\?surl=[a-zA-Z0-9_-]+)"
        r"(?:[?&][^\s，。、\)\]】]*)?)",
        re.IGNORECASE
    )),
]

PWD_PATTERN = re.compile(r"pwd[=:：]\s*([a-zA-Z0-9]{4})")
CODE_PATTERN = re.compile(r"(?:提取码|密码|验证码|passcode)[=:：]\s*([a-zA-Z0-9]{4})")

# 链接行常见的「引导词」——剥离 URL 后如果只剩这些，说明真正的标题在上一行。
# 例：「链接：https://...」「地址：https://...」「提取码：ab12 https://...」
_TITLE_NOISE_RE = re.compile(
    r"^(?:"
    r"链接|下载链接|网盘链接|分享链接|资源链接|链\s*接|地址|下载地址|网盘地址|"
    r"提取码|密码|验证码|passcode|pwd|口令|复制|点击|请|点此|"
    r"link|url|链接地址|下载|下载地址"
    r")\s*[：:，,。.、\-—|｜]?\s*$",
    re.IGNORECASE,
)

# 标题里出现这些词，基本可判定不是标题（是引导语/表头）
_TITLE_HINT_RE = re.compile(
    r"(?:链接|地址|提取码|密码|复制|打开|点击|下载)", re.IGNORECASE)


def _clean_title_line(line: str) -> str:
    """把一行文本清洗成候选标题：去 URL、去 emoji 序列、去首尾分隔符。"""
    if not line:
        return ""
    s = re.sub(r"https?://\S+", " ", line)
    # 去掉 emoji（含变体选择符、ZWJ 组合）与常见符号前缀
    s = re.sub(r"[\U00010000-\U0010ffff\U0001F000-\U0001FAFF\ufe0f\u200d]", "", s)
    s = s.strip().rstrip("：:-—|｜><=*·. \t")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_noise_title(s: str) -> bool:
    """判断清洗后的字符串是否只是无意义的引导词。"""
    if not s:
        return True
    if _TITLE_NOISE_RE.match(s):
        return True
    # 纯符号/纯数字（且很短）也认为无意义
    if not re.search(r"[\u4e00-\u9fff a-zA-Z]", s):
        return True
    if len(s) <= 2 and _TITLE_HINT_RE.search(s):
        return True
    return False


def _pick_display_title(origin_title: str, api_title: str, index) -> str:
    """决定展示/写飞书用的资源名称。

    **优先级：转存成功时显示的名称（分享接口返回的 title）最高**——
    这是网盘里真实的资源名，与「转存成功」日志一致，也是用户期望看到的。

    原文标题只在接口没返回 title 时作为兜底；且必须先做噪音判断
    （历史实现直接 `origin_title or title`，origin_title 是「链接」这类
    引导词时会把正确的 api_title 盖掉，导致飞书资源名为「链接」）。
    """
    api = (api_title or "").strip()
    if api and api != "unknown":
        return api
    if origin_title and not _is_noise_title(origin_title):
        return origin_title
    return f"资源{index}"


def extract_origin_title(text: str, span, url: str, max_lookback: int = 6) -> str:
    """从原文本中提取链接对应的「原文标题」。

    策略（两行式文案是主流，必须支持回溯）：
      1. 先看链接所在行，剥离 URL 后若还有实质内容 → 用它
      2. 否则**向上回溯**若干行，找到第一个像标题的行
      3. 都找不到返回空串（调用方会回退到分享接口返回的标题）

    原实现只看链接所在行，遇到「标题在上一行、链接独占一行」的常见排版时
    会提取出「链接」二字，并因为 `origin_title or title` 的优先级把正确标题
    覆盖掉。这里补上回溯与噪音过滤。
    """
    if not url or not span:
        return ""
    lines = text.splitlines(keepends=True)
    # 定位链接所在行的索引
    pos = 0
    cur_line_idx = -1
    for li, ln in enumerate(lines):
        if pos <= span[0] < pos + len(ln):
            cur_line_idx = li
            break
        pos += len(ln)
    if cur_line_idx < 0:
        return ""

    # ① 链接所在行
    cand = _clean_title_line(lines[cur_line_idx])
    if not _is_noise_title(cand):
        return cand

    # ② 向上回溯
    steps = 0
    li = cur_line_idx - 1
    while li >= 0 and steps < max_lookback:
        cand = _clean_title_line(lines[li])
        if not _is_noise_title(cand):
            # 太长的行多半是段落正文而非标题，但仍可接受（截断）
            return cand[:120]
        li -= 1
        steps += 1

    return ""


def extract_links(text):
    found = []
    for platform, pattern in LINK_PATTERNS:
        for m in pattern.finditer(text):
            url = m.group(1)
            password = ""
            pwd_match = PWD_PATTERN.search(url)
            if pwd_match:
                password = pwd_match.group(1)
            else:
                ctx_start = max(0, m.start() - 30)
                ctx_end = min(len(text), m.end() + 30)
                ctx = text[ctx_start:ctx_end]
                ctx_no_url = ctx.replace(url, "")
                code_match = CODE_PATTERN.search(ctx_no_url)
                if code_match:
                    password = code_match.group(1)
            found.append({
                "platform": platform,
                "url": url,
                "password": password,
                "span": (m.start(), m.end()),
            })
    found.sort(key=lambda x: x["span"][0])
    dedup = []
    seen_urls = set()
    for item in found:
        if item["url"] not in seen_urls:
            dedup.append(item)
            seen_urls.add(item["url"])
    return dedup


async def _save_and_share_quark(url, password, folder_fid, kuake_cli, pdf_path="",
                                folder_path=""):
<<<<<<< HEAD
    from adapters.quark import QuarkPanFileManager, _upload_yinliu_files
=======
    from adapters.quark import (QuarkPanFileManager, _upload_yinliu_pdf,
                                clean_yinliu_after_save,
                                _resolve_top_fids_to_paths)
>>>>>>> e4fb7ff1f606566358dfbe04a902afb748bf9c53
    config = load_config()
    quark_cookie = config["quark"].get("cookie", "")

    if not quark_cookie:
        raise RuntimeError("夸克未配置 Cookie（请在 .env 填写 QUARK_COOKIE）")

    if password and "pwd=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}pwd={password}"

    manager = QuarkPanFileManager(cookie=quark_cookie)

    save_result = await manager.run(url.strip(), folder_fid)
    if save_result is None:
        raise RuntimeError("转存结果为空")

    file_ids = save_result["data"]["save_as"]["save_as_top_fids"]

    # ── 转存后、分享前：清理别人塞在里面的引流文件 ──────────────────────
    # 与批量模式（adapters/quark.py）保持一致的行为：把转存进来的顶层条目
    # 展开成路径，交给 clean_yinliu_after_save 按规则删掉加群/公众号之类的
    # 引流文件，避免自己的分享里夹带别人的引流内容。
    cleanup_result = None
    filter_cfg = config.get("clean_yinliu", {}) or {}
    if filter_cfg.get("enabled"):
        try:
            top_paths = _resolve_top_fids_to_paths(kuake_cli, folder_fid, file_ids)
            if top_paths:
                log_print(f"开始清理转存内容中的引流文件（{len(top_paths)} 个顶层条目）...", "INFO")
                # 排除我们自己的引流 PDF 名，避免刚上传的 99999.pdf 被误删
                pdf_name = (os.path.splitext(os.path.basename(pdf_path))[0]
                            if pdf_path else "")
                cleanup_excludes = [pdf_name] if pdf_name else None
                cleanup_result = clean_yinliu_after_save(
                    kuake_cli, top_paths, filter_cfg, excludes=cleanup_excludes)
                log_print(
                    f"引流清理完成: 删除 {len(cleanup_result.get('deleted', []))} 个",
                    "SUCCESS" if cleanup_result.get("deleted") else "INFO")
            else:
                log_print("未能解析转存条目的路径，跳过引流清理", "WARNING")
        except Exception as e:
            # 清理只是优化项，失败不能阻断转存/分享主流程
            log_print(f"引流清理异常（已跳过）: {e}", "WARNING")
    else:
        log_print("引流清理未开启（.env 的 CLEAN_YINLIU_ENABLED），跳过", "INFO")

    await asyncio.sleep(random.uniform(1, 3))

    share_result = await manager.share_run(
        file_ids, folder_id=folder_fid,
        url_type=1, expired_type=4,
        password="", traverse_depth=0
    )

    yinliu_files = [pdf_path] if isinstance(pdf_path, str) else list(pdf_path or [])
    yinliu_files = [p for p in yinliu_files if p and os.path.exists(p)]
    if yinliu_files:
        title = share_result.get("title", "unknown")
        _upload_yinliu_files(kuake_cli, yinliu_files, title, folder_path)

    return {
        "platform": "quark",
        "save_result": save_result,
        "share_result": share_result,
        "cleanup_result": cleanup_result,
    }


def _save_and_share_baidu(url, password, target_folder):
    """百度网盘：转存 + 分享（文本替换路径）。

    直接复用 adapters.baidu.run() 的单资源模式，而不是另写一套 HTTP 调用。
    早期版本这里手写了一套 network.transfer()/get_transfer_params()，
    但 adapters.baidu.Network 里并没有这两个方法（只有 transfer_file），
    于是每次都会抛 "too many values to unpack / AttributeError"，
    百度链路实际上从未跑通过。复用 run() 可以保证两条路径行为一致。
    """
    from adapters import baidu as baidu_adapter

    # run() 内部会自己读 config 里的 cookie / 目录，这里只需要把单个资源塞进去。
    # selected_items 走的是"已有资源直接处理"分支，跳过缓存与随机抽样。
    item = {
        "url": url,
        "password": password or "",
        "note": url,
    }

    cfg = load_config().get("baidu", {})
    result = baidu_adapter.run(
        count=1,
        selected_items=[item],
        preview_only=False,
        platform_cfg=cfg,
        notify=False,   # 文本替换路径统一由外层推一条，避免重复推送
    )

    if not result or result.get("code") != 200:
        raise RuntimeError((result or {}).get("message", "百度转存失败"))

    share_results = result.get("share_results") or []
    if not share_results:
        raise RuntimeError("百度转存后未拿到分享链接（可能被限流或资源已失效）")

    first = share_results[0]
    # run() 的 note 优先用调用方传入的（文本替换路径传的是 URL，没有意义），
    # 回退到真正转存下来的文件名。
    api_title = first.get("file_name") or ""
    return {
        "platform": "baidu",
        "share_result": {
            "url": first.get("share_url", ""),
            "title": api_title,
            "size": first.get("size", ""),
        },
    }


async def run_from_text(text, save_results=False, push_wechat=False):
    config = load_config()
    links = extract_links(text)

    if not links:
        log_print("文本中未识别到任何网盘链接", "WARNING")
        return {"code": 404, "message": "未找到网盘链接", "total": 0,
                "replaced_text": text, "results": []}

    log_print(f"识别到 {len(links)} 个网盘链接")
    for i, l in enumerate(links):
        log_print(f"  [{i+1}/{len(links)}] {l['platform']} - {l['url'][:80]}", "INFO")

    from adapters.quark import _resolve_fid_to_path

    kuake_cli = get_kuake_cli(config)
    pdf_path = get_yinliu_files(config)
    quark_cfg = config["quark"]
    folder_fid = quark_cfg.get("folder_fid", "0") or "0"
    folder_path = _resolve_fid_to_path(kuake_cli, folder_fid)
    baidu_target = config.get("baidu", {}).get("target_folder") or config.get("target_folder", "下载")

    results = []
    new_text = text
    offset = 0

    for i, link in enumerate(links):
        original_url = link["url"]
        platform = link["platform"]
        password = link["password"]
        new_url = None
        error_msg = None

        # 提取该链接在原文本中的「原文标题」：
        # 优先用行内/上一行的文案标题，避免推送时用的是分享接口返回的内部标题。
        # 详见 extract_origin_title 的文档（支持向上回溯 + 噪音过滤）。
        origin_title = extract_origin_title(new_text, link["span"], original_url)

        try:
            log_print(f"\n--- 处理 {i+1}/{len(links)}: [{platform}] {original_url[:60]} ---")
            title = ""
            size = ""
            if platform == "quark":
                result = await _save_and_share_quark(
                    original_url, password, folder_fid, kuake_cli,
                    pdf_path, folder_path
                )
                sr = result["share_result"]
                new_url = sr.get("share_url", "")
                title = sr.get("title", "")
                size = sr.get("size", "")
                log_print(f"夸克转存+分享成功: {title}", "SUCCESS")
            elif platform == "baidu":
                result = _save_and_share_baidu(original_url, password, baidu_target)
                sr = result["share_result"]
                if isinstance(sr, dict):
                    new_url = sr.get("url", "")
                    title = sr.get("title", "")
                    size = sr.get("size", "")
                elif isinstance(sr, str):
                    new_url = sr
                log_print(f"百度转存+分享成功", "SUCCESS")
        except Exception as e:
            error_msg = str(e)
            log_print(f"处理失败: {e}", "ERROR")

        if new_url:
            if offset == 0:
                search_start = 0
            else:
                search_start = max(0, links[i]["span"][0] + offset - 20)
            idx = new_text.find(original_url, search_start)
            if idx >= 0:
                tail_start = idx + len(original_url)
                new_text = new_text[:idx] + new_url + new_text[tail_start:]
                offset += len(new_url) - len(original_url)
                delta = len(new_url) - len(original_url)

                # 百度链接的提取码：新链接自带 ?pwd=xxxx 时，原文旁边的
                # 「提取码: 6666」还是旧分享的密码，必须一起改掉，
                # 否则用户拿着新链接 + 旧密码必然打不开。
                pwd_m = re.search(r"[?&]pwd=([A-Za-z0-9]{4})", new_url)
                if pwd_m:
                    new_pwd = pwd_m.group(1)
                    after = new_text[tail_start + delta:tail_start + delta + 40]
                    code_m = CODE_PATTERN.search(after)
                    if code_m and code_m.group(1) != new_pwd:
                        a, b = (tail_start + delta + code_m.start(1),
                                tail_start + delta + code_m.end(1))
                        new_text = new_text[:a] + new_pwd + new_text[b:]
                        offset += len(new_pwd) - len(code_m.group(1))
        else:
            # 转存失败：把原链接替换成「转存失败」字样。
            # 绝不能保留别人的原链接 —— 那样发出去等于替原作者导流，
            # 而且用户根本看不出哪条没成功。
            idx = new_text.find(original_url)
            if idx >= 0:
                tail_end = idx + len(original_url)
                # 连带删掉紧随其后的「提取码: xxxx」，否则会留下悬空的提取码
                after = new_text[tail_end:tail_end + 40]
                code_m = CODE_PATTERN.search(after)
                if code_m:
                    tail_end += code_m.end()
                    # 吞掉提取码后面多余的空白
                    while tail_end < len(new_text) and new_text[tail_end] in " \t":
                        tail_end += 1
                replaced_len = tail_end - idx
                new_text = new_text[:idx] + "转存失败" + new_text[tail_end:]
                offset += len("转存失败") - replaced_len
                log_print(f"链接已替换为「转存失败」: {original_url[:50]}", "WARNING")

        results.append({
            "index": i + 1,
            "platform": platform,
            "original_url": original_url,
            "password": password,
            "new_url": new_url,
            "title": title,
            "origin_title": origin_title,
            "display_title": _pick_display_title(origin_title, title, i + 1),
            "size": size,
            "status": "success" if new_url else "error",
            "message": error_msg,
        })

        time.sleep(random.uniform(1, 2))

    if save_results:
        try:
            os.makedirs(SHARE_DIR, exist_ok=True)
            share_total_file = str(SHARE_DIR / "share_url_total.txt")
            with open(share_total_file, "a", encoding="utf-8") as f:
                for r in results:
                    title = r.get("platform", "") + "_" + str(r["index"])
                    orig = r.get("original_url", "")
                    new = r.get("new_url", "")
                    if new:
                        f.write(f"{title}  |  {new}  |  DONE\n")
                    elif orig:
                        f.write(f"{title}  |  {orig}  |  PICKED\n")
        except Exception as e:
            log_print(f"写入历史失败: {e}", "WARNING")

    success_count = sum(1 for r in results if r["status"] == "success")
    log_print(f"\n===== 处理完成: {success_count}/{len(results)} 成功 =====")

    wechat_result = None
    if push_wechat and success_count > 0:
        import requests as _requests
        webhook_url = config.get("wechat", {}).get("webhook_url", "")
        if webhook_url:
            # 只发一条：替换后的完整文案（不带任何标题头），供直接复制转发。
            # 2026-10-18 应用户要求：去掉摘要卡片和「替换后完整文本」头部，
            # 之前发两条（摘要 + 全文）看起来像重复推送。
            full_text = new_text
            max_len = 3800
            chunks = [full_text[i:i + max_len] for i in range(0, len(full_text), max_len)]
            ok_chunks = 0
            for ci, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    chunk = f"[ {ci+1}/{len(chunks)} ]\n\n" + chunk
                try:
                    _requests.post(
                        webhook_url,
                        json={"msgtype": "text", "text": {"content": chunk.strip()}},
                        timeout=10,
                    )
                    ok_chunks += 1
                    log_print(f"企业微信推送分片 {ci+1}/{len(chunks)} 成功", "SUCCESS")
                except Exception as e:
                    log_print(f"企业微信推送分片 {ci+1} 失败: {e}", "ERROR")
            wechat_result = {"success": ok_chunks > 0, "chunks": len(chunks)}
        else:
            log_print("未配置 webhook_url，跳过企业微信推送", "WARNING")

    feishu_result = None
    fs_cfg = config.get("feishu", {})
    if fs_cfg.get("enabled") and success_count > 0:
        try:
            from core.feishu_client import batch_write_resources
            feishu_items = []
            for r in results:
                if r["status"] == "success" and r.get("new_url"):
                    feishu_items.append({
                        # 资源名称 = 转存成功时显示的名称（分享接口返回的 title）
                        "name": r.get("display_title") or f"资源{r.get('index','?')}",
                        "url": r["new_url"],
                        "size": r.get("size", ""),
                        "tag": r.get("platform", ""),
                        "search": r.get("display_title") or "",
                        "update_time": time.strftime("%Y-%m-%d"),
                    })
            if feishu_items:
                feishu_result = batch_write_resources(config, feishu_items)
        except Exception as e:
            log_print(f"飞书写入异常: {e}", "ERROR")

    return {
        "code": 200 if success_count > 0 else 400,
        "message": f"成功 {success_count}/{len(results)}",
        "total": len(results),
        "success_count": success_count,
        "replaced_text": new_text,
        "results": results,
        "wechat_result": wechat_result,
        "feishu_result": feishu_result,
    }
