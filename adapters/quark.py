"""夸克网盘适配器。

迁移自 fresh_quark2weixin.py，实现统一适配器接口 run()。
依赖：httpx（转存/分享 API）+ kuake CLI（上传引流 PDF）。
Cookie 通过 .env 的 QUARK_COOKIE 或 cookie 文件提供，无需浏览器。
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
import subprocess
from datetime import datetime
from typing import Union, Dict, Any, List, Tuple

import httpx

from core import cache
from core import pansou_check
from core.config import (load_config, get_kuake_cli, get_yinliu_pdf,
                         get_yinliu_files, resolve_path, CACHE_DIR,
                         SHARE_DIR, LOG_DIR, is_check_links_enabled)
from core.logger import log_print
from core.notifier import send_notifications

# 历史推送记录文件（用于去重）
SHARE_TOTAL_FILE = str(SHARE_DIR / "share_url_total.txt")

# 顺序推送游标文件（记录上次推到资源池的第几条）
CURSOR_FILE = str(SHARE_DIR / "push_cursor.txt")


def _load_cursor() -> int:
    """读取上次顺序推送的游标位置（0-based）。游标文件不存在返回 0。"""
    try:
        if os.path.exists(CURSOR_FILE):
            with open(CURSOR_FILE, "r", encoding="utf-8") as f:
                return max(0, int(f.read().strip() or "0"))
    except Exception:
        pass
    return 0


def _save_cursor(pos: int):
    """保存游标位置（0-based）。"""
    os.makedirs(os.path.dirname(CURSOR_FILE), exist_ok=True)
    try:
        with open(CURSOR_FILE, "w", encoding="utf-8") as f:
            f.write(str(max(0, pos)))
    except Exception as e:
        log_print(f"写入游标失败: {e}", "WARNING")


def _collect_valid_items(pool, start, need, check_enabled, max_scan=None):
    """从游标位置起，顺序收集 need 条「有效」资源。

    预检开启时会跳过 bad 链接继续往后取，直到凑够 need 条或绕完整个池子。
    采用**分批前瞻**：先取一批候选送去检测，够用就停，避免为一个 count=6
    的推送把 267 条全检一遍（那样会白白打满上游请求）。

    Returns:
        (selected, dead_items) —— 选中的条目、检测为失效的条目
    """
    total = len(pool)
    if total == 0 or need <= 0:
        return [], []

    selected = []
    dead_items = []
    scanned = 0
    seen_idx = set()
    limit = total if max_scan is None else min(total, max_scan)

    # 前瞻窗口自适应扩容：资源池里「XX合集」类条目死链率能到 80%+，
    # 固定窗口经常要跑好几轮。这里从 need 的 3 倍起步，每轮不够就翻倍，
    # 既避免小 count 时扫全池，又能在高死链率下快速凑够。
    window = max(need * 3, need + 8)
    window = min(window, limit)

    while len(selected) < need and scanned < limit:
        batch = []
        for i in range(window):
            pos = scanned + i
            if pos >= limit:
                break
            idx = (start + pos) % total
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            batch.append(pool[idx])
        if not batch:
            break

        if check_enabled:
            urls = [(it.get("url") or "").strip() for it in batch]
            states = pansou_check.check_links(urls)
            for it in batch:
                u = (it.get("url") or "").strip()
                st = states.get(u, (pansou_check.STATE_UNCERTAIN, ""))[0]
                if not pansou_check.is_usable(st):
                    dead_items.append(it)
                    continue
                if len(selected) < need:
                    selected.append(it)
        else:
            for it in batch:
                if len(selected) < need:
                    selected.append(it)

        scanned += len(batch)
        window = min(window * 2, limit)   # 本轮不够 → 下一轮翻倍

    return selected, dead_items

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


def _normalize_title(s):
    """去掉空白/标点/转全角，用于模糊匹配。"""
    if not s:
        return ""
    s = re.sub(r"[\s\u3000]+", "", s)
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("：", ":").replace("，", ",").replace("。", ".")
    s = s.replace("！", "!").replace("？", "?").replace("、", ",")
    s = s.replace("【", "[").replace("】", "]")
    return s.lower()


def load_pushed_records():
    """加载历史推送记录，返回 (titles, urls, normalized_titles)。"""
    titles = set()
    urls = set()
    normed = []
    if not os.path.exists(SHARE_TOTAL_FILE):
        return titles, urls, normed
    try:
        with open(SHARE_TOTAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 1 and parts[0].strip():
                    t = parts[0].strip()
                    titles.add(t)
                    n = _normalize_title(t)
                    if len(n) >= 3:
                        normed.append(n)
                if len(parts) >= 2 and parts[1].strip():
                    urls.add(parts[1].strip())
    except Exception as e:
        log_print(f"加载历史推送记录失败: {str(e)}", "WARNING")
    return titles, urls, normed


def _is_pushed(item, pushed_titles, pushed_urls, pushed_normed):
    title = item.get("note", "") or ""
    url = item.get("url", "") or ""
    if url and url in pushed_urls:
        return True
    if title and title in pushed_titles:
        return True
    nt = _normalize_title(title)
    if not nt or len(nt) < 3:
        return False
    for pn in pushed_normed:
        if len(pn) >= 4 and (pn in nt or nt in pn):
            return True
    return False


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
class QuarkLogin:
    """夸克登录态解析。

    不再依赖浏览器（playwright 已移除），改为纯 Cookie 模式：
    优先取 .env 的 QUARK_COOKIE，其次读 cookies/quark_cookie.txt。
    两者都没有时直接报错，提示用户手动配置。
    """

    def __init__(self, cookie_file=None, cookies_env=""):
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
        # 无 Cookie 时不再启动浏览器，直接报错提示用户配置
        log_print(
            "未找到夸克 Cookie。请在 .env 中配置 QUARK_COOKIE，"
            "或在 cookies/quark_cookie.txt 中提供 Cookie 后重试。",
            "ERROR"
        )
        return None


# ---------------------------------------------------------------------------
# 文件管理器
# ---------------------------------------------------------------------------
class QuarkPanFileManager:
    def __init__(self, cookie="", cookie_file=None):
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
        login = QuarkLogin(cookie_file=self.cookie_file)
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
            raise ValueError("获取 stoken 失败（链接可能已失效或分享者被封）")

        is_owner, data_list = await self.get_detail(pwd_id, stoken)
        if not data_list:
            return None

        if is_owner == 1:
            log_print("网盘中已存在该文件，无需再次转存", "INFO")
            return None

        fid_list = [i["fid"] for i in data_list]
        share_fid_token_list = [i["share_fid_token"] for i in data_list]
        task_id_resp = await self.get_share_save_task_id(
            pwd_id, stoken, fid_list, share_fid_token_list, to_pdir_fid=folder_id
        )
        if not isinstance(task_id_resp, str):
            raise ValueError(f"获取转存任务失败: {task_id_resp}")
        return await self.submit_task(task_id_resp)

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
            j = response.json()
            d = j.get("data") or {}
            tid = d.get("task_id")
            if not tid:
                raise ValueError(f"创建转存任务失败: code={j.get('code')} msg={j.get('message')}")
            return tid

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
                                expired_type=1, password=""):
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
                        expired_type=1, password="", traverse_depth=2):
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


_FID_PATH_CACHE = {}


def _resolve_fid_to_path(kuake_cli, folder_fid):
    if not folder_fid or folder_fid == "0":
        return ""
    if folder_fid in _FID_PATH_CACHE:
        return _FID_PATH_CACHE[folder_fid]
    try:
        result = subprocess.run(
            [kuake_cli, "list", "/"], capture_output=True, timeout=15
        )
        text = result.stdout.decode("utf-8", errors="replace")
        data = json.loads(text[text.find("{"):]) if text.find("{") >= 0 else {}
        for item in data.get("data", {}).get("list", []):
            if item.get("fid") == folder_fid:
                path = item.get("path", "")
                _FID_PATH_CACHE[folder_fid] = path
                log_print(f"解析 fid={folder_fid} → path={path}", "DEBUG")
                return path
    except Exception as e:
        log_print(f"解析 folder_fid 失败: {e}", "WARNING")
    _FID_PATH_CACHE[folder_fid] = ""
    return ""


def _upload_yinliu_files(kuake_cli, file_paths, title="", folder_path=""):
    """把引流文件（PDF / 视频等，可多个）上传到本次转存的资源文件夹内。

    Args:
        kuake_cli: kuake CLI 可执行文件路径
        file_paths: 文件路径（单个 str 或 list）
        title: 分享标题（文件夹名）
        folder_path: 转存根目录路径
    """
    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print(f"kuake 可执行文件不存在，跳过引流文件上传: {kuake_cli}", "WARNING")
        return
    if isinstance(file_paths, str):
        file_paths = [file_paths]
    file_paths = [p for p in (file_paths or []) if p]
    if not file_paths:
        return

    if title and folder_path:
        prefix = folder_path.rstrip("/") + "/" + title
    elif title:
        prefix = "/" + title
    else:
        prefix = folder_path or ""

    uploaded = 0
    for path in file_paths:
        if not os.path.exists(path):
            log_print(f"引流文件不存在，跳过上传: {path}", "WARNING")
            continue
        filename = os.path.basename(path)
        cmd = f'{kuake_cli} upload "{path}" "{prefix}/{filename}"'
        log_print(f"执行: {cmd}", "DEBUG")
        try:
            os.system(cmd)
            uploaded += 1
        except Exception as e:
            log_print(f"引流文件上传失败: {filename} - {e}", "WARNING")
    if uploaded:
        log_print(f"引流文件已上传 {uploaded} 个到: {prefix}", "INFO")


# 兼容旧调用名（单文件）
def _upload_yinliu_pdf(kuake_cli, pdf_path, title="", folder_path=""):
    return _upload_yinliu_files(kuake_cli, [pdf_path], title, folder_path)


def _kuake_list(kuake_cli, path):
    """列出夸克网盘某目录内容，返回文件条目 list，失败返回 []。"""
    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print(f"kuake 可执行文件不存在，无法列出目录: {kuake_cli}", "WARNING")
        return []
    try:
        result = subprocess.run(
            [kuake_cli, "list", path], capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        text = result.stdout.strip()
        if not text:
            return []
        obj = json.loads(text[text.find("{"):]) if text.find("{") >= 0 else {}
        if not obj.get("success"):
            log_print(f"kuake list 失败: {obj.get('message')}", "WARNING")
            return []
        return obj.get("data", {}).get("list", [])
    except subprocess.TimeoutExpired:
        log_print(f"kuake list 超时: {path}", "WARNING")
        return []
    except Exception as e:
        log_print(f"kuake list 异常: {e}", "WARNING")
        return []


def _kuake_info(kuake_cli, path):
    """查询夸克网盘某文件/目录信息，返回 entry dict 或 None。"""
    if not kuake_cli or not os.path.exists(kuake_cli):
        return None
    try:
        result = subprocess.run(
            [kuake_cli, "info", path], capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        text = result.stdout.strip()
        if not text:
            return None
        obj = json.loads(text[text.find("{"):]) if text.find("{") >= 0 else {}
        if not obj.get("success"):
            return None
        return obj.get("data")
    except Exception:
        return None


def _kuake_delete(kuake_cli, path):
    """删除夸克网盘某文件/目录，返回 True 表示成功。"""
    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print(f"kuake 可执行文件不存在，无法删除: {kuake_cli}", "WARNING")
        return False
    try:
        result = subprocess.run(
            [kuake_cli, "delete", path], capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        text = result.stdout.strip()
        ok = result.returncode == 0 and ("success" in text.lower() or "成功" in text)
        if not ok:
            log_print(f"kuake delete 失败: {path} | {text[:200]}", "WARNING")
        return ok
    except subprocess.TimeoutExpired:
        log_print(f"kuake delete 超时: {path}", "WARNING")
        return False
    except Exception as e:
        log_print(f"kuake delete 异常: {e}", "WARNING")
        return False


def _kuake_rename(kuake_cli, path, new_name):
    """重命名夸克网盘某文件/目录，返回 True 表示成功。"""
    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print(f"kuake 可执行文件不存在，无法重命名: {kuake_cli}", "WARNING")
        return False
    try:
        result = subprocess.run(
            [kuake_cli, "rename", path, new_name], capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        text = result.stdout.strip()
        ok = result.returncode == 0 and ("success" in text.lower() or "成功" in text)
        if not ok:
            log_print(f"kuake rename 失败: {path} | {text[:200]}", "WARNING")
        return ok
    except subprocess.TimeoutExpired:
        log_print(f"kuake rename 超时: {path}", "WARNING")
        return False
    except Exception as e:
        log_print(f"kuake rename 异常: {e}", "WARNING")
        return False


def _resolve_top_fids_to_paths(kuake_cli, folder_fid, top_fids):
    """把转存返回的 top_fids 列表解析成对应的网盘路径。

    优先用 fid 精确匹配 list 结果，匹配不到再遍历子目录。
    """
    if not top_fids:
        return []

    if not folder_fid or folder_fid == "0":
        root_path = "/"
    else:
        root_path = _resolve_fid_to_path(kuake_cli, folder_fid) or "/"

    root_entries = _kuake_list(kuake_cli, root_path)
    fid_to_entry = {e.get("fid"): e for e in root_entries}

    resolved = []
    unresolved = list(top_fids)

    for fid in unresolved[:]:
        entry = fid_to_entry.get(fid)
        if entry:
            resolved.append(entry["path"])
            unresolved.remove(fid)

    if unresolved and root_path == "/":
        pass

    return resolved


def _collect_all_paths(kuake_cli, start_path):
    """递归收集目录下所有文件/目录路径（含 start_path 自身）。"""
    paths = [start_path]
    for entry in _kuake_list(kuake_cli, start_path):
        child_path = entry.get("path", "")
        if not child_path:
            continue
        if entry.get("dir"):
            paths.extend(_collect_all_paths(kuake_cli, child_path))
        else:
            paths.append(child_path)
    return paths


def _write_clean_record(records, stats):
    """把本次引流清理扫描到的全部文件名写入按日期汇总的记录文件。

    Args:
        records: 明细列表，每项 dict: {"path","size","is_yinliu","deleted","reason"}
        stats: {"checked": int, "deleted": int}

    Returns:
        记录文件路径（写失败则返回 None）
    """
    from datetime import datetime
    from core.config import LOG_DIR

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        now = datetime.now()
        out_path = os.path.join(str(LOG_DIR), f"yinliu_clean_{now:%Y-%m-%d}.txt")
        with open(out_path, "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(f"引流清理记录  {now:%Y-%m-%d %H:%M:%S}\n")
            f.write(f"扫描 {stats.get('checked', 0)} 个文件，"
                    f"删除 {stats.get('deleted', 0)} 个引流文件\n")
            f.write("-" * 70 + "\n")
            for r in records:
                size = r.get("size", 0) or 0
                size_str = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB" \
                    if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f}MB"
                if r.get("deleted"):
                    flag = "[已删除]"
                elif r.get("is_yinliu"):
                    flag = "[引流未删]"
                elif r.get("is_dir"):
                    flag = "[目录]"
                else:
                    flag = "[保留]"
                reason = f"  <{r['reason']}>" if r.get("reason") else ""
                f.write(f"{flag} {r.get('path', '')}  ({size_str}){reason}\n")
            f.write("\n")
        log_print(f"引流清理记录已写入: {out_path}", "INFO")
        return out_path
    except Exception as e:
        log_print(f"写入引流清理记录失败: {e}", "WARNING")
        return None


def clean_yinliu_after_save(kuake_cli, top_paths, filter_cfg, excludes=None):
    """对一组转存顶层路径进行引流文件清理。

    Args:
        kuake_cli: kuake 可执行文件路径
        top_paths: 转存到网盘后顶层条目的路径列表
        filter_cfg: 过滤配置 dict，来自 config["clean_yinliu"]
        excludes: 额外排除关键词列表

    Returns:
        {"deleted": [...], "total": int}
    """
    from core.yinliu_filter import match_yinliu, rewrite_contact

    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print("kuake CLI 不可用，跳过引流清理", "WARNING")
        return {"deleted": [], "renamed": [], "total": 0}

    keywords = filter_cfg.get("keywords") or None
    extensions = filter_cfg.get("extensions") or None
    max_bytes = filter_cfg.get("max_small_file_bytes")
    my_contact = filter_cfg.get("my_contact")

    all_excludes = list(filter_cfg.get("excludes") or [])
    if excludes:
        all_excludes.extend(excludes)

    if not top_paths:
        return {"deleted": [], "renamed": [], "total": 0}

    deleted = []
    renamed = []
    checked = 0
    records = []

    def _excluded(name):
        base = os.path.basename(name)
        for excl in all_excludes:
            if excl and excl in base:
                return True
        return False

    def _handle(path, size, is_dir=False):
        """处理单个条目。

        顺序很关键：
          ① 名字里带别人联系方式 → 改名换成自己的号，**不删**
             （正课文件常挂「【更多好课加微信xxx】」尾巴，删了等于删课）
          ② 其余再按关键词判引流 → 删
        """
        if _excluded(path):
            records.append({"path": path, "size": size, "is_yinliu": False,
                            "deleted": False, "reason": "", "is_dir": is_dir})
            return

        new_name = rewrite_contact(path, my_contact)
        if new_name:
            log_print(f"发现他人联系方式: {os.path.basename(path)}", "INFO")
            if _kuake_rename(kuake_cli, path, new_name):
                renamed.append(path)
                log_print(f"已改名: {os.path.basename(path)} → {new_name}", "SUCCESS")
                records.append({"path": path, "size": size, "is_yinliu": False,
                                "deleted": False, "reason": f"联系方式改写为 {new_name}",
                                "is_dir": is_dir})
                return
            log_print(f"改名失败，继续按引流判定: {path}", "WARNING")

        is_yl, reason = match_yinliu(
            path, size,
            keywords=keywords or None,
            extensions=extensions or None,
            max_small_file_bytes=max_bytes,
            extra_excludes=all_excludes or None,
            aggressive_ext_check=filter_cfg.get("aggressive_ext_check", False),
        )

        if is_yl:
            log_print(f"发现引流文件: {path} | {reason}", "INFO")
            ok = _kuake_delete(kuake_cli, path)
            if ok:
                deleted.append(path)
                log_print(f"已删除: {path}", "SUCCESS")
            else:
                log_print(f"删除失败: {path}", "WARNING")
            records.append({"path": path, "size": size, "is_yinliu": True,
                            "deleted": bool(ok), "reason": reason,
                            "is_dir": False})
        else:
            records.append({"path": path, "size": size, "is_yinliu": False,
                            "deleted": False, "reason": "", "is_dir": is_dir})

    for top_path in top_paths:
        info = _kuake_info(kuake_cli, top_path)
        is_dir = info.get("dir", True) if info else True

        if not is_dir and info:
            checked += 1
            _handle(top_path, info.get("size", 0), is_dir=False)
            continue

        queue = [top_path]
        while queue:
            cur = queue.pop(0)
            entries = _kuake_list(kuake_cli, cur)
            for entry in entries:
                checked += 1
                path = entry.get("path", "")
                size = entry.get("size", 0)
                entry_is_dir = bool(entry.get("dir"))
                _handle(path, size, is_dir=entry_is_dir)
                # 改名/未删的目录继续下钻；已删的不用再进
                if entry_is_dir and path not in deleted:
                    queue.append(path)

    log_print(f"引流清理完成：检查 {checked} 个，删除 {len(deleted)} 个，"
              f"改写联系方式 {len(renamed)} 个", "INFO")
    _write_clean_record(records, {"checked": checked, "deleted": len(deleted),
                                  "renamed": len(renamed)})
    return {"deleted": deleted, "renamed": renamed, "total": checked}


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
async def _batch_save_and_share(count, webhook_url, kuake_cli, pdf_path,
                                quark_cookie, kw=None,
                                include=None, exclude=None, preview_only=False,
                                selected_items=None, folder_fid="0",
                                force=False, filter_cfg=None,
                                check_links_arg=None):
    # 引流文件列表：pdf_path 传 str（兼容旧调用）或 list
    yinliu_files = [pdf_path] if isinstance(pdf_path, str) else list(pdf_path or [])
    manager = QuarkPanFileManager(cookie=quark_cookie)

    folder_path = _resolve_fid_to_path(kuake_cli, folder_fid)

    if selected_items is not None:
        quark_data = selected_items
    else:
        quark_data = cache.fetch_api_data("quark", kw or "",
                                          expire_hours=24,
                                          force_refresh=force,
                                          include=include,
                                          exclude=exclude or ["可搜索"])
    if not quark_data:
        log_print("没有可用的资源（API 无数据或已全部过滤）", "WARNING")
        return {"code": 404, "message": "API 无匹配资源，请换关键词或用 --include 本地过滤",
                "total_selected": 0, "save_success_count": 0,
                "share_success_count": 0, "share_results": [],
                "preview": preview_only, "items": None}

    pushed_titles, pushed_urls, pushed_normed = load_pushed_records()
    if pushed_titles or pushed_urls or pushed_normed:
        before = len(quark_data)
        quark_data = [i for i in quark_data
                      if not _is_pushed(i, pushed_titles, pushed_urls, pushed_normed)]
        log_print(f"过滤已推送资源 {before - len(quark_data)} 个，剩余 {len(quark_data)} 个", "INFO")

    # ── 指定条目模式（--selected）：直接对这些条目预检，不走游标 ──────────
    if selected_items is not None:
        selected = [it for it in quark_data if (it.get("url") or "").strip()]
        dead_hits = []
        if is_check_links_enabled() and check_links_arg is not False and selected:
            log_print(f"对指定的 {len(selected)} 条做有效性预检...", "INFO")
            urls = [(it.get("url") or "").strip() for it in selected]
            states = pansou_check.check_links(urls)
            alive, dead_hits = [], []
            for it in selected:
                u = (it.get("url") or "").strip()
                st = states.get(u, (pansou_check.STATE_UNCERTAIN, ""))[0]
                (alive if pansou_check.is_usable(st) else dead_hits).append(it)
            if dead_hits:
                for it in dead_hits:
                    st, summary = states.get((it.get("url") or "").strip(),
                                             (pansou_check.STATE_BAD, ""))
                    log_print(f"  跳过失效链接：{it.get('note', '')[:40]}（{summary}）", "WARNING")
                pansou_check.record_dead_links(dead_hits)
            if not alive:
                log_print("指定的条目经预检后全部失效", "ERROR")
                return {"code": 404, "message": "指定的条目经预检后全部失效",
                        "total_selected": 0, "save_success_count": 0,
                        "share_success_count": 0, "share_results": [],
                        "preview": preview_only, "items": None,
                        "dead_count": len(dead_hits)}
            selected = alive
        count = len(selected)

    else:
        if len(quark_data) < count:
            log_print(f"链接数量不足，调整选取数量为 {len(quark_data)}", "WARNING")
            count = len(quark_data)

        # ── 顺序游标推进 + 有效性预检（死链跳过、顺延取满）──────────────
        # 先剔除历史死链，避免每次都撞同一条
        known_dead = pansou_check.load_dead_links()
        if known_dead:
            before = len(quark_data)
            quark_data = pansou_check.filter_dead(quark_data, known_dead)
            if before != len(quark_data):
                log_print(f"按历史死链记录剔除 {before - len(quark_data)} 条，"
                          f"剩余 {len(quark_data)} 条", "INFO")

        cursor = _load_cursor()
        total = len(quark_data)

        if total == 0:
            log_print("资源池为空（可能全部为死链）", "ERROR")
            return {"code": 404, "message": "资源池为空", "total_selected": 0,
                    "save_success_count": 0, "share_success_count": 0,
                    "share_results": [], "preview": preview_only, "items": None}

        if cursor >= total:
            log_print(f"游标 {cursor} 超出可用范围（共 {total} 条），重置为 0", "INFO")
            cursor = 0

        check_enabled = is_check_links_enabled() and check_links_arg is not False
        log_print(f"顺序推送：游标从第 {cursor + 1} 条开始，取 {count} 条（池内共 {total} 条）"
                  f"{'，已启用有效性预检' if check_enabled else '，未启用有效性预检'}", "INFO")

        selected, dead_hits = _collect_valid_items(
            quark_data, cursor, count, check_enabled)
        consumed = len(selected) + len(dead_hits)

        if not selected:
            log_print("预检后没有可用资源（全部为死链）", "ERROR")
            return {"code": 404, "message": "预检后无可用资源，资源池可能已大面积失效",
                    "total_selected": 0, "save_success_count": 0,
                    "share_success_count": 0, "share_results": [],
                    "preview": preview_only, "items": None}

        if len(selected) < count:
            log_print(f"预检后仅凑够 {len(selected)} 条可用资源（目标 {count} 条），按实际数量推送",
                      "WARNING")

        # 死链落盘，下次加载直接排除
        if dead_hits:
            pansou_check.record_dead_links(dead_hits)

        # 游标只推进到「实际消费过」的位置，死链也算消费（已记录，不会重复推）
        next_cursor = (cursor + consumed) % total
        log_print(f"游标已更新：{cursor} → {next_cursor}（消费 {consumed} 条，"
                  f"其中死链 {len(dead_hits)} 条；下次从第 {next_cursor + 1} 条开始）", "INFO")

        # 预览模式不落游标：预览只是看看，不该吃掉正式名额
        if preview_only:
            log_print("预览模式：游标未变更", "INFO")
        else:
            _save_cursor(next_cursor)

    if preview_only:
        items = [{"index": i + 1, "note": it.get("note", "无标题"),
                  "url": it["url"]} for i, it in enumerate(selected)]
        return {"code": 200, "message": "预览成功", "preview": True,
                "items": items, "total_selected": len(selected),
                "total_found": len(quark_data), "save_success_count": 0,
                "share_success_count": 0, "share_results": [],
                "dead_count": len(dead_hits)}

    save_results = []
    os.makedirs(SHARE_DIR, exist_ok=True)
    for index, item in enumerate(selected):
        url = item["url"]
        note = item.get("note", "")

        with open(SHARE_TOTAL_FILE, "a", encoding="utf-8") as f:
            f.write(f"{note}  |  {url}  |  PICKED\n")

        try:
            result = await manager.run(url.strip(), folder_fid)
            if result is None:
                raise ValueError("转存结果为空（可能已存在或未返回任务）")
            d = result.get("data") if isinstance(result, dict) else None
            save_as = (d or {}).get("save_as") if d else None
            file_ids = (save_as or {}).get("save_as_top_fids") if save_as else None
            if not file_ids:
                raise ValueError(f"转存返回异常结构: {json.dumps(result, ensure_ascii=False)[:200]}")
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

    if filter_cfg and filter_cfg.get("enabled") and success_count > 0:
        log_print("开始清理转存内容中的引流文件...", "INFO")
        all_top_paths = []
        for r in save_results:
            if r["status"] == "success" and r.get("file_id"):
                paths = _resolve_top_fids_to_paths(kuake_cli, folder_fid, r["file_id"])
                all_top_paths.extend(paths)
        try:
            # 排除自己的引流文件（PDF / 视频等），避免被清理规则误删
            cleanup_excludes = [os.path.splitext(os.path.basename(p))[0]
                                for p in yinliu_files if p]
            cleanup_excludes = cleanup_excludes or None
            cleanup_result = clean_yinliu_after_save(
                kuake_cli, all_top_paths, filter_cfg, excludes=cleanup_excludes,
            )
            log_print(f"引流清理完成: 删除 {len(cleanup_result['deleted'])} 个",
                      "SUCCESS" if cleanup_result["deleted"] else "INFO")
        except Exception as e:
            log_print(f"引流清理异常: {e}", "WARNING")

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
                                             url_type=1, expired_type=1,
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

            # 上传引流文件（PDF / 视频等）
            _upload_yinliu_files(kuake_cli, yinliu_files, title, folder_path)

            with open(SHARE_TOTAL_FILE, "a", encoding="utf-8") as f:
                f.write(f"{title}  |  {share_url}  |  DONE\n")
        except Exception as e:
            share_results.append({"index": save_result.get("index",
                                                          len(share_results) + 1),
                                  "note": save_result.get("note", ""),
                                  "url": save_result.get("url", "unknown"),
                                  "status": "error",
                                  "message": f"生成分享链接失败: {e}"})

    wechat_result = None
    feishu_bot_result = None
    # 企业微信 + 飞书群机器人并行推送（企微地址由调用方传入，飞书从 config 取）
    _cfg = {}
    try:
        _cfg = load_config()
    except Exception:
        pass
    _cfg.setdefault("wechat", {})
    if webhook_url:
        _cfg["wechat"]["webhook_url"] = webhook_url
    if _cfg["wechat"].get("webhook_url") or (_cfg.get("feishu", {}) or {}).get("webhook_url"):
        success_shares = [r for r in share_results
                          if isinstance(r, dict) and r.get("share_url")]
        if success_shares:
            _res = send_notifications(_cfg, success_shares, kw)
        else:
            _res = send_notifications(_cfg, [], kw, failed=True)
        wechat_result = _res.get("wechat")
        feishu_bot_result = _res.get("feishu")

    feishu_result = None
    try:
        config = load_config()
    except Exception:
        config = {}
    fs_cfg = config.get("feishu", {})
    if fs_cfg.get("enabled") and share_success_count > 0:
        try:
            from core.feishu_client import batch_write_resources
            feishu_items = []
            for r in share_results:
                if isinstance(r, dict) and r.get("share_url"):
                    feishu_items.append({
                        "name": r.get("title") or r.get("note", ""),
                        "url": r["share_url"],
                        "size": r.get("size", ""),
                        "tag": "夸克",
                        "search": r.get("title") or r.get("note", ""),
                        "update_time": time.strftime("%Y-%m-%d"),
                    })
            if feishu_items:
                feishu_result = batch_write_resources(config, feishu_items)
        except Exception as e:
            log_print(f"飞书写入异常: {e}", "ERROR")

    return {
        "code": 200,
        "message": "批量转存分享操作完成",
        "total_selected": len(selected),
        "save_success_count": success_count,
        "share_success_count": share_success_count,
        "share_results": share_results,
        "wechat_result": wechat_result,
        "feishu_bot_result": feishu_bot_result,
        "feishu_result": feishu_result,
        "preview": preview_only,
        "items": None,
        "dead_count": len(dead_hits),
    }


def run(count=None, kw=None, include=None, exclude=None, preview_only=False, force=False,
        selected_items=None, platform_cfg=None, check_links=None):
    """夸克网盘转存分享主流程（统一接口，同步封装）。

    Args:
        check_links: 转存前是否用 PanSou 预检链接有效性。
            None=跟随 .env 的 PANSOU_CHECK_ENABLED（默认开启）；
            True=强制开启；False=强制关闭（CLI 的 --no-check-links）。
    """
    config = load_config()
    quark = config["quark"]
    quark_cookie = quark.get("cookie", "")
    if count is None:
        count = quark.get("count") or config["count"]
    webhook_url = config.get("wechat", {}).get("webhook_url", "")
    kuake_cli = get_kuake_cli(config)
    pdf_path = get_yinliu_files(config)
    folder_fid = quark.get("folder_fid", "0") or "0"

    if not quark_cookie:
        log_print("未配置夸克 Cookie，跳过执行", "WARNING")
        return {"code": 400,
                "message": "未配置夸克 Cookie（请在 .env 填写 QUARK_COOKIE）",
                "total_selected": count, "save_success_count": 0,
                "share_success_count": 0, "share_results": [],
                "wechat_result": None, "preview": preview_only, "items": None}

    filter_cfg = config.get("clean_yinliu", {})
    if filter_cfg and filter_cfg.get("enabled"):
        log_print("已开启转存后引流文件自动清理", "INFO")

    return asyncio.run(_batch_save_and_share(
        count=count, webhook_url=webhook_url, kuake_cli=kuake_cli,
        pdf_path=pdf_path, quark_cookie=quark_cookie,
        kw=kw, include=include, exclude=exclude, preview_only=preview_only,
        selected_items=selected_items, folder_fid=folder_fid,
        force=force, filter_cfg=filter_cfg,
        check_links_arg=check_links,
    ))