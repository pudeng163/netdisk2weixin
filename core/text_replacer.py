import re
import os
import time
import random
import asyncio
import urllib.parse

from core.config import load_config, get_kuake_cli, get_yinliu_pdf, SHARE_DIR
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
    from adapters.quark import QuarkPanFileManager, QuarkLogin, _upload_yinliu_pdf
    config = load_config()
    quark = config["quark"]
    cookie_env = quark.get("cookie", "")
    cookie_file = str(config.get("cache_dir", "data")) + "/cookies.txt"

    if not cookie_env:
        login = QuarkLogin(cookie_file=cookie_file)
        if login.check_cookies() is None:
            raise RuntimeError("夸克未配置 Cookie")

    if password and "pwd=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}pwd={password}"

    manager = QuarkPanFileManager(headless=False, slow_mo=500,
                                  cookie=cookie_env, cookie_file=cookie_file)

    save_result = await manager.run(url.strip(), folder_fid)
    if save_result is None:
        raise RuntimeError("转存结果为空")

    file_ids = save_result["data"]["save_as"]["save_as_top_fids"]

    await asyncio.sleep(random.uniform(1, 3))

    share_result = await manager.share_run(
        file_ids, folder_id=folder_fid,
        url_type=1, expired_type=4,
        password="", traverse_depth=0
    )

    if pdf_path and os.path.exists(pdf_path):
        title = share_result.get("title", "unknown")
        _upload_yinliu_pdf(kuake_cli, pdf_path, title, folder_path)

    return {
        "platform": "quark",
        "save_result": save_result,
        "share_result": share_result,
    }


def _save_and_share_baidu(url, password, target_folder):
    from adapters.baidu import Network, normalize_link, parse_url_and_code

    config = load_config()
    baidu = config["baidu"]
    cookie = baidu.get("cookie", "")
    if not cookie:
        raise RuntimeError("百度未配置 Cookie")

    network = Network()
    network.headers["Cookie"] = cookie

    bdstoken = network.get_bdstoken()
    if isinstance(bdstoken, int):
        raise RuntimeError(f"获取 bdstoken 失败: {bdstoken}")
    network.bdstoken = bdstoken

    result = network.get_dir_list(f"/{target_folder}")
    if isinstance(result, int):
        rc = network.create_dir(target_folder)
        if rc != 0:
            raise RuntimeError(f"创建目录失败: {rc}")

    link_text = f"{url} {password}" if password else url
    normalized = normalize_link(link_text)
    parsed_url, code = parse_url_and_code(normalized)
    if not parsed_url:
        raise RuntimeError(f"无法解析百度链接: {url}")

    if code:
        bdclnd = network.verify_pass_code(parsed_url, code)
        if isinstance(bdclnd, int):
            raise RuntimeError(f"验证码校验失败: {bdclnd}")

    fs_ids, filenames = network.get_transfer_params(parsed_url)
    if not fs_ids:
        raise RuntimeError("获取转存参数失败")

    save_result = network.transfer(fs_ids, filenames, f"/{target_folder}")
    if save_result.get("errno", 0) != 0:
        raise RuntimeError(f"转存失败: {save_result}")

    fs_id = fs_ids[0] if isinstance(fs_ids, list) else fs_ids
    random_code = "".join(random.choices("abcdefghijklmnopqrstuvwxyz1234567890", k=4))
    share_result = network.create_share(fs_id, expiry=4, password=random_code)

    return {
        "platform": "baidu",
        "save_result": save_result,
        "share_result": share_result,
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
    pdf_path = get_yinliu_pdf(config)
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

        try:
            log_print(f"\n--- 处理 {i+1}/{len(links)}: [{platform}] {original_url[:60]} ---")
            if platform == "quark":
                result = await _save_and_share_quark(
                    original_url, password, folder_fid, kuake_cli,
                    pdf_path, folder_path
                )
                sr = result["share_result"]
                new_url = sr.get("share_url", "")
                log_print(f"夸克转存+分享成功: {sr.get('title', '')}", "SUCCESS")
            elif platform == "baidu":
                result = _save_and_share_baidu(original_url, password, baidu_target)
                sr = result["share_result"]
                if isinstance(sr, dict):
                    new_url = sr.get("url", "")
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
                new_text = new_text[:idx] + new_url + new_text[idx + len(original_url):]
                offset += len(new_url) - len(original_url)

        results.append({
            "index": i + 1,
            "platform": platform,
            "original_url": original_url,
            "password": password,
            "new_url": new_url,
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
                    if r["new_url"]:
                        title = r.get("platform", "") + "_" + str(r["index"])
                        f.write(f"{title}  |  {r['new_url']}\n")
        except Exception as e:
            log_print(f"写入历史失败: {e}", "WARNING")

    success_count = sum(1 for r in results if r["status"] == "success")
    log_print(f"\n===== 处理完成: {success_count}/{len(results)} 成功 =====")

    wechat_result = None
    if push_wechat and success_count > 0:
        from core.notifier import send_wechat_notification
        import requests as _requests
        webhook_url = config.get("wechat", {}).get("webhook_url", "")
        if webhook_url:
            shares = []
            for r in results:
                if r["new_url"]:
                    shares.append({
                        "note": r.get("platform", "") + " " + r.get("original_url", "")[-20:],
                        "share_url": r["new_url"],
                    })
            send_wechat_notification(webhook_url, shares, kw="文本替换")

            header_msg = "===== 替换后完整文本 ====="
            max_len = 3800
            full_text = header_msg + "\n\n" + new_text
            chunks = [full_text[i:i + max_len] for i in range(0, len(full_text), max_len)]
            for ci, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    chunk = f"[ {ci+1}/{len(chunks)} ]\n\n" + chunk
                try:
                    _requests.post(
                        webhook_url,
                        json={"msgtype": "text", "text": {"content": chunk.strip()}},
                        timeout=10,
                    )
                    log_print(f"企业微信推送分片 {ci+1}/{len(chunks)} 成功", "SUCCESS")
                except Exception as e:
                    log_print(f"企业微信推送分片 {ci+1} 失败: {e}", "ERROR")
            wechat_result = {"success": True, "chunks": len(chunks)}
        else:
            log_print("未配置 webhook_url，跳过企业微信推送", "WARNING")

    return {
        "code": 200 if success_count > 0 else 400,
        "message": f"成功 {success_count}/{len(results)}",
        "total": len(results),
        "success_count": success_count,
        "replaced_text": new_text,
        "results": results,
        "wechat_result": wechat_result,
    }
