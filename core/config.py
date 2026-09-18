"""统一配置加载。

优先级（高 -> 低）：
1. 环境变量（含 .env）
2. config.yaml（项目根目录）
3. 代码内置默认值

config.yaml 结构示例见项目根目录 config.example.yaml。
"""
import os
from pathlib import Path

# 项目根目录（core 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 运行时数据目录
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
SHARE_DIR = DATA_DIR / "share"
ASSETS_DIR = PROJECT_ROOT / "assets"

# 默认配置
_DEFAULTS = {
    "count": 5,
    "cache_expire_hours": 24,
    "target_folder": "转存资源",
    "wechat": {
        "webhook_url": "",
    },
    "baidu": {
        "cookie": "",
        "count": None,           # None 表示回退到顶层 count
        "target_folder": None,
    },
    "quark": {
        "cookie": "",
        "count": None,
        "kuake_cli": "",
        "folder_fid": "0",
        "yinliu_pdf": "assets/333333.pdf",
        "yinliu_subdir": "自动转存",
        "yinliu_probability": 0.0,
    },
}


def _load_env():
    """加载 .env 到环境变量（若 python-dotenv 可用）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def _load_yaml():
    """读取 config.yaml（若存在），返回 dict 或 None。"""
    try:
        import yaml
    except ImportError:
        return None
    path = PROJECT_ROOT / "config.yaml"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return None


def _deep_merge(base, override):
    """递归合并 override 到 base，返回新 dict。"""
    result = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _from_env(config):
    """用环境变量覆盖配置（兼容旧项目 .env 字段名）。"""
    # 顶层
    if os.getenv("COUNT"):
        config["count"] = int(os.getenv("COUNT"))
    if os.getenv("CACHE_EXPIRE_HOURS"):
        config["cache_expire_hours"] = float(os.getenv("CACHE_EXPIRE_HOURS"))

    # 企微 webhook（兼容两套命名）
    webhook = (os.getenv("WEBHOOK_URL")
               or os.getenv("WECHAT_WEBHOOK_URL")
               or config.get("wechat", {}).get("webhook_url", ""))
    if webhook:
        config["wechat"]["webhook_url"] = webhook

    # 百度
    if os.getenv("COOKIE"):
        config["baidu"]["cookie"] = os.getenv("COOKIE")
    if os.getenv("TARGET_FOLDER"):
        config["baidu"]["target_folder"] = os.getenv("TARGET_FOLDER")

    # 夸克
    if os.getenv("QUARK_COOKIES"):
        config["quark"]["cookie"] = os.getenv("QUARK_COOKIES")
    if os.getenv("KUAKE_CLI"):
        config["quark"]["kuake_cli"] = os.getenv("KUAKE_CLI")
    if os.getenv("PROBABILITY"):
        config["quark"]["yinliu_probability"] = float(os.getenv("PROBABILITY"))
    if os.getenv("YINLIU_FILE_ID"):
        config["quark"]["yinliu_file_id"] = os.getenv("YINLIU_FILE_ID")

    return config


def load_config():
    """加载最终配置 dict。"""
    _load_env()
    config = _deep_merge(_DEFAULTS, _load_yaml())
    config = _from_env(config)

    # 平台级 count / target_folder 回退到顶层
    if config["baidu"].get("count") is None:
        config["baidu"]["count"] = config["count"]
    if config["quark"].get("count") is None:
        config["quark"]["count"] = config["count"]
    if config["baidu"].get("target_folder") is None:
        config["baidu"]["target_folder"] = config["target_folder"]

    return config


def resolve_path(rel_path: str) -> str:
    """把相对项目根目录的路径解析为绝对路径。"""
    p = Path(rel_path)
    if p.is_absolute():
        return str(p)
    return str(PROJECT_ROOT / p)


def get_kuake_cli(config) -> str:
    rel = config["quark"].get("kuake_cli")
    is_win = os.name == "nt"
    default_win = "assets/bin/kuake-v1.5.0-windows-amd64.exe"
    default_linux = "assets/bin/kuake-v1.5.0-linux-amd64"
    if not rel or rel == default_linux or rel == default_win:
        rel = default_win if is_win else default_linux
    return resolve_path(rel)


def get_yinliu_pdf(config) -> str:
    """返回引流 PDF 的绝对路径。"""
    rel = config["quark"].get("yinliu_pdf") or "assets/333333.pdf"
    return resolve_path(rel)