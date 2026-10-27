"""迅雷网盘适配器。

从 xunlei_pan.py 重构而来，暴露统一接口 run()：
{
    "code": 200 / 500 / ...,
    "message": str,
    "total_selected": int,
    "save_success_count": int,
    "share_success_count": int,
    "share_results": [{"note": ..., "share_url": ..., "pass_code": ...}, ...],
    "wechat_result": {...} | None,
    "preview": bool,
    "items": list | None,
}

依赖：requests（项目 requirements.txt 已包含）。
配置：从 core.config.load_config() 读 xunlei 段；refresh_token 优先 .env 的
XUNLEI_REFRESH_TOKEN，其次 data/xunlei_token.json，最后回退到默认路径。
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from core import cache
from core.config import load_config, DATA_DIR, SHARE_DIR
from core.logger import log_print

# ==================== 常量 ====================

API_BASE = "https://api-pan.xunlei.com"
AUTH_BASE = "https://xluser-ssl.xunlei.com"

CLIENT_ID = "Xqp0kJBXWhwaTpB6"

XUNLEI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

XUNLEI_HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": "https://pan.xunlei.com",
    "Referer": "https://pan.xunlei.com/",
    "User-Agent": XUNLEI_UA,
    "x-client-id": CLIENT_ID,
}

# 固定设备 ID 和 captcha 签名（三者配套，不可单独修改）
DEVICE_ID = "925b7631473a13716b791d7f28289cad"
CAPTCHA_SIGN = "1.fe2108ad808a74c9ac0243309242726c"
CAPTCHA_TIMESTAMP = "1645241033384"

FILE_LIST_FILTERS = json.dumps({
    "phase": {"eq": "PHASE_TYPE_COMPLETE"},
    "trashed": {"eq": False},
})

ERROR_CODES = {
    "ALREADY_EXISTED": "文件已存在",
    "FORBIDDEN": "无权限操作",
    "NOT_FOUND": "资源不存在",
    "INVALID_ARGUMENT": "参数错误",
    "UNAUTHENTICATED": "认证失败，请重新登录",
    "PERMISSION_DENIED": "权限不足",
    "RESOURCE_EXHAUSTED": "配额已用尽",
    "SENSITIVE_RESOURCE": "该分享内容可能涉及违规信息，无法访问",
    "WRONG_PASS_CODE": "提取码错误",
}


# ==================== 异常 ====================

class XunleiError(Exception):
    """业务异常（提示信息可直接展示给用户）"""


# ==================== 工具函数 ====================

def fmt_size(size: Any) -> str:
    try:
        n = float(size or 0)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return "-"


def fmt_time(ts_ms: Any) -> str:
    try:
        ts = int(ts_ms or 0)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")


def _get_default_token_file() -> Path:
    """默认 token 文件：data/xunlei_token.json（项目根相对）。"""
    return DATA_DIR / "xunlei_token.json"


# ==================== token 持久化 ====================

class TokenStore:
    """refresh_token 会随刷新轮换，这里负责落盘，避免下次失效。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else _get_default_token_file()

    def load(self) -> str:
        if not self.path.exists():
            return ""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return str(data.get("refresh_token") or "").strip()
        except Exception:
            return ""

    def save(self, refresh_token: str) -> None:
        if not self.path or not refresh_token:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"refresh_token": refresh_token, "updated_at": int(time.time())}
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log_print(f"[xunlei] token 保存失败: {exc}", "WARNING")


# ==================== 迅雷网盘客户端 ====================

class XunleiClient:
    """迅雷网盘 API 客户端。"""

    def __init__(self, refresh_token: str, token_store: Optional[TokenStore] = None):
        refresh_token = str(refresh_token or "").strip()
        if not refresh_token:
            raise XunleiError(
                "缺少 refresh_token。请在 .env 配置 XUNLEI_REFRESH_TOKEN，"
                "或把它写入 data/xunlei_token.json。"
            )

        self._rth = requests.Session()
        self._session = requests.Session()
        self._session.headers.update(XUNLEI_HEADERS)

        self._refresh_token = refresh_token
        self._token_store = token_store or TokenStore()

        self._access_token = ""
        self._access_token_expire = 0.0
        self._captcha_token = ""
        self._captcha_token_expire = 0.0

        self._device_id = DEVICE_ID
        self._user_id = ""
        self._user_name = ""
        self.nickname = ""

        self._token_lock = threading.Lock()
        self._rate_limit_min_interval = 0.05
        self._rate_limit_max_interval = 0.10
        self._last_request_at = 0.0

    # ---------- token ----------

    def _refresh_access_token(self) -> bool:
        url = f"{AUTH_BASE}/v1/auth/token"
        data = {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        headers = {"Content-Type": "application/json", "User-Agent": XUNLEI_UA}

        try:
            resp = self._rth.post(url, json=data, headers=headers, timeout=30)
            result = resp.json()
        except Exception as exc:
            raise XunleiError(f"刷新 access_token 请求失败: {exc}") from exc

        if "access_token" not in result:
            error = result.get("error_description") or result.get("error") or "未知错误"
            raise XunleiError(f"刷新 access_token 失败: {error}（refresh_token 可能已失效）")

        self._access_token = result["access_token"]
        self._access_token_expire = time.time() + int(result.get("expires_in", 7200)) - 120

        new_refresh = str(result.get("refresh_token") or "")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._token_store:
                self._token_store.save(new_refresh)

        self._user_id = str(result.get("user_id") or "")
        self._user_name = str(result.get("user_name") or "")

        self._session.headers["Authorization"] = f"Bearer {self._access_token}"
        self._session.headers["x-device-id"] = self._device_id
        return True

    def _refresh_captcha_token(self) -> bool:
        url = f"{AUTH_BASE}/v1/shield/captcha/init"
        data = {
            "client_id": CLIENT_ID,
            "action": "get:/drive/v1/share",
            "device_id": self._device_id,
            "meta": {
                "username": "",
                "phone_number": "",
                "email": "",
                "package_name": "pan.xunlei.com",
                "client_version": "1.45.0",
                "captcha_sign": CAPTCHA_SIGN,
                "timestamp": CAPTCHA_TIMESTAMP,
                "user_id": self._user_id or "0",
            },
        }
        headers = {"Content-Type": "application/json", "User-Agent": XUNLEI_UA}

        try:
            resp = self._rth.post(url, json=data, headers=headers, timeout=30)
            result = resp.json()
        except Exception as exc:
            log_print(f"[xunlei] captcha_token 请求失败: {exc}", "WARNING")
            return False

        if "captcha_token" not in result:
            log_print(
                f"[xunlei] captcha_token 失败: "
                f"{result.get('error_description') or result.get('error') or '未知错误'}",
                "WARNING",
            )
            return False

        self._captcha_token = result["captcha_token"]
        self._captcha_token_expire = time.time() + int(result.get("expires_in", 300)) - 10
        self._session.headers["x-captcha-token"] = self._captcha_token
        return True

    def ensure_tokens(self) -> None:
        if self._access_token and time.time() < self._access_token_expire:
            if self._captcha_token and time.time() < self._captcha_token_expire:
                return
        with self._token_lock:
            if not (self._access_token and time.time() < self._access_token_expire):
                self._refresh_access_token()
            if not (self._captcha_token and time.time() < self._captcha_token_expire):
                self._refresh_captcha_token()

    # ---------- HTTP ----------

    def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request_at > 0:
            interval = random.uniform(self._rate_limit_min_interval, self._rate_limit_max_interval)
            elapsed = now - self._last_request_at
            if elapsed < interval:
                time.sleep(interval - elapsed)
                now = time.monotonic()
        self._last_request_at = now

    def _request(
        self, method: str, url: str, body: Optional[Dict] = None, params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        self.ensure_tokens()
        self._throttle()
        try:
            if method.upper() == "GET":
                resp = self._session.get(url, params=params, timeout=30)
            elif method.upper() == "PATCH":
                resp = self._session.patch(url, json=body, params=params, timeout=30)
            else:
                resp = self._session.post(url, json=body, params=params, timeout=30)
            return resp.json()
        except Exception as exc:
            raise XunleiError(f"HTTP 请求失败: {exc}") from exc

    @staticmethod
    def _has_error(result: Any) -> bool:
        return isinstance(result, dict) and ("error" in result or "error_code" in result)

    @staticmethod
    def _error_message(result: Dict[str, Any]) -> str:
        if "error_description" in result:
            return str(result["error_description"])
        if "error" in result:
            code = str(result["error"])
            return ERROR_CODES.get(code, code)
        if "share_status_text" in result:
            return str(result["share_status_text"])
        return "未知错误"

    def _check(self, result: Any) -> Dict[str, Any]:
        if not isinstance(result, dict):
            raise XunleiError("接口返回格式异常")
        if self._has_error(result):
            raise XunleiError(self._error_message(result))
        return result

    # ---------- 账号 ----------

    def info(self) -> Dict[str, Any]:
        self.ensure_tokens()
        user_info = self._safe_call(f"{AUTH_BASE}/v1/user/me")
        about_info = self._safe_call(f"{API_BASE}/drive/v1/about")

        nickname = user_info.get("name") or self._user_name or f"迅雷用户{self._user_id}"
        self.nickname = str(nickname)
        username = (
            user_info.get("phone_number")
            or user_info.get("sub")
            or user_info.get("id")
            or self._user_id
        )

        quota = about_info.get("quota") if isinstance(about_info.get("quota"), dict) else {}
        used_space = quota.get("usage")
        total_space = quota.get("limit")

        member_type = ""
        vip_info = user_info.get("vip_info")
        if isinstance(vip_info, list):
            for it in vip_info:
                if isinstance(it, dict) and str(it.get("is_vip") or "") == "1":
                    member_type = "VIP"
                    break

        return {
            "user_id": self._user_id,
            "nickname": nickname,
            "username": username,
            "used_space": used_space,
            "total_space": total_space,
            "member_type": member_type,
        }

    def _safe_call(self, url: str) -> Dict[str, Any]:
        result = self._request("GET", url)
        return result if isinstance(result, dict) and not self._has_error(result) else {}

    # ---------- 文件 / 目录 ----------

    @staticmethod
    def _convert_item(item: Dict[str, Any]) -> Dict[str, Any]:
        is_folder = item.get("kind") == "drive#folder"
        updated_at = 0
        time_str = item.get("modified_time") or item.get("created_time", "")
        if time_str:
            try:
                dt = datetime.fromisoformat(str(time_str).replace("Z", "+00:00"))
                updated_at = int(dt.timestamp() * 1000)
            except Exception:
                pass
        return {
            "fid": item.get("id", ""),
            "file_name": item.get("name", ""),
            "dir": is_folder,
            "size": int(item.get("size", 0) or 0),
            "updated_at": updated_at,
        }

    def ls(self, dir_fid: str = "") -> List[Dict[str, Any]]:
        file_list: List[Dict[str, Any]] = []
        page_token = ""
        is_root = not dir_fid or str(dir_fid) in ("0", "")

        while True:
            params: Dict[str, Any] = {
                "filters": FILE_LIST_FILTERS,
                "with_audit": "true",
                "thumbnail_size": "SIZE_SMALL",
                "limit": 100,
            }
            if not is_root:
                params["parent_id"] = str(dir_fid)
            if page_token:
                params["page_token"] = page_token

            result = self._check(
                self._request("GET", f"{API_BASE}/drive/v1/files", params=params)
            )
            for item in result.get("files", []):
                file_list.append(self._convert_item(item))
            page_token = result.get("next_page_token", "")
            if not page_token:
                break

        return file_list

    def _find_child(self, parent_id: str, name: str) -> Optional[Dict[str, Any]]:
        page_token = ""
        while True:
            params: Dict[str, Any] = {"filters": FILE_LIST_FILTERS, "limit": 100}
            if parent_id:
                params["parent_id"] = str(parent_id)
            if page_token:
                params["page_token"] = page_token

            result = self._check(
                self._request("GET", f"{API_BASE}/drive/v1/files", params=params)
            )
            for item in result.get("files", []):
                if item.get("name") == name:
                    return item
            page_token = result.get("next_page_token", "")
            if not page_token:
                return None

    def resolve_fid(self, path: str, *, create: bool = False) -> str:
        parts = [p for p in str(path or "").strip("/").split("/") if p]
        if not parts:
            return ""

        fid = ""
        for i, name in enumerate(parts):
            item = self._find_child(fid, name)
            if item:
                fid = str(item.get("id") or "")
                continue

            if not create:
                raise XunleiError(f"路径不存在: {path}")

            result = self._check(
                self._request(
                    "POST",
                    f"{API_BASE}/drive/v1/files",
                    body={"kind": "drive#folder", "parent_id": fid, "name": name},
                )
            )
            fid = str(
                (result.get("file") or {}).get("id") or result.get("id") or ""
            )
            log_print(f"[xunlei] 已创建目录 {'/' + '/'.join(parts[:i + 1])}", "INFO")

        return fid

    def mkdir(self, path: str) -> str:
        return self.resolve_fid(path, create=True)

    # ---------- 分享 ----------

    @staticmethod
    def parse_share_url(url: str) -> Tuple[str, str, str]:
        url = str(url or "").strip()
        match = re.search(r"pan\.xunlei\.com/s/([a-zA-Z0-9_-]+)", url)
        if not match:
            raise XunleiError(f"无法识别的迅雷分享链接: {url}")

        share_id = match.group(1)
        if "?" in share_id:
            share_id = share_id.split("?")[0]
        if "#" in share_id:
            share_id = share_id.split("#")[0]

        passcode = ""
        match_pwd = re.search(r"(?:pwd|password)=([a-zA-Z0-9]+)", url)
        if match_pwd:
            passcode = match_pwd.group(1)

        pdir_fid = ""
        if "#/list/share/" in url:
            raw_fid = url.split("#/list/share/")[-1]
            match_fid = re.match(r"(\w+)", raw_fid)
            if match_fid:
                pdir_fid = match_fid.group(1)

        return share_id, passcode, pdir_fid

    def get_stoken(self, share_id: str, passcode: str = "") -> Tuple[str, Dict[str, Any]]:
        params = {
            "share_id": share_id,
            "pass_code": passcode,
            "limit": 100,
            "pass_code_token": "",
            "page_token": "",
            "thumbnail_size": "SIZE_SMALL",
        }
        result = self._request("GET", f"{API_BASE}/drive/v1/share", params=params)
        if not isinstance(result, dict):
            raise XunleiError("分享信息返回格式异常")
        if self._has_error(result):
            raise XunleiError(self._error_message(result))

        share_status = str(result.get("share_status") or "")
        if share_status and share_status != "OK":
            text = (
                result.get("share_status_text")
                or ERROR_CODES.get(share_status)
                or "分享已失效"
            )
            raise XunleiError(str(text))

        return str(result.get("pass_code_token") or ""), result

    def list_share(
        self,
        share_id: str,
        stoken: str,
        parent_id: str = "",
        recursive: bool = False,
        depth: int = 0,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page_token = ""

        while True:
            params: Dict[str, Any] = {
                "share_id": share_id,
                "pass_code_token": stoken,
                "limit": 100,
                "page_token": page_token,
                "thumbnail_size": "SIZE_SMALL",
            }
            if parent_id and str(parent_id) != "0":
                params["parent_id"] = parent_id
                url = f"{API_BASE}/drive/v1/share/detail"
            else:
                url = f"{API_BASE}/drive/v1/share"

            result = self._check(self._request("GET", url, params=params))
            for item in result.get("files", []):
                items.append(self._convert_item(item))
            page_token = result.get("next_page_token", "")
            if not page_token:
                break

        if recursive and depth < 5:
            for it in list(items):
                if it["dir"]:
                    children = self.list_share(
                        share_id, stoken, it["fid"], recursive=True, depth=depth + 1
                    )
                    items.extend(children)

        return items

    def create_share(
        self,
        paths: Optional[List[str]] = None,
        fids: Optional[List[str]] = None,
        expire_days: str = "-1",
        restore_limit: int = -1,
        title: str = "",
    ) -> Dict[str, Any]:
        """创建分享。paths 和 fids 二选一（fids 优先）。"""
        if fids:
            fids = [str(f).strip() for f in fids if str(f).strip()]
            if not fids:
                raise XunleiError("fids 为空")
            names: List[str] = []
        elif paths:
            fids = []
            names = []
            for path in paths:
                fid = self.resolve_fid(path)
                if not fid:
                    raise XunleiError("不能分享根目录")
                fids.append(fid)
                names.append(os.path.basename(str(path).rstrip("/")) or str(path))
        else:
            raise XunleiError("create_share 需要 paths 或 fids")

        body = {
            "file_ids": fids,
            "title": title or (names[0] if len(names) == 1 else f"{names[0]}等{len(names)}项"),
            "share_to": "copy",
            "expiration_days": str(expire_days),
            "restore_limit": restore_limit,
        }
        result = self._check(self._request("POST", f"{API_BASE}/drive/v1/share", body=body))

        share_id = str(result.get("share_id") or "")
        if not share_id:
            raise XunleiError("创建分享失败：接口未返回 share_id")

        return {
            "share_id": share_id,
            "share_url": result.get("share_url") or f"https://pan.xunlei.com/s/{share_id}",
            "pass_code": result.get("pass_code") or "",
            "share_text": result.get("share_text") or "",
        }

    # ---------- 转存 ----------

    def _save_files(
        self, file_ids: List[str], to_pdir_fid: str, share_id: str, stoken: str
    ) -> str:
        body = {
            "parent_id": to_pdir_fid if to_pdir_fid and str(to_pdir_fid) != "0" else "",
            "share_id": share_id,
            "pass_code_token": stoken,
            "file_ids": file_ids,
            "ancestor_ids": [],
            "specify_parent_id": True,
        }
        result = self._check(
            self._request("POST", f"{API_BASE}/drive/v1/share/restore", body=body)
        )
        task_id = str(result.get("restore_task_id") or "")
        return task_id or f"xunlei_sync_{share_id}"

    def wait_task(self, task_id: str, timeout_seconds: int = 180) -> Dict[str, Any]:
        if task_id.startswith("xunlei_sync_"):
            return {"ok": True, "message": "转存已同步完成"}

        deadline = time.time() + timeout_seconds
        last_phase = ""

        while time.time() < deadline:
            result = self._request("GET", f"{API_BASE}/drive/v1/tasks/{task_id}")
            if self._has_error(result):
                raise XunleiError(self._error_message(result))

            progress = result.get("progress", 0)
            phase = str(result.get("phase") or "")

            if phase != last_phase:
                last_phase = phase
                log_print(f"[xunlei task] {phase or '进行中'} progress={progress}", "INFO")

            if progress == 100 or phase == "PHASE_TYPE_COMPLETE":
                save_as_fids: List[str] = []
                params = result.get("params") or {}
                trace_ids = params.get("trace_file_ids")
                if trace_ids:
                    try:
                        trace_data = json.loads(trace_ids)
                        if isinstance(trace_data, dict):
                            save_as_fids = list(trace_data.values())
                        elif isinstance(trace_data, list):
                            save_as_fids = trace_data
                    except Exception:
                        pass
                return {
                    "ok": True,
                    "message": result.get("name") or "转存完成",
                    "save_as_fids": save_as_fids,
                }

            if phase == "PHASE_TYPE_ERROR":
                raise XunleiError(result.get("message") or "转存任务失败")

            time.sleep(0.5)

        raise XunleiError("转存任务查询超时")

    def transfer(
        self,
        url: str,
        target_dir: str = "/",
        passcode: str = "",
        only: Optional[List[str]] = None,
        list_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        share_id, url_passcode, sub_fid = self.parse_share_url(url)
        passcode = passcode or url_passcode

        stoken, share_info = self.get_stoken(share_id, passcode)
        share_name = share_info.get("share_name") or share_info.get("name") or share_id
        log_print(f"[xunlei share] {share_name}  (share_id={share_id})", "INFO")

        if sub_fid:
            target_item = None
            for it in self.list_share(share_id, stoken, ""):
                if it["fid"] == sub_fid:
                    target_item = it
                    break
            selected = [
                target_item or {"fid": sub_fid, "file_name": sub_fid, "dir": True, "size": 0}
            ]
        else:
            selected = self.list_share(share_id, stoken, "")

        if not selected:
            raise XunleiError("分享内容为空")

        if only:
            keywords = [k.strip() for k in only]
            filtered = [it for it in selected if any(k in it["file_name"] for k in keywords)]
            if not filtered:
                raise XunleiError(f"没有匹配到指定文件: {', '.join(keywords)}")
            selected = filtered

        if list_only:
            return None

        to_fid = self.mkdir(target_dir) if target_dir and target_dir.strip("/") else ""
        log_print(f"[xunlei save] 目标目录: {target_dir or '/'}", "INFO")

        task_id = self._save_files([it["fid"] for it in selected], to_fid, share_id, stoken)
        log_print(f"[xunlei save] 已提交转存任务: {task_id}", "INFO")
        result = self.wait_task(task_id)
        log_print(f"[xunlei save] {result.get('message')}，共 {len(selected)} 项", "SUCCESS")

        return {
            "share_name": share_name,
            "target_dir": target_dir or "/",
            "count": len(selected),
            "message": result.get("message") or "",
            "save_as_fids": result.get("save_as_fids", []),
        }


# ==================== 客户端工厂 ====================

def build_client(xunlei_cfg: Optional[Dict[str, Any]] = None) -> XunleiClient:
    """构造 XunleiClient。

    refresh_token 优先级：.env XUNLEI_REFRESH_TOKEN → data/xunlei_token.json。
    """
    store = TokenStore()
    token = ""
    if xunlei_cfg:
        token = (xunlei_cfg.get("refresh_token") or "").strip()
    if not token:
        token = os.getenv("XUNLEI_REFRESH_TOKEN", "").strip()
    if not token:
        token = store.load()
    return XunleiClient(token, token_store=store)


# ==================== 统一适配器接口 ====================

SHARE_TOTAL_FILE = str(SHARE_DIR / "share_url_total.txt")


def _write_history(note: str, url: str, status: str) -> None:
    os.makedirs(os.path.dirname(SHARE_TOTAL_FILE), exist_ok=True)
    try:
        with open(SHARE_TOTAL_FILE, "a", encoding="utf-8") as f:
            f.write(f"{note}  |  {url}  |  {status}\n")
    except Exception as exc:
        log_print(f"[xunlei] 写入历史失败: {exc}", "WARNING")


def _is_xunlei_url(url: str) -> bool:
    return bool(url) and "pan.xunlei.com" in url


def _collect_xunlei_items(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从资源池里挑迅雷链接。"""
    out = []
    for it in pool:
        u = (it.get("url") or "").strip()
        if _is_xunlei_url(u):
            out.append(it)
    return out


def run(
    count=None,
    kw=None,
    include=None,
    exclude=None,
    preview_only=False,
    force=False,
    selected_items=None,
    platform_cfg=None,
    check_links=None,
):
    """迅雷网盘转存分享主流程（统一接口）。

    逻辑：
      1. 从资源池（或 PanSou API）里筛选迅雷链接
      2. 对每条做转存 → 为转存结果创建自己的永久分享链接
      3. 汇总推送企业微信（如果配置了）
    """
    config = load_config()
    xunlei = config.get("xunlei", {}) or {}
    target_dir = xunlei.get("target_folder") or f"/{config.get('target_folder', '转存资源')}"
    webhook_url = config.get("wechat", {}).get("webhook_url", "")

    if count is None:
        count = xunlei.get("count") or config.get("count", 5)

    try:
        client = build_client(xunlei)
        log_print(f"[xunlei] 登录成功，昵称: {client.nickname or client._user_name}", "SUCCESS")
    except XunleiError as exc:
        log_print(f"[xunlei] 登录失败: {exc}", "ERROR")
        return {
            "code": 401,
            "message": f"迅雷登录失败: {exc}",
            "total_selected": 0,
            "save_success_count": 0,
            "share_success_count": 0,
            "share_results": [],
            "wechat_result": None,
            "preview": preview_only,
            "items": None,
        }

    # 取资源池
    if selected_items is not None:
        candidates = selected_items
    else:
        all_pool = cache.fetch_api_data(
            "xunlei", kw or "", expire_hours=24, force_refresh=force,
            include=include, exclude=exclude or ["可搜索"],
        )
        candidates = _collect_xunlei_items(all_pool)

    if not candidates:
        log_print("没有可用的迅雷资源（资源池里没有迅雷链接，可换关键词）", "WARNING")
        return {
            "code": 404,
            "message": "没有可用的迅雷资源",
            "total_selected": 0,
            "save_success_count": 0,
            "share_success_count": 0,
            "share_results": [],
            "wechat_result": None,
            "preview": preview_only,
            "items": None,
        }

    selected = candidates[:count]
    log_print(f"[xunlei] 选中 {len(selected)} 条，目标目录 {target_dir}", "INFO")

    if preview_only:
        items = [
            {"index": i + 1, "note": it.get("note", "无标题"), "url": it.get("url", "")}
            for i, it in enumerate(selected)
        ]
        return {
            "code": 200,
            "message": "预览成功",
            "preview": True,
            "items": items,
            "total_selected": len(selected),
            "save_success_count": 0,
            "share_success_count": 0,
            "share_results": [],
            "wechat_result": None,
        }

    # 逐条：转存 → 创建分享
    share_results = []
    save_success_count = 0
    share_success_count = 0

    for idx, item in enumerate(selected):
        url = (item.get("url") or "").strip()
        note = item.get("note", "") or ""
        log_print(f"[{idx + 1}/{len(selected)}] {note or url[:50]}", "INFO")

        # 1) 转存
        try:
            save_summary = client.transfer(
                url=url, target_dir=target_dir, list_only=False,
            )
            if not save_summary:
                raise XunleiError("转存未返回摘要")
            save_success_count += 1
            log_print(f"  转存成功: {save_summary.get('message', '')}", "SUCCESS")
        except XunleiError as exc:
            log_print(f"  转存失败: {exc}", "ERROR")
            _write_history(note, url, "SAVE_FAIL")
            share_results.append({
                "index": idx + 1,
                "note": note,
                "original_url": url,
                "status": "save_error",
                "message": str(exc),
            })
            continue

        # 2) 为转存结果创建自己的永久分享链接
        try:
            save_as_fids = save_summary.get("save_as_fids", [])
            if save_as_fids:
                # 直接用转存返回的 fid 创建分享，避免路径找错
                s = client.create_share(fids=save_as_fids, expire_days="-1",
                                        title=note or save_summary.get("share_name", ""))
            else:
                # fallback：按路径查找
                save_root = (target_dir.rstrip("/") + "/" + save_summary["share_name"].strip("/")).strip("/")
                if not save_root.startswith("/"):
                    save_root = "/" + save_root
                s = client.create_share(paths=[save_root], expire_days="-1")
            share_success_count += 1
            share_results.append({
                "index": idx + 1,
                "note": note or save_summary.get("share_name", ""),
                "original_url": url,
                "status": "success",
                "share_url": s["share_url"],
                "pass_code": s.get("pass_code", ""),
            })
            _write_history(note or save_summary.get("share_name", ""), s["share_url"], "DONE")
            log_print(f"  分享成功: {s['share_url']} (提取码 {s.get('pass_code') or '无'})", "SUCCESS")
        except XunleiError as exc:
            log_print(f"  创建分享失败: {exc}", "ERROR")
            share_results.append({
                "index": idx + 1,
                "note": note,
                "original_url": url,
                "status": "share_error",
                "message": str(exc),
            })

    # 统一收口：企微 + 飞书 bot + 飞书 bitable（core.post_share）
    wechat_result = None
    feishu_bot_result = None
    feishu_result = None
    success_shares = [r for r in share_results if r.get("share_url")]
    if success_shares:
        try:
            from core.post_share import send_messages, write_feishu_bitable
            config = load_config()
            m = send_messages(config, success_shares, kw=kw or "迅雷")
            wechat_result = m["wechat"]
            feishu_bot_result = m["feishu"]
            feishu_result = write_feishu_bitable(config, success_shares, platform="xunlei")
        except Exception as exc:
            log_print(f"[xunlei] 推送异常: {exc}", "WARNING")

    return {
        "code": 200,
        "message": "迅雷网盘转存分享完成",
        "total_selected": len(selected),
        "save_success_count": save_success_count,
        "share_success_count": share_success_count,
        "share_results": share_results,
        "wechat_result": wechat_result,
        "feishu_bot_result": feishu_bot_result,
        "feishu_result": feishu_result,
        "preview": False,
        "items": None,
    }
