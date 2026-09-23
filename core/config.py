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

    baidu_cookie = _get("BAIDU_COOKIE", "")
    baidu_count = _get("BAIDU_COUNT", None, int)
    baidu_target_folder = _get("BAIDU_TARGET_FOLDER", None)

    quark_cookie = _get("QUARK_COOKIE", "")
    quark_count = _get("QUARK_COUNT", None, int)
    quark_kuake_cli = _get("QUARK_KUAKE_CLI", "")
    quark_folder_fid = _get("QUARK_FOLDER_FID", "0") or "0"
    quark_yinliu_pdf = _get("QUARK_YINLIU_PDF", "assets/333333.pdf")
    quark_yinliu_prob = _get("QUARK_YINLIU_PROBABILITY", 0.0, float)
    quark_yinliu_subdir = _get("QUARK_YINLIU_SUBDIR", "自动转存")

    config = {
        "count": count,
        "cache_expire_hours": cache_expire_hours,
        "target_folder": target_folder,
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
    }
    return config


def resolve_path(rel_path: str) -> str:
    p = Path(rel_path)
    if p.is_absolute():
        return str(p)
    return str(PROJECT_ROOT / p)


def get_kuake_cli(config) -> str:
    rel = config["quark"].get("kuake_cli", "")
    is_win = os.name == "nt"
    default_win = "assets/bin/kuake-v1.5.0-windows-amd64.exe"
    default_linux = "assets/bin/kuake-v1.5.0-linux-amd64"
    if not rel or rel == default_linux or rel == default_win:
        rel = default_win if is_win else default_linux
    return resolve_path(rel)


def get_yinliu_pdf(config) -> str:
    rel = config["quark"].get("yinliu_pdf") or "assets/333333.pdf"
    return resolve_path(rel)
