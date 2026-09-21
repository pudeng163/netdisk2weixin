"""统一 API 数据获取与缓存。

两个项目都请求同一个聚合 API（https://so.252035.xyz/api/search），
仅 cloud_types 不同（baidu / quark），缓存结构略有差异：
- 百度：缓存整个 data 对象，用 data.merged_by_type.baidu 取资源，带 cache_time
- 夸克：缓存资源列表本身，带 timestamp

统一为：缓存按 cloud_type 分文件存储，结构统一为
{ "timestamp": ..., "data": <资源列表> }。
"""
import json
import os
import re
import time
import requests
from .logger import log_print
from .config import CACHE_DIR

os.makedirs(CACHE_DIR, exist_ok=True)

API_URL = "https://so.252035.xyz/api/search"


def _safe_kw(kw: str) -> str:
    kw = kw or "1"
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", kw)
    return safe[:30] or "1"


def _cache_file(cloud_type: str, kw: str = "1") -> str:
    return str(CACHE_DIR / f"api_cache_{cloud_type}_{_safe_kw(kw)}.json")


def save_api_cache(cloud_type: str, data, kw: str = "1"):
    try:
        payload = {"timestamp": time.time(), "kw": kw, "data": data}
        with open(_cache_file(cloud_type, kw), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log_print(f"API 数据已缓存 [{cloud_type}] kw={kw}", "DEBUG")
    except Exception as e:
        log_print(f"保存 API 缓存失败: {str(e)}", "WARNING")


def load_api_cache(cloud_type: str, kw: str = "1"):
    path = _cache_file(cloud_type, kw)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get("data")
    except Exception as e:
        log_print(f"加载 API 缓存失败: {str(e)}", "WARNING")
        return None


def is_cache_expired(cloud_type: str, expire_hours: float, kw: str = "1") -> bool:
    path = _cache_file(cloud_type, kw)
    if not os.path.exists(path):
        return True
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        ts = cache.get("timestamp", 0)
        return (time.time() - ts) >= (expire_hours * 3600)
    except Exception:
        return True


def deduplicate_data(data):
    """按 url 去重。"""
    if not data:
        return []
    seen = set()
    unique = []
    for item in data:
        url = item.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(item)
    if len(data) > len(unique):
        log_print(f"去重完成：原始 {len(data)} 条，去重后 {len(unique)} 条", "INFO")
    return unique


def local_filter(data, include=None, exclude=None):
    """本地按标题关键词过滤。"""
    if not data or (not include and not exclude):
        return data
    out = []
    for item in data:
        note = item.get("note", "") or ""
        if include:
            if not any(kw in note for kw in include):
                continue
        if exclude:
            if any(kw in note for kw in exclude):
                continue
        out.append(item)
    if len(data) != len(out):
        log_print(f"本地过滤：{len(data)} → {len(out)} 条", "INFO")
    return out


def fetch_api_data(cloud_type, kw="1", force_refresh=False, expire_hours=24.0,
                   include=None, exclude=None):
    """从聚合 API 获取指定网盘类型的资源列表。

    :param cloud_type: 'baidu' / 'quark'
    :param kw: 搜索关键词
    :param force_refresh: 强制刷新
    :param expire_hours: 缓存过期小时（默认 24）
    :param include/exclude: 关键词过滤（本地过滤，不影响缓存）
    """
    use_kw = kw if kw else "1"

    use_cache = (not force_refresh) and \
                (not is_cache_expired(cloud_type, expire_hours, use_kw))
    if use_cache:
        cached = load_api_cache(cloud_type, use_kw)
        if cached:
            cached = deduplicate_data(cached)
            cached = local_filter(cached, include, exclude)
            log_print(f"使用本地缓存 [{cloud_type}] kw={use_kw}，共 {len(cached)} 条记录", "SUCCESS")
            return cached

    params = {"kw": use_kw, "cloud_types": cloud_type}

    headers = {
        "accept": "application/json, text/plain, */*",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/130.0.0.0 Safari/537.36 QuarkPC/6.7.7.829"),
    }

    max_retries = 3
    retry_interval = 6
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            log_print(f"请求 API [{cloud_type}] kw={use_kw}（第 {attempt}/{max_retries} 次）...")
            response = requests.get(API_URL, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get("code") not in (0, None):
                msg = data.get("message", "未知错误")
                raise ValueError(f"API 业务错误 code={data.get('code')}: {msg}")
            break
        except Exception as e:
            last_exception = e
            log_print(f"API 请求失败（第 {attempt}/{max_retries} 次）: {str(e)}", "WARNING")
            if attempt < max_retries:
                log_print(f"等待 {retry_interval} 秒后重试...", "INFO")
                time.sleep(retry_interval)
            continue
    else:
        log_print(f"API 请求全部重试失败: {last_exception}", "ERROR")
        cached = load_api_cache(cloud_type, use_kw)
        if cached:
            cached = deduplicate_data(cached)
            cached = local_filter(cached, include, exclude)
            log_print(f"回退到过期缓存 [{cloud_type}] kw={use_kw}，共 {len(cached)} 条", "WARNING")
            return cached
        return []

    body = data.get("data", {})
    resources = body.get("merged_by_type", {}).get(cloud_type, [])
    api_total = body.get("total", len(resources))

    if resources:
        save_api_cache(cloud_type, resources, use_kw)

    resources = local_filter(resources, include, exclude)

    if not resources:
        log_print(
            f"API 成功但无匹配资源 [{cloud_type}] kw={use_kw} "
            f"(API 返回总数: {api_total}, 过滤后: {len(resources)})",
            "WARNING"
        )

    return resources