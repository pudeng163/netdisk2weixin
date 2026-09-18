"""夸克网盘适配器。

迁移自 fresh_quark2weixin.py，实现统一适配器接口 run()。
依赖 playwright（登录）+ httpx（API）+ kuake CLI（上传引流 PDF）。
kuake 可执行文件路径与引流 PDF 路径已配置化（core.config）。
"""
import os
import re
import sys
import time
import json
import random
import string
import asyncio
from datetime import datetime
from typing import Union, Dict, Any, List, Tuple

import httpx

from core import cache
from core.config import (load_config, get_kuake_cli, get_yinliu_pdf,
                         resolve_path, CACHE_DIR, SHARE_DIR, LOG_DIR)
from core.logger import log_print
from core.notifier import send_wechat_notification

# 历史推送记录文件（用于去重）
SHARE_TOTAL_FILE = str(SHARE_DIR / "share_url_total.txt")

# 需要保留的全局（供登录等使用）
_quark_cookies_env = ""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def get_datetime(timestamp=None, fmt="%Y-%m-%d %H:%M:%S"):
    if timestamp is None or not isinstance(timestamp, (int, float)):
        return datetime.today().strftime(fmt)
    return datetime.fromtimestamp(timestamp).strftime(fmt)


def get_timestamp(length):
    if length == 13:
        return int(time.time()) * 1000
    return int(time.time())


def generate_random_code(length=4):
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for _ in range(length))


def format_size(size_bytes):
    KB, MB, GB, TB = 1024, 1024 ** 2, 1024 ** 3, 1024 ** 4
    if size_bytes >= TB:
        return f"{size_bytes / TB:.1f}TB"
    if size_bytes >= GB:
        return f"{size_bytes / GB:.1f}GB"
    if size_bytes >= MB:
        return f"{size_bytes / MB:.1f}MB"
    if size_bytes >= KB:
        return f"{size_bytes / KB:.1f}KB"
    return f"{size_bytes}B"


def save_text(path, content, mode="w"):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        f.write(content)


def load_pushed_titles():
    """从历史记录中加载已推送的资源名称集合。"""
    pushed = set()
    if not os.path.exists(SHARE_TOTAL_FILE):
        return pushed
    try:
        with open(SHARE_TOTAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if parts and parts[0].strip():
                    pushed.add(parts[0].strip())
    except Exception as e:
        log_print(f"加载历史推送记录失败: {str(e)}", "WARNING")
    return pushed


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
class QuarkLogin:
    def __init__(self, headless=True, slow_mo=0, cookie_file=None, cookies_env=""):
        self.headless = headless
        self.slow_mo = slow_mo
        self.cookie_file = cookie_file
        self.cookies_env = cookies_env

    @staticmethod
    def transfer_cookies(cookies_list):
        out = {}
        for c in cookies_list:
            if "quark" in c.get("domain", ""):
                out[c["name"]] = c["value"]
        return out

    @staticmethod
    def dict_to_cookie_str(cookies_dict):
        return "; ".join(f"{k}={v}" for k, v in cookies_dict.items())

    def check_cookies(self):
        if not os.path.exists(self.cookie_file):
            return None
        try:
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                content = f.read()
            if not content:
                return None
            if "[" in content:
                saved = eval(content)
                cookies_dict = self.transfer_cookies(saved)
                if "expires" in cookies_dict and \
                        int(time.time()) > int(cookies_dict["expires"]):
                    return None
                return cookies_dict
            return content.strip()
        except Exception as e:
            log_print(f"检查 Cookie 失败: {e}", "WARNING")
            return None

    def get_cookies(self):
        if self.cookies_env:
            log_print("使用配置中的夸克 Cookie", "INFO")
            return self.cookies_env
        cookie = self.check_cookies()
        if isinstance(cookie, dict):
            return self.dict_to_cookie_str(cookie)
        if isinstance(cookie, str):
            return cookie
        # 触发浏览器登录
        return self._browser_login()

    def _browser_login(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log_print("未安装 playwright，无法浏览器登录", "ERROR")
            return None
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(LOG_DIR / "web_browser_data"),
                headless=self.headless,
                slow_mo=self.slow_mo,
                args=["--start-maximized"],
                no_viewport=True,
            )
            page = context.pages[0]
            page.goto("https://pan.quark.cn/")
            input("请在弹出的浏览器中登录夸克，登录成功后按 Enter 继续...")
            cookies = page.context.cookies()
            cookies_dict = self.transfer_cookies(cookies)
            save_text(self.cookie_file, json.dumps(cookies_dict, ensure_ascii=False))
            return self.dict_to_cookie_str(cookies_dict)


# ---------------------------------------------------------------------------
# 文件管理器
# ---------------------------------------------------------------------------
class QuarkPanFileManager:
    def __init__(self, headless=False, slow_mo=0, cookie="", cookie_file=None):
        self.headless = headless
        self.slow_mo = slow_mo
        self.cookie_file = cookie_file
        self.cookies = self._get_cookies(cookie)
        self.headers = {
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/94.0.4606.71 Safari/537.36 "
                           "Core/1.94.225.400 QQBrowser/12.2.5544.400"),
            "origin": "https://pan.quark.cn",
            "referer": "https://pan.quark.cn/",
            "accept-language": "zh-CN,zh;q=0.9",
            "cookie": self.cookies,
        }

    def _get_cookies(self, cookie):
        if cookie:
            return cookie
        login = QuarkLogin(headless=self.headless, slow_mo=self.slow_mo,
                           cookie_file=self.cookie_file)
        return login.get_cookies() or ""

    @staticmethod
    def get_pwd_id(share_url):
        return share_url.split("?")[0].split("/s/")[-1]

    async def get_stoken(self, pwd_id, password=""):
        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": "",
                  "__dt": random.randint(100, 9999), "__t": get_timestamp(13)}
        api = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token"
        data = {"pwd_id": pwd_id, "passcode": password}
        async with httpx.AsyncClient() as client:
            timeout = httpx.Timeout(60.0, connect=60.0)
            response = await client.post(api, json=data, params=params,
                                         headers=self.headers, timeout=timeout)
            j = response.json()
            if j.get("status") == 200 and j.get("data"):
                return j["data"]["stoken"]
            log_print(f"获取 stoken 失败：{j.get('message')}", "WARNING")
            return ""

    async def get_detail(self, pwd_id, stoken, pdir_fid="0"):
        api = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail"
        page = 1
        file_list = []
        async with httpx.AsyncClient() as client:
            while True:
                params = {
                    "pr": "ucpro", "fr": "pc", "uc_param_str": "",
                    "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": pdir_fid,
                    "force": "0", "_page": str(page), "_size": "50",
                    "_sort": "file_type:asc,updated_at:desc",
                    "__dt": random.randint(200, 9999), "__t": get_timestamp(13),
                }
                timeout = httpx.Timeout(60.0, connect=60.0)
                response = await client.get(api, headers=self.headers,
                                            params=params, timeout=timeout)
                j = response.json()
                is_owner = j["data"]["is_owner"]
                total = j["metadata"]["_total"]
                if total < 1:
                    return is_owner, file_list
                size = j["metadata"]["_size"]
                count = j["metadata"]["_count"]
                for f in j["data"]["list"]:
                    file_list.append({
                        "fid": f["fid"], "file_name": f["file_name"],
                        "file_type": f["file_type"], "dir": f["dir"],
                        "pdir_fid": f["pdir_fid"],
                        "include_items": f.get("include_items", ""),
                        "share_fid_token": f["share_fid_token"],
                        "status": f["status"],
                    })
                if total <= size or count < size:
                    return is_owner, file_list
                page += 1

    async def run(self, input_line, folder_id="0"):
        share_url = input_line.strip()
        match = re.search(r"pwd=(.*?)(?=$|&)", share_url)
        password = match.group(1) if match else ""
        pwd_id = self.get_pwd_id(input_line).split("#")[0]
        if not pwd_id:
            raise ValueError("文件分享链接不可为空")

        stoken = await self.get_stoken(pwd_id, password)
        if not stoken:
            raise ValueError("获取 stoken 失败")

        is_owner, data_list = await self.get_detail(pwd_id, stoken)
        if not data_list:
            return None

        if is_owner == 1:
            log_print("网盘中已存在该文件，无需再次转存", "INFO")
            return None

        fid_list = [i["fid"] for i in data_list]
        share_fid_token_list = [i["share_fid_token"] for i in data_list]
        task_id = await self.get_share_save_task_id(pwd_id, stoken, fid_list,
                                                    share_fid_token_list,
                                                    to_pdir_fid=folder_id)
        return await self.submit_task(task_id)

    async def get_share_save_task_id(self, pwd_id, stoken, first_ids,
                                     share_fid_tokens, to_pdir_fid="0"):
        url = "https://drive.quark.cn/1/clouddrive/share/sharepage/save"
        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": "",
                  "__dt": random.randint(600, 9999), "__t": get_timestamp(13)}
        data = {"fid_list": first_ids, "fid_token_list": share_fid_tokens,
                "to_pdir_fid": to_pdir_fid, "pwd_id": pwd_id, "stoken": stoken,
                "pdir_fid": "0", "scene": "link"}
        async with httpx.AsyncClient() as client:
            timeout = httpx.Timeout(60.0, connect=60.0)
            response = await client.post(url, json=data, headers=self.headers,
                                         params=params, timeout=timeout)
            return response.json()["data"]["task_id"]

    async def submit_task(self, task_id, retry=10):
        for i in range(retry):
            await asyncio.sleep(random.randint(1, 2))
            url = (f"https://drive-pc.quark.cn/1/clouddrive/task?pr=ucpro&fr=pc"
                   f"&uc_param_str=&task_id={task_id}&retry_index={i}"
                   f"&__dt=21192&__t={get_timestamp(13)}")
            async with httpx.AsyncClient() as client:
                timeout = httpx.Timeout(60.0, connect=60.0)
                response = await client.get(url, headers=self.headers, timeout=timeout)
                j = response.json()
            if j.get("message") == "ok":
                if j["data"]["status"] == 2:
                    return j
            else:
                if j.get("code") == 41013:
                    raise ValueError("网盘文件夹不存在，请重新获取保存目录")
                raise ValueError(f"转存失败: {j.get('message')}")
        raise ValueError("提交转存任务超时")

    async def get_share_task_id(self, fid, file_name, url_type=1,
                                expired_type=2, password=""):
        data = {"fid_list": fid, "title": file_name, "url_type": url_type,
                "expired_type": expired_type}
        if url_type == 2:
            data["passcode"] = password or generate_random_code()
        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        async with httpx.AsyncClient() as client:
            timeout = httpx.Timeout(60.0, connect=60.0)
            response = await client.post(
                "https://drive-pc.quark.cn/1/clouddrive/share", params=params,
                json=data, headers=self.headers, timeout=timeout)
            return response.json()["data"]["task_id"]

    async def get_share_id(self, task_id, max_retries=3):
        retry_count = 0
        while retry_count < max_retries:
            try:
                params = {"pr": "ucpro", "fr": "pc", "uc_param_str": "",
                          "task_id": task_id, "retry_index": str(retry_count)}
                async with httpx.AsyncClient() as client:
                    timeout = httpx.Timeout(60.0, connect=60.0)
                    response = await client.get(
                        "https://drive-pc.quark.cn/1/clouddrive/task",
                        params=params, headers=self.headers, timeout=timeout)
                    response.raise_for_status()
                    j = response.json()
                    if not j.get("data") or not j["data"].get("share_id"):
                        raise ValueError("share_id 为空")
                    return j["data"]["share_id"]
            except Exception as e:
                retry_count += 1
                log_print(f"获取 share_id 第 {retry_count} 次失败: {e}", "WARNING")
                if retry_count >= max_retries:
                    raise
                await asyncio.sleep(2)

    async def submit_share(self, share_id):
        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        data = {"share_id": share_id}
        async with httpx.AsyncClient() as client:
            timeout = httpx.Timeout(60.0, connect=60.0)
            response = await client.post(
                "https://drive-pc.quark.cn/1/clouddrive/share/password",
                params=params, json=data, headers=self.headers, timeout=timeout)
            j = response.json()
            share_url = j["data"]["share_url"]
            title = j["data"]["title"]
            size = format_size(j["data"]["size"])
            if "passcode" in j["data"]:
                share_url += f"?pwd={j['data']['passcode']}"
            return share_url, title, size

    async def share_run(self, share_fid, folder_id=None, url_type=1,
                        expired_type=2, password="", traverse_depth=2):
        try:
            self.folder_id = folder_id
            pwd_id = share_fid
            if traverse_depth == 0:
                task_id = await self.get_share_task_id(pwd_id, "根目录",
                                                        url_type=url_type,
                                                        expired_type=expired_type,
                                                        password=password)
                share_id = await self.get_share_id(task_id)
                share_url, title, size = await self.submit_share(share_id)
                return {"title": title, "share_url": share_url, "size": size}

            # 多级目录遍历分享（原逻辑），此处保留简化实现
            raise ValueError("当前仅支持 traverse_depth=0")
        except Exception as e:
            log_print(f"分享失败: {e}", "ERROR")
            raise


def _upload_yinliu_pdf(kuake_cli, pdf_path, title, subdir=""):
    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print(f"kuake 可执行文件不存在，跳过引流文件上传: {kuake_cli}", "WARNING")
        return
    if not os.path.exists(pdf_path):
        log_print(f"引流 PDF 不存在，跳过上传: {pdf_path}", "WARNING")
        return
    prefix = f"/{subdir}" if subdir else ""
    cmd = f'{kuake_cli} upload "{pdf_path}" "{prefix}/{title}/333333.pdf"'
    log_print(f"执行: {cmd}", "DEBUG")
    os.system(cmd)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
async def _batch_save_and_share(count, webhook_url, kuake_cli, pdf_path,
                                quark_cookie, cookie_file, kw=None,
                                include=None, exclude=None, preview_only=False,
                                selected_items=None, folder_fid="0",
                                yinliu_subdir="", force=False):
    manager = QuarkPanFileManager(headless=False, slow_mo=500,
                                  cookie=quark_cookie, cookie_file=cookie_file)

    if selected_items is not None:
        quark_data = selected_items
    else:
        quark_data = cache.fetch_api_data("quark", kw or "",
                                          expire_hours=24,
                                          force_refresh=force,
                                          include=include,
                                          exclude=exclude or ["可搜索"])
    if not quark_data:
        return {"code": 400, "message": "无法获取 API 数据且缓存不可用",
                "total_selected": 0, "save_success_count": 0,
                "share_success_count": 0, "share_results": [],
                "preview": preview_only, "items": None}

    pushed_titles = load_pushed_titles()
    if pushed_titles:
        before = len(quark_data)
        quark_data = [i for i in quark_data
                      if i.get("note", "") not in pushed_titles]
        log_print(f"过滤已推送资源 {before - len(quark_data)} 个，剩余 {len(quark_data)} 个", "INFO")

    if len(quark_data) < count:
        log_print(f"链接数量不足，调整选取数量为 {len(quark_data)}", "WARNING")
        count = len(quark_data)

    selected = random.sample(quark_data, count)

    if preview_only:
        items = [{"index": i + 1, "note": it.get("note", "无标题"),
                  "url": it["url"]} for i, it in enumerate(selected)]
        return {"code": 200, "message": "预览成功", "preview": True,
                "items": items, "total_selected": count,
                "total_found": len(quark_data), "save_success_count": 0,
                "share_success_count": 0, "share_results": []}

    save_results = []
    for index, item in enumerate(selected):
        url = item["url"]
        note = item.get("note", "")
        try:
            result = await manager.run(url.strip(), folder_fid)
            if result is None:
                raise ValueError("转存结果为空（可能已存在或未返回任务）")
            file_ids = result["data"]["save_as"]["save_as_top_fids"]
            save_results.append({"index": index + 1, "url": url, "note": note,
                                 "status": "success", "result": result,
                                 "file_id": file_ids})
            log_print(f"转存成功 [{index+1}/{count}]: {note}", "SUCCESS")
        except Exception as e:
            save_results.append({"index": index + 1, "url": url, "note": note,
                                 "status": "error", "message": str(e),
                                 "file_id": None})
            log_print(f"转存失败 [{index+1}/{count}]: {note} - {e}", "ERROR")

    success_count = sum(1 for r in save_results if r["status"] == "success")

    share_results = []
    share_success_count = 0
    for save_result in save_results:
        if save_result["status"] == "error":
            share_results.append({"index": save_result["index"],
                                  "note": save_result["note"],
                                  "url": save_result["url"], "status": "error",
                                  "message": "转存失败，无法生成分享链接"})
            continue
        try:
            file_id = save_result["result"]["data"]["save_as"]["save_as_top_fids"]
            delay = random.random() * 4
            time.sleep(delay)
            result = await manager.share_run(file_id, folder_id=folder_fid,
                                             url_type=1, expired_type=4,
                                             password="", traverse_depth=0)
            if isinstance(result, dict):
                result["index"] = save_result["index"]
                result["note"] = save_result["note"]
                result["original_url"] = save_result["url"]
            share_results.append(result)
            share_success_count += 1

            title = result.get("title", save_result["note"])
            share_url = result.get("share_url", "")
            log_print(f"分享成功 [{save_result['index']}]: {title} - {share_url}",
                      "SUCCESS")

            # 上传引流 PDF
            if pdf_path and os.path.exists(pdf_path):
                _upload_yinliu_pdf(kuake_cli, pdf_path, title, yinliu_subdir)

            # 记录历史
            os.makedirs(SHARE_DIR, exist_ok=True)
            with open(SHARE_TOTAL_FILE, "a", encoding="utf-8") as f:
                f.write(f"{title}  |  {share_url}\n")
        except Exception as e:
            share_results.append({"index": save_result.get("index",
                                                          len(share_results) + 1),
                                  "note": save_result.get("note", ""),
                                  "url": save_result.get("url", "unknown"),
                                  "status": "error",
                                  "message": f"生成分享链接失败: {e}"})

    wechat_result = None
    if webhook_url:
        success_shares = [r for r in share_results
                          if isinstance(r, dict) and r.get("share_url")]
        if success_shares:
            wechat_result = send_wechat_notification(webhook_url, success_shares, kw)
        else:
            wechat_result = send_wechat_notification(webhook_url, [], kw, failed=True)

    return {
        "code": 200,
        "message": "批量转存分享操作完成",
        "total_selected": count,
        "save_success_count": success_count,
        "share_success_count": share_success_count,
        "share_results": share_results,
        "wechat_result": wechat_result,
        "preview": preview_only,
        "items": None,
    }


def run(count=None, kw=None, include=None, exclude=None, preview_only=False, force=False,
        selected_items=None, platform_cfg=None):
    """夸克网盘转存分享主流程（统一接口，同步封装）。"""
    config = load_config()
    quark = config["quark"]
    cookie_env = quark.get("cookie", "")
    if count is None:
        count = quark.get("count") or config["count"]
    webhook_url = config.get("wechat", {}).get("webhook_url", "")
    kuake_cli = get_kuake_cli(config)
    pdf_path = get_yinliu_pdf(config)
    folder_fid = quark.get("folder_fid", "0") or "0"
    yinliu_subdir = quark.get("yinliu_subdir", "") or ""

    cookie_file = str(CACHE_DIR / "cookies.txt")

    # 提前校验登录态：无 cookie 且无 cookie 文件时，优雅返回而不是触发浏览器登录
    if not cookie_env:
        login = QuarkLogin(cookie_file=cookie_file)
        if login.check_cookies() is None:
            log_print("未配置夸克 Cookie，且无已保存的登录态，跳过执行", "WARNING")
            return {"code": 400,
                    "message": "未配置夸克 Cookie（请在 config.yaml 填写，"
                               "或运行一次浏览器登录生成 cookies.txt）",
                    "total_selected": count, "save_success_count": 0,
                    "share_success_count": 0, "share_results": [],
                    "wechat_result": None, "preview": preview_only, "items": None}

    return asyncio.run(_batch_save_and_share(
        count=count, webhook_url=webhook_url, kuake_cli=kuake_cli,
        pdf_path=pdf_path, quark_cookie=cookie_env, cookie_file=cookie_file,
        kw=kw, include=include, exclude=exclude, preview_only=preview_only,
        selected_items=selected_items, folder_fid=folder_fid,
        yinliu_subdir=yinliu_subdir, force=force,
    ))