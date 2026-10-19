"""统一配置加载。

唯一配置文件：项目根目录 .env（加载到环境变量）。
结构示例见 .env.example。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
SHARE_DIR = DATA_DIR / "share"
ASSETS_DIR = PROJECT_ROOT / "assets"

# 本地资源池文件（由 tools/list_resources.py 导出，结构 [{index, note, url}, ...]）。
# 存在时 fetch_api_data 会优先从这里取数，避免依赖 PanSou 在线服务。
# 可用 .env 的 RESOURCE_POOL_FILE 覆盖（相对项目根目录或绝对路径）。
DEFAULT_RESOURCE_POOL_FILE = PROJECT_ROOT / "资源池.json"


def get_resource_pool_file():
    """返回本地资源池 JSON 的绝对路径。

    优先取 .env 的 RESOURCE_POOL_FILE（相对路径按项目根解析），
    否则用项目根下的 资源池.json。
    """
    _load_env()
    rel = os.getenv("RESOURCE_POOL_FILE", "").strip()
    if not rel:
        return DEFAULT_RESOURCE_POOL_FILE
    p = Path(rel)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def is_local_pool_enabled():
    """本地资源池总开关：LOCAL_POOL_ENABLED=false 时强制走 API。"""
    _load_env()
    v = os.getenv("LOCAL_POOL_ENABLED", "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def _get(key, default=None, cast=None):
    v = os.getenv(key)
    if v is None or v == "":
        return default
    if cast:
        try:
            return cast(v)
        except Exception:
            return default
    return v


def load_config():
    _load_env()

    count = _get("COUNT", 5, int)
    cache_expire_hours = _get("CACHE_EXPIRE_HOURS", 24.0, float)
    target_folder = _get("TARGET_FOLDER", "转存资源")

    webhook_url = _get("WECHAT_WEBHOOK_URL", "")

    def _split_csv(v):
        if not v:
            return []
        return [x.strip() for x in v.split(",") if x.strip()]

    clean_yinliu = _get("CLEAN_YINLIU", True,
                        lambda v: v.lower() in ("1", "true", "yes"))
    clean_yinliu_keywords = _split_csv(_get("CLEAN_YINLIU_KEYWORDS", ""))
    clean_yinliu_extensions = _split_csv(_get("CLEAN_YINLIU_EXTENSIONS", ""))
    clean_yinliu_excludes = _split_csv(_get("CLEAN_YINLIU_EXCLUDES", ""))
    clean_yinliu_max_mb = _get("CLEAN_YINLIU_MAX_MB", 5, float)
    clean_yinliu_aggressive = _get("CLEAN_YINLIU_AGGRESSIVE", False,
                                   lambda v: v.lower() in ("1", "true", "yes"))
    # 文件名里出现他人微信号/QQ号时，替换成自己的号（默认 kds3566）
    clean_yinliu_my_contact = _get("CLEAN_YINLIU_MY_CONTACT", "kds3566")

    baidu_cookie = _get("BAIDU_COOKIE", "")
    baidu_count = _get("BAIDU_COUNT", None, int)
    baidu_target_folder = _get("BAIDU_TARGET_FOLDER", None)

    quark_cookie = _get("QUARK_COOKIE", "")
    quark_count = _get("QUARK_COUNT", None, int)
    quark_kuake_cli = _get("QUARK_KUAKE_CLI", "")
    quark_folder_fid = _get("QUARK_FOLDER_FID", "0") or "0"
    quark_yinliu_pdf = _get("QUARK_YINLIU_PDF", "assets/99999.pdf")
    quark_yinliu_prob = _get("QUARK_YINLIU_PROBABILITY", 0.0, float)
    quark_yinliu_subdir = _get("QUARK_YINLIU_SUBDIR", "自动转存")

    config = {
        "count": count,
        "cache_expire_hours": cache_expire_hours,
        "target_folder": target_folder,
        "clean_yinliu": {
            "enabled": clean_yinliu,
            "keywords": clean_yinliu_keywords,
            "extensions": clean_yinliu_extensions,
            "excludes": clean_yinliu_excludes,
            "max_small_file_bytes": int(clean_yinliu_max_mb * 1024 * 1024),
            "aggressive_ext_check": clean_yinliu_aggressive,
            "my_contact": clean_yinliu_my_contact,
        },
        "wechat": {
            "webhook_url": webhook_url,
        },
        "baidu": {
            "cookie": baidu_cookie,
            "count": baidu_count if baidu_count is not None else count,
            "target_folder": baidu_target_folder or target_folder,
        },
        "quark": {
            "cookie": quark_cookie,
            "count": quark_count if quark_count is not None else count,
            "kuake_cli": quark_kuake_cli,
            "folder_fid": quark_folder_fid,
            "yinliu_pdf": quark_yinliu_pdf,
            "yinliu_probability": quark_yinliu_prob,
            "yinliu_subdir": quark_yinliu_subdir,
        },
        "feishu": {
            "enabled": _get("FEISHU_ENABLED", False, lambda v: v.lower() in ("1", "true", "yes")),
            "app_id": _get("FEISHU_APP_ID", ""),
            "app_secret": _get("FEISHU_APP_SECRET", ""),
            "wiki_node_token": _get("FEISHU_WIKI_NODE_TOKEN", ""),
            "table_id": _get("FEISHU_TABLE_ID", ""),
        },
    }
    return config


def resolve_path(rel_path: str) -> str:
    p = Path(rel_path)
    if p.is_absolute():
        return str(p)
    return str(PROJECT_ROOT / p)


def get_kuake_cli(config) -> str:
    """返回当前平台可用的 kuake CLI 绝对路径。

    自动识别优先：QUARK_KUAKE_CLI 为空或 'auto' 时按当前系统/架构自动选择；
    填了具体路径则视为显式指定，完全跳过自动识别（但会校验文件是否存在）。
    老 .env 里残留的历史默认值会被识别并回落为自动识别。

    全都拿不到时返回 ""，调用方（adapters/quark.py 的 _kuake_* 系列）
    已有 os.path.exists 判空逻辑，会跳过相关操作。
    """
    from core import binresolver
    from core.logger import log_print

    res = binresolver.resolve_kuake_cli(config)
    if res.ok:
        if res.warning:
            log_print(res.warning, "WARNING")
        return res.path
    log_print(f"kuake CLI 不可用 —— {res.reason}", "ERROR")
    return ""


def get_yinliu_pdf(config) -> str:
    rel = config["quark"].get("yinliu_pdf") or "assets/333333.pdf"
    return resolve_path(rel)


# ---------------------------------------------------------------------------
# PanSou 服务地址（供 core/cache.py 与 core/pansou_launcher.py 共用）
# ---------------------------------------------------------------------------
def get_pansou_host() -> str:
    """PanSou 监听地址，默认 127.0.0.1。

    用 127.0.0.1 而非 localhost，规避部分系统上 localhost 优先解析到
    IPv6 ::1 而服务只监听 IPv4 导致的连接失败。
    """
    _load_env()
    return (_get("PANSOU_HOST", "127.0.0.1") or "127.0.0.1").strip()


def get_pansou_port() -> int:
    _load_env()
    return int(_get("PANSOU_PORT", 8888, int) or 8888)


def get_pansou_api_url() -> str:
    """搜索接口完整 URL。PANSOU_API_URL 显式设置时优先。"""
    _load_env()
    explicit = (_get("PANSOU_API_URL", "") or "").strip()
    if explicit:
        return explicit
    return f"http://{get_pansou_host()}:{get_pansou_port()}/api/search"


def get_pansou_health_url() -> str:
    """健康检查接口 URL（以 /api/search 为基准推导，保证同源同端口）。"""
    api = get_pansou_api_url()
    if api.endswith("/api/search"):
        return api[: -len("/api/search")] + "/api/health"
    return f"http://{get_pansou_host()}:{get_pansou_port()}/api/health"


def get_pansou_check_url() -> str:
    """链接有效性检测接口 URL（POST /api/check/links）。"""
    api = get_pansou_api_url()
    if api.endswith("/api/search"):
        return api[: -len("/api/search")] + "/api/check/links"
    return f"http://{get_pansou_host()}:{get_pansou_port()}/api/check/links"


# ---------------------------------------------------------------------------
# 转存前链接有效性预检
# ---------------------------------------------------------------------------
def is_check_links_enabled() -> bool:
    """转存前是否用 PanSou 预检链接有效性。默认开启。

    .env 设 PANSOU_CHECK_ENABLED=false 可全局关闭。
    """
    _load_env()
    return bool(_get("PANSOU_CHECK_ENABLED", True, bool))


def get_check_timeout() -> float:
    """单次检测请求超时（秒）。默认 30。

    检测走的是网盘上游接口，一批多条时耗时可能到十几秒，超时别设太短。
    """
    _load_env()
    try:
        v = float(_get("PANSOU_CHECK_TIMEOUT", 30) or 30)
    except (TypeError, ValueError):
        v = 30.0
    return v if v > 0 else 30.0


def get_check_batch_size() -> int:
    """单次检测请求最多带多少条链接。默认 50。

    PanSou 侧有 maxCheckItems 上限，超了会被拒；这里主动分批更稳妥。
    """
    _load_env()
    try:
        v = int(_get("PANSOU_CHECK_BATCH", 50) or 50)
    except (TypeError, ValueError):
        v = 50
    return v if v > 0 else 50
