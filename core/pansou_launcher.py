"""PanSou 跨平台启动器。

替代原来只有 Windows 的 start_pansou.ps1：自动选二进制、注入正确的环境变量、
后台启动、等待就绪、记录 PID 与日志。所有平台（Windows/Linux/macOS/Android）
共用同一份逻辑。

**关键陷阱**：PanSou 必须拿到 `ENABLED_PLUGINS`，否则它会打印
「未设置插件列表 (ENABLED_PLUGINS)，未加载任何插件」并**永远返回 0 条搜索结果**，
但 `/api/health` 依然返回 200。所以本模块的就绪判定不只查 health 状态码，
还要求 health 返回体里的插件数 > 0 —— 否则视为启动失败并给出明确原因。

环境变量优先级：.env 显式设置 > pansou/docker-compose.yml > 内置兜底常量。
从 docker-compose.yml 读取是为了避免「插件列表在三处硬编码、改一处漏两处」。
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from core import binresolver
from core.config import (PROJECT_ROOT, LOG_DIR, DATA_DIR,
                         get_pansou_port, get_pansou_api_url,
                         get_pansou_health_url)
from core.logger import log_print

PANSOU_DIR = PROJECT_ROOT / "pansou"
COMPOSE_FILE = PANSOU_DIR / "docker-compose.yml"
PID_FILE = DATA_DIR / "pansou.pid"
LOG_FILE = LOG_DIR / "pansou.log"

# 兜底常量：docker-compose.yml 读取失败时使用（内容取自该文件）
_FALLBACK_CHANNELS = (
    "tgsearchers3,tgsearchers7,yunpanxunlei,QuarkFree,Quark_Movies,"
    "Baidu_netdisk,shareAliyun,ucquark,leoziyuan"
)
_FALLBACK_PLUGINS = (
    "wanou,pansearch,quark4k,quarksoo,quarkres,dyyjpro,shandian,zhizhen,"
    "duoduo,huban,labi,muou,susu,u3c3,clmao,feikuai,haitunsou,miaoso,"
    "xiaoyu,xiaokupan,qqpd,panlian,buerchen"
)


def _parse_compose_env(key):
    """从 pansou/docker-compose.yml 的 environment 段提取某个变量的值。

    只做行级正则匹配（形如 `- KEY=VALUE`），不引入 YAML 依赖，
    因为该文件结构简单且要保持 pypi 依赖精简。读取失败返回 ""。
    """
    if not COMPOSE_FILE.is_file():
        return ""
    try:
        text = COMPOSE_FILE.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = re.search(rf"^\s*-\s*{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _build_env():
    """组装 PanSou 运行所需的环境变量。

    优先级：.env（PANSOU_* 前缀） > docker-compose.yml > 内置兜底。
    """
    env = os.environ.copy()
    port = get_pansou_port()

    env["PORT"] = str(port)
    env["CACHE_ENABLED"] = "true"
    env["CACHE_PATH"] = str(PANSOU_DIR / "cache")
    env["CACHE_MAX_SIZE"] = "100"
    env["CACHE_TTL"] = "60"
    env["ASYNC_PLUGIN_ENABLED"] = "true"
    env["ASYNC_RESPONSE_TIMEOUT"] = "4"
    env["ASYNC_MAX_BACKGROUND_WORKERS"] = "20"
    env["ASYNC_MAX_BACKGROUND_TASKS"] = "100"
    env["ASYNC_CACHE_TTL_HOURS"] = "1"

    plugins = (os.getenv("PANSOU_ENABLED_PLUGINS", "").strip()
               or _parse_compose_env("ENABLED_PLUGINS")
               or _FALLBACK_PLUGINS)
    channels = (os.getenv("PANSOU_CHANNELS", "").strip()
                or _parse_compose_env("CHANNELS")
                or _FALLBACK_CHANNELS)
    env["ENABLED_PLUGINS"] = plugins
    env["CHANNELS"] = channels

    os.makedirs(PANSOU_DIR / "cache", exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    return env, plugins, channels


# ---------------------------------------------------------------------------
# 运行状态检测
# ---------------------------------------------------------------------------
def probe_health(timeout=5.0):
    """请求 /api/health，返回 (是否可达, 插件数, 原始dict)。

    插件数用于识别「服务起来了但插件没加载」这一静默失败场景。
    """
    url = get_pansou_health_url()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            if r.status_code != 200:
                return False, 0, {}
            data = r.json()
    except Exception:
        return False, 0, {}

    # health 返回体里插件信息可能在 data 下，也可能在顶层，两种都试
    body = data.get("data") if isinstance(data.get("data"), dict) else data
    count = 0
    for key in ("plugin_count", "pluginCount", "plugins_count"):
        if isinstance(body.get(key), int):
            count = body[key]
            break
    else:
        plugins = body.get("plugins") or body.get("plugin_names")
        if isinstance(plugins, list):
            count = len(plugins)
        elif isinstance(plugins, int):
            count = plugins
    return True, count, data


def is_running():
    """服务是否可达（只看端口通不通，不看插件）。"""
    ok, _, _ = probe_health(timeout=3.0)
    return ok


def _read_pid():
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_pid(pid):
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# 启动 / 停止
# ---------------------------------------------------------------------------
def start(foreground=False, wait=60, explicit_bin=None):
    """启动 PanSou。

    返回 dict: {'ok', 'pid', 'url', 'log', 'plugin_count', 'message'}
    """
    url = get_pansou_api_url()

    # ① 已在运行则直接复用，避免重复起进程抢占端口
    if is_running():
        ok, count, _ = probe_health()
        log_print(f"PanSou 已在运行：{url}（插件数={count}）", "SUCCESS")
        return {"ok": True, "pid": _read_pid(), "url": url,
                "log": str(LOG_FILE), "plugin_count": count,
                "message": "已在运行，复用现有实例"}

    # ② 选二进制
    res = binresolver.resolve_pansou_bin(explicit_bin)
    if not res.ok:
        log_print(f"PanSou 不可用 —— {res.reason}", "ERROR")
        return {"ok": False, "pid": None, "url": url, "log": str(LOG_FILE),
                "plugin_count": 0, "message": res.reason}
    log_print(f"PanSou 二进制：{res.path}（架构={res.arch}）", "INFO")

    env, plugins, channels = _build_env()
    plugin_n = len([x for x in plugins.split(",") if x.strip()])
    channel_n = len([x for x in channels.split(",") if x.strip()])
    log_print(f"注入环境变量：插件 {plugin_n} 个，频道 {channel_n} 个", "INFO")

    if foreground:
        log_print(f"前台启动 PanSou → {url}（Ctrl+C 退出）", "INFO")
        try:
            subprocess.call([res.path], cwd=str(PANSOU_DIR), env=env)
        except KeyboardInterrupt:
            pass
        return {"ok": True, "pid": None, "url": url, "log": str(LOG_FILE),
                "plugin_count": 0, "message": "前台进程已退出"}

    # ③ 后台启动：独立进程组，日志追加写入
    try:
        log_fp = open(LOG_FILE, "ab", buffering=0)
    except OSError as e:
        msg = f"无法写入日志文件 {LOG_FILE}: {e}"
        log_print(msg, "ERROR")
        return {"ok": False, "pid": None, "url": url, "log": str(LOG_FILE),
                "plugin_count": 0, "message": msg}

    kwargs = {}
    if os.name != "nt":
        # 建立独立进程组，stop() 时可按组整棵终止
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [res.path], cwd=str(PANSOU_DIR), env=env,
            stdout=log_fp, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            **kwargs
        )
    except OSError as e:
        log_fp.close()
        msg = f"启动失败（{e}）。若是 'Exec format error' 说明架构不匹配。"
        log_print(msg, "ERROR")
        return {"ok": False, "pid": None, "url": url, "log": str(LOG_FILE),
                "plugin_count": 0, "message": msg}

    _write_pid(proc.pid)
    log_print(f"PanSou 已启动 PID={proc.pid}，日志：{LOG_FILE}", "INFO")

    # ④ 等待就绪（必须过插件校验）
    ok, message = wait_ready(proc, timeout=wait)
    if not ok:
        log_print(f"PanSou 启动失败：{message}", "ERROR")
        _tail_log()
        _kill(proc.pid)
        return {"ok": False, "pid": proc.pid, "url": url,
                "log": str(LOG_FILE), "plugin_count": 0, "message": message}

    _, count, _ = probe_health()
    log_print(f"PanSou 就绪：{url}（插件数={count}）", "SUCCESS")
    return {"ok": True, "pid": proc.pid, "url": url, "log": str(LOG_FILE),
            "plugin_count": count, "message": "启动成功"}


def wait_ready(proc=None, timeout=60, interval=1.0):
    """轮询等待服务就绪，返回 (ok, message)。

    就绪条件（两者都要满足）：
      1. /api/health 可达
      2. health 返回的插件数 > 0

    只看条件 1 是不够的：ENABLED_PLUGINS 没传对时服务照样起得来、
    health 照样 200，但搜索永远是 0 条，属于典型的静默失败。
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, f"进程已退出（exit code={proc.returncode}）"

        reachable, count, _ = probe_health(timeout=3.0)
        if reachable:
            if count > 0:
                return True, f"就绪（插件数={count}）"
            return False, (
                "服务已响应但**未加载任何插件**，搜索将永远返回 0 条。"
                "请检查 ENABLED_PLUGINS 环境变量是否传入"
                "（可在 .env 设 PANSOU_ENABLED_PLUGINS 覆盖）"
            )
        time.sleep(interval)

    return False, f"等待 {timeout} 秒后仍无法连接 {get_pansou_health_url()}"


def _kill(pid):
    """终止进程（含子进程树）。"""
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        pass


def stop():
    """停止 PanSou（按 PID 文件记录的进程组终止）。"""
    pid = _read_pid()
    if not pid or not _pid_alive(pid):
        # PID 文件没用，但服务可能在跑（人工启动的），尝试用端口找
        if not is_running():
            return {"ok": True, "message": "PanSou 未在运行"}
        return {"ok": False, "message":
                "服务在运行但找不到 PID（可能是人工启动的），请手动终止"}

    _kill(pid)
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.25)
    else:
        # 温和终止无效，强杀
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               capture_output=True, timeout=15)
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, ProcessLookupError, subprocess.SubprocessError):
            pass

    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    log_print(f"PanSou 已停止（PID={pid}）", "SUCCESS")
    return {"ok": True, "message": f"已停止 PID={pid}"}


def status():
    """返回当前运行状态。"""
    reachable, count, _ = probe_health()
    return {
        "running": reachable,
        "url": get_pansou_api_url(),
        "health_url": get_pansou_health_url(),
        "plugin_count": count,
        "pid": _read_pid(),
        "pid_alive": _pid_alive(_read_pid()),
        "log": str(LOG_FILE),
    }


def _tail_log(lines=20):
    """把日志末尾若干行打到控制台，方便定位启动失败原因。"""
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    tail = [ln for ln in text if ln.strip()][-lines:]
    if tail:
        log_print("---- PanSou 日志末尾 ----", "INFO")
        for ln in tail:
            log_print(f"  {ln}", "INFO")
        log_print("---- 日志结束 ----", "INFO")
