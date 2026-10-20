"""转存前的链接有效性预检（基于 PanSou 的 POST /api/check/links）。

为什么需要它
------------
资源池里的链接是爬来的，随时可能被网盘方封禁。不预检的后果是：顺序游标
会把死链当成正常条目消耗掉——转存失败 → 该条作废 → 但游标已经前进，
下一次推送又要等整整一圈才会轮到后面的好资源。

PanSou 自带检测能力，我们直接复用：
    POST /api/check/links
    {"items": [{"disk_type": "quark", "url": "https://pan.quark.cn/s/xxx"}]}
    → {"results": [{"url": ..., "state": "ok"|"bad"|"uncertain", "summary": ...}]}

三态语义（**不要退化成两态**）
-----------------------------
- ``ok``        链接有效
- ``bad``       链接确实失效（分享不存在/已取消/被封禁/内容为空）
- ``uncertain`` 无法判定，典型原因是**被限流**（上游返回 exceed/frequency/limit）

``uncertain`` 必须当作「不阻塞」处理——如果把它当 bad 丢掉，网络抖动或限流
时会误杀大量好链接。本模块的策略是：**只跳过 bad，ok 和 uncertain 都放行**。

降级策略
--------
预检是「锦上添花」，不能因为 PanSou 没起就把主流程搞挂：
- PanSou 不可达 / 请求超时 / 返回异常 → 记 warning，**放行全部链接**（fail-open）
- 想让整个失败都当 bad 的严格模式，不是这里的默认行为

本地缓存
--------
一次预检结果写 ``data/cache/link_check.json``，默认 6 小时内不重复检测。
PanSou 侧其实也有缓存，但本地缓存能省掉一次 HTTP 往返，且 PanSou 挂掉时
仍可用（不过我们选择 fail-open 放行，缓存只是加速）。
"""
import json
import os
import time
from typing import Dict, List, Optional, Tuple

import httpx

from core.config import (get_pansou_check_url, get_check_timeout,
                         get_check_batch_size, is_check_links_enabled,
                         CACHE_DIR)
from core.logger import log_print

# 检测结果状态
STATE_OK = "ok"
STATE_BAD = "bad"
STATE_UNCERTAIN = "uncertain"

# 本次进程内的检测结果缓存（url -> (state, summary)），避免同一次运行重复请求
_MEM_CACHE: Dict[str, Tuple[str, str]] = {}

# 磁盘缓存：失败结果缓存久一点（死链几乎不会复活），有效结果缓存短一点
_DISK_CACHE_FILE = CACHE_DIR / "link_check.json"
_TTL_OK = 6 * 3600
_TTL_BAD = 24 * 3600
_TTL_UNCERTAIN = 300


# ---------------------------------------------------------------------------
# 磁盘缓存
# ---------------------------------------------------------------------------
def _load_disk_cache() -> Dict[str, dict]:
    try:
        if _DISK_CACHE_FILE.exists():
            data = json.loads(_DISK_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_disk_cache(cache: Dict[str, dict]):
    try:
        _DISK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DISK_CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log_print(f"写入链接检测缓存失败: {e}", "WARNING")


def _cache_ttl(state: str) -> int:
    if state == STATE_OK:
        return _TTL_OK
    if state == STATE_BAD:
        return _TTL_BAD
    return _TTL_UNCERTAIN


def _from_disk_cache(url: str, cache: Dict[str, dict]) -> Optional[Tuple[str, str]]:
    """命中未过期缓存则返回 (state, summary)，否则 None。"""
    rec = cache.get(url)
    if not rec:
        return None
    ts = rec.get("ts", 0)
    state = rec.get("state", "")
    if time.time() - ts > _cache_ttl(state):
        return None
    return state, rec.get("summary", "")


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------
def _normalize_items(urls: List[str], disk_type: str = "quark") -> List[dict]:
    return [{"disk_type": disk_type, "url": u} for u in urls if u]


def _post_check(urls: List[str], disk_type: str = "quark") -> Optional[List[dict]]:
    """向 PanSou 发一次检测请求。返回 results 列表，失败返回 None。"""
    payload = {"items": _normalize_items(urls, disk_type)}
    try:
        with httpx.Client(timeout=get_check_timeout()) as client:
            resp = client.post(get_pansou_check_url(), json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log_print(f"链接检测请求失败（{len(urls)} 条）: {e}", "WARNING")
        return None

    results = (data or {}).get("results")
    if not isinstance(results, list):
        log_print(f"链接检测返回结构异常: {str(data)[:200]}", "WARNING")
        return None
    return results


def check_links(urls: List[str], disk_type: str = "quark",
                use_cache: bool = True) -> Dict[str, Tuple[str, str]]:
    """批量检测链接有效性。

    Args:
        urls: 待检测的分享链接列表
        disk_type: 网盘类型，本项目的资源池都是 quark
        use_cache: 是否使用缓存

    Returns:
        ``{url: (state, summary)}``。**未出现在返回值里的 url 表示「未能判定」**，
        调用方应按放行处理（fail-open）。
    """
    result: Dict[str, Tuple[str, str]] = {}
    if not urls:
        return result

    pending: List[str] = []
    disk_cache = _load_disk_cache() if use_cache else {}
    dirty = False

    for url in urls:
        if not url:
            continue
        if use_cache and url in _MEM_CACHE:
            result[url] = _MEM_CACHE[url]
            continue
        if use_cache:
            hit = _from_disk_cache(url, disk_cache)
            if hit:
                result[url] = hit
                _MEM_CACHE[url] = hit
                continue
        pending.append(url)

    if not pending:
        return result

    batch_size = get_check_batch_size()
    total_ok = total_bad = total_unc = total_fail = 0

    for start in range(0, len(pending), batch_size):
        chunk = pending[start:start + batch_size]
        try:
            results = _post_check(chunk, disk_type)
        except Exception as e:
            # _post_check 本身已吞异常，这里再兜一层：预检是「锦上添花」，
            # 任何意外都不允许把主流程（转存/分享/推送）带崩。
            log_print(f"链接检测异常（已忽略并放行）: {e}", "WARNING")
            results = None
        if results is None:
            total_fail += len(chunk)
            continue

        # 用 normalized_url 兜底匹配：PanSou 可能对 url 做了归一化
        by_url = {}
        for r in results:
            if not isinstance(r, dict):
                continue
            for key in (r.get("url"), r.get("normalized_url")):
                if key:
                    by_url[key] = r

        for url in chunk:
            r = by_url.get(url)
            if r is None:
                total_fail += 1
                continue
            state = (r.get("state") or STATE_UNCERTAIN).strip().lower()
            summary = r.get("summary", "")
            # 兜底：认识之外的状态一律当「未能判定」，放行
            if state not in (STATE_OK, STATE_BAD, STATE_UNCERTAIN):
                state = STATE_UNCERTAIN
            pair = (state, summary)
            result[url] = pair
            _MEM_CACHE[url] = pair
            if use_cache:
                disk_cache[url] = {"state": state, "summary": summary,
                                   "ts": int(time.time())}
                dirty = True
            if state == STATE_OK:
                total_ok += 1
            elif state == STATE_BAD:
                total_bad += 1
            else:
                total_unc += 1

    if dirty:
        _save_disk_cache(disk_cache)

    log_print(
        f"链接预检完成：有效 {total_ok}，失效 {total_bad}，"
        f"未判定 {total_unc}，请求失败 {total_fail}（共 {len(urls)} 条）",
        "INFO")
    return result


def is_usable(state: str) -> bool:
    """该状态是否应当放行（送去转存）。

    **只把 bad 判为不可用**；ok 与 uncertain 都放行——uncertain 往往是限流，
    误杀代价远高于放行一条可能失效的链接。
    """
    return state != STATE_BAD


# ---------------------------------------------------------------------------
# 死链记录（避免每次重试都撞同一条）
# ---------------------------------------------------------------------------
def dead_links_file():
    from core.config import SHARE_DIR
    return SHARE_DIR / "dead_links.txt"


def load_dead_links() -> set:
    """读取历史死链 URL 集合。

    文件格式与 share_url_total.txt 保持一致：``note  |  url``，
    也兼容「一行一个纯 url」的写法。
    """
    path = dead_links_file()
    urls = set()
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                # note | url → 取 url；纯 url → 取自身
                candidate = parts[-1] if len(parts) >= 2 else parts[0]
                if candidate:
                    urls.add(candidate)
    except Exception as e:
        log_print(f"读取死链记录失败: {e}", "WARNING")
    return urls


def record_dead_links(items: List[dict]):
    """把失效条目追加写入 dead_links.txt（note | url）。"""
    if not items:
        return
    path = dead_links_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for it in items:
                f.write(f"{it.get('note', '')}  |  {it.get('url', '')}\n")
        log_print(f"已记录 {len(items)} 条死链到 {path}", "INFO")
    except Exception as e:
        log_print(f"写入死链记录失败: {e}", "WARNING")


def filter_dead(items: List[dict], dead: Optional[set] = None) -> List[dict]:
    """从条目列表中剔除已知死链。"""
    if dead is None:
        dead = load_dead_links()
    if not dead:
        return list(items)
    return [it for it in items if (it.get("url") or "").strip() not in dead]


def probe_available() -> Tuple[bool, str]:
    """探测 PanSou 检测接口是否可用（供 diagnose / 启动日志使用）。"""
    try:
        results = _post_check(["https://pan.quark.cn/s/0000000000ffff"])
    except Exception as e:
        return False, str(e)
    if results is None:
        return False, "接口无响应或返回异常"
    return True, "可用"
