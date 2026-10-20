#!/usr/bin/env python3
"""环境诊断：打印当前平台 → 选中的二进制 → 可用性全链路。

用法：
    python tools/diagnose.py
    python main.py diagnose          # 等价

退出码 0 表示全部正常，非 0 表示存在致命项（便于 CI 与脚本判断）。
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import platform_probe as pp          # noqa: E402
from core import binresolver as br             # noqa: E402
from core.config import (load_config, get_kuake_cli,   # noqa: E402
                         get_pansou_api_url, get_pansou_health_url)

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _c(text, color):
    # Windows 老控制台不支持 ANSI 时退化为纯文本
    if os.name == "nt" and not os.environ.get("WT_SESSION"):
        return text
    return f"{color}{text}{RESET}"


def _mark(ok, warn=False):
    if ok:
        return _c("✔", GREEN)
    return _c("~", YELLOW) if warn else _c("✘", RED)


def _try_run(path, args=("-h",), timeout=10):
    """试着执行二进制，返回 (成功, 说明)。

    专门把 'Exec format error' 与 'Permission denied' 区分出来，
    这两个是跨平台部署最常见的两类坑。
    """
    if not path:
        return False, "路径为空"
    try:
        r = subprocess.run([path, *args], capture_output=True, timeout=timeout)
        # 很多 CLI 用 -h 返回非 0，只要不是执行失败都算能跑
        return True, f"可执行（exit={r.returncode}）"
    except FileNotFoundError:
        return False, "文件不存在"
    except PermissionError:
        return False, "缺少执行权限（posix 上需 chmod +x）"
    except OSError as e:
        msg = str(e)
        if "Exec format error" in msg or getattr(e, "errno", None) == 8:
            return False, "Exec format error —— 架构不匹配，这是选错二进制了"
        return False, msg
    except subprocess.TimeoutExpired:
        return True, "可执行（超时未退出，通常正常）"


def main():
    fatal = False
    info = pp.probe_platform()

    print(_c("=" * 68, CYAN))
    print(_c("  环境诊断", CYAN))
    print(_c("=" * 68, CYAN))
    print()

    # ---- 平台 ----
    print(_c("[平台]", CYAN))
    print(f"  系统/架构    {pp.describe_platform(info)}")
    print(f"  raw          sys.platform={info['raw_platform']!r}  "
          f"machine={info['raw_machine']!r}")
    print(f"  Android      {info['is_android']}")
    print()

    # ---- kuake CLI ----
    cfg = load_config()
    kres = br.resolve_kuake_cli(cfg)
    print(_c("[kuake CLI]", CYAN))
    _report_binary(kres, "kuake", args=("list", "/"))
    if not kres.ok:
        fatal = True
    print()

    # ---- pansou 二进制 ----
    pres = br.resolve_pansou_bin()
    print(_c("[pansou 二进制]", CYAN))
    _report_binary(pres, "pansou", args=("-h",))
    if not pres.ok:
        fatal = True
    print()

    # ---- pansou 服务 ----
    print(_c("[pansou 服务]", CYAN))
    try:
        from core import pansou_launcher as pl
        st = pl.status()
        if st["running"]:
            print(f"  {_mark(True)} 运行中        {st['url']}")
            print(f"    插件数        {st['plugin_count']}")
            print(f"    PID           {st['pid']}（存活={st['pid_alive']}）")
            if st["plugin_count"] == 0:
                hint = _c("插件数为 0 —— 搜索会永远返回 0 条！请检查 ENABLED_PLUGINS",
                          RED)
                print(f"    {_mark(False)} {hint}")
                fatal = True
        else:
            print(f"  {_mark(False, warn=True)} 未运行        {st['url']}"
                  f"（可执行 python main.py pansou start 启动）")
    except Exception as e:
        print(f"  {_mark(False)} 检测异常      {e}")
        fatal = True
    print()

    # ---- 数据来源 ----
    print(_c("[数据来源]", CYAN))
    from core.config import get_resource_pool_file, is_local_pool_enabled
    pool = get_resource_pool_file()
    enabled = is_local_pool_enabled()
    print(f"  本地资源池     {'启用' if enabled else _c('已关闭', YELLOW)}  {pool}")
    if pool.exists():
        import json
        try:
            data = json.loads(pool.read_text(encoding="utf-8"))
            items = data.get("data") if isinstance(data, dict) else data
            print(f"  {_mark(True)} 可用条目      {len(items) if isinstance(items, list) else '?'} 条")
        except Exception as e:
            print(f"  {_mark(False)} 解析失败      {e}")
            fatal = True
    else:
        print(f"  {_mark(False, warn=True)} 文件不存在    回退 PanSou API")

    print()

    # ---- 链接预检 ----
    print(_c("[链接预检]", CYAN))
    from core.config import (is_check_links_enabled, get_pansou_check_url,
                             get_check_timeout)
    cl_enabled = is_check_links_enabled()
    print(f"  预检开关       {'开启' if cl_enabled else _c('已关闭', YELLOW)}"
          f"（.env 的 PANSOU_CHECK_ENABLED）")
    print(f"  检测接口       {get_pansou_check_url()}")
    print(f"  单次超时       {get_check_timeout():.0f}s")
    if cl_enabled:
        # 用一个必然失效的假链接探测接口连通性，不消耗真实资源
        from core import pansou_check
        ok, msg = pansou_check.probe_available()
        if ok:
            print(f"  {_mark(True)} 接口连通      {msg}")
        else:
            print(f"  {_mark(False, warn=True)} 接口不可用    {msg}")
            print(f"    {_c('预检将自动降级为「全部放行」，不影响主流程', YELLOW)}")
    dead = None
    try:
        from core import pansou_check as _pc
        dead = _pc.load_dead_links()
        print(f"  历史死链记录   {len(dead)} 条"
              f"（{_pc.dead_links_file()}）")
    except Exception:
        pass

    print()
    print(_c("=" * 68, CYAN))
    if fatal:
        print(_c("  结论：存在致命问题，请按上方提示修复", RED))
    else:
        print(_c("  结论：环境正常", GREEN))
    print(_c("=" * 68, CYAN))
    return 1 if fatal else 0


def _report_binary(res, name, args=("-h",)):
    """打印单个二进制的选择结果与可用性。"""
    if res.ok:
        print(f"  {_mark(True)} 路径          {res.path}")
        print(f"    来源          {res.source}")
        print(f"    真实架构      {res.arch or '未知'}")
        print(f"    期望平台      {res.expected}")
        ok, msg = _try_run(res.path, args)
        print(f"    {_mark(ok)} 试运行        {msg}")
        if res.warning:
            print(f"    {_c('! ' + res.warning, YELLOW)}")
    else:
        print(f"  {_mark(False)} 不可用        {res.reason}")
        print(f"    期望平台      {res.expected}")
        # 顺带列出所有候选，方便人工排查
        table = br.KUAKE_CANDIDATES if name == "kuake" else br.PANSOU_CANDIDATES
        print(f"    可选候选：")
        from core.config import PROJECT_ROOT
        for (o, a), rel in sorted(table.items()):
            p = PROJECT_ROOT / rel
            exists = "存在" if p.is_file() else "缺失"
            real = pp.detect_binary_arch(str(p)) if p.is_file() else "-"
            print(f"      {o}/{a:<8} {exists}  {real or '-':<16} {rel}")


if __name__ == "__main__":
    sys.exit(main())
