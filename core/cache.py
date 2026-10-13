"""统一 API 数据获取与缓存。

数据来源（按优先级）：
1. 本地资源池 JSON（默认 项目根/资源池.json，可用 .env 的 RESOURCE_POOL_FILE 覆盖）
   —— 由 tools/list_resources.py 导出，结构 [{index, note, url}, ...]
2. PanSou 聚合 API（地址由 .env 的 PANSOU_HOST/PANSOU_PORT 决定，
   默认 http://127.0.0.1:8888/api/search）—— 本地池不可用时的兜底
3. 磁盘缓存（API 请求失败时的最后兜底）

三个来源统一返回 [{"note": ..., "url": ...}, ...] 结构。
"""
import json
import os
import re
import time
import httpx
from .logger import log_print
from .config import (CACHE_DIR, get_resource_pool_file,
                     is_local_pool_enabled, get_pansou_api_url)

os.makedirs(CACHE_DIR, exist_ok=True)


def _clean_note(note: str) -> str:
    """清洗 note 字段：剥离内嵌 URL、电报频道、百度链接、@mentions，只留纯标题。

    PanSou 插件源返回的 note 往往包含来源链接（如 "电影合集 - https://t.me/xxx | ..."）
    或 @guomanpindao 之类的频道标识，这些需要去掉，否则推送消息里会混入别人的链接。
    """
    s = note or ""
    # ① 去掉所有 URL（夸克/百度/电报/https://t.me/ 等）
    s = re.sub(r'https?://[^\s]+', ' ', s)
    # ② 去掉 @xxx 频道标识
    s = re.sub(r'@[\w\u4e00-\u9fff_-]+', ' ', s)
    # ③ 去掉电报频道链接（@xxx 已处理，再清理残留）
    # ④ 去掉 emoji 和电报 emoji（两个字节的 U+1F000 范围）
    s = re.sub(r'[\U00010000-\U0010ffff]', '', s)
    # ⑤ 去掉多余空白
    s = re.sub(r'[\s\u3000]+', ' ', s).strip()
    return s


def load_local_pool(cloud_type="quark", include=None, exclude=None):
    """读取本地资源池 JSON。

    仅对 quark 生效（资源池.json 是夸克资源清单，不含百度）。
    返回 [{"note":..., "url":...}, ...]；文件不存在或为空时返回 None
    （None 表示"本地池不可用"，调用方可据此回退到 API）。
    """
    if cloud_type != "quark":
        return None
    if not is_local_pool_enabled():
        log_print("本地资源池已通过 LOCAL_POOL_ENABLED=false 关闭，改用 API", "INFO")
        return None

    path = get_resource_pool_file()
    if not os.path.exists(path):
        log_print(f"本地资源池不存在（{path}），回退 API", "WARNING")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log_print(f"读取本地资源池失败（{path}）: {e}，回退 API", "WARNING")
        return None

    # 兼容两种结构：裸列表，或 {"data": [...]}
    if isinstance(raw, dict):
        raw = raw.get("data", [])

    items = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        raw_note = (it.get("note") or "").strip()
        url = (it.get("url") or "").strip()
        if not url:
            continue
        note = _clean_note(raw_note) or raw_note[:60]
        items.append({"note": note, "url": url, "_raw_note": raw_note})

    if not items:
        log_print(f"本地资源池为空（{path}），回退 API", "WARNING")
        return None

    items = deduplicate_data(items)
    items = local_filter(items, include, exclude)
    # 去掉内部字段
    for it in items:
        it.pop("_raw_note", None)
    log_print(f"从本地资源池读取 {len(items)} 条（{path}）", "SUCCESS")
    return items


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
    """按 url 去重 + 按 note 去重（同一个清洗后的标题只留第一条）。"""
    if not data:
        return []
    seen_urls = set()
    seen_notes = set()
    unique = []
    for item in data:
        url = item.get("url", "")
        note = item.get("note", "")
        if url and url in seen_urls:
            continue
        if note and note in seen_notes:
            continue
        if url:
            seen_urls.add(url)
        if note:
            seen_notes.add(note)
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
    """获取指定网盘类型的资源列表。

    优先级：本地资源池 JSON → PanSou API（含磁盘缓存）→ 过期缓存兜底。

    :param cloud_type: 'baidu' / 'quark'
    :param kw: 搜索关键词（仅 API 路径使用；本地池路径忽略）
    :param force_refresh: 强制刷新（跳过 API 磁盘缓存；本地池为静态文件，不受影响）
    :param expire_hours: 缓存过期小时（默认 24）
    :param include/exclude: 关键词过滤（本地过滤，不影响缓存）
    """
    # ① 本地资源池优先（仅 quark；命中即返回，不再请求 API）
    local_items = load_local_pool(cloud_type, include=include, exclude=exclude)
    if local_items is not None:
        return local_items

    # ② 回退 PanSou API 通路
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
    api_url = get_pansou_api_url()

    for attempt in range(1, max_retries + 1):
        try:
            log_print(f"请求 API [{cloud_type}] kw={use_kw}（第 {attempt}/{max_retries} 次）...")
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.get(api_url, params=params, headers=headers)
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