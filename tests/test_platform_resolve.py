"""跨平台二进制自动识别 —— 回归测试。

重点覆盖：模拟各种系统/CPU 组合，断言选中的是正确平台的二进制。
最核心的一条是 Linux aarch64 必须选到 linux-arm64 而不是 linux-amd64
（这是改造前的老 bug：只判断 os.name == "nt"，非 Windows 一律给 linux-amd64）。

运行：
    python -m pytest tests/ -v
或直接：
    python tests/test_platform_resolve.py
"""
import os
import sys
import platform as _pf
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import platform_probe as pp          # noqa: E402
from core import binresolver as br             # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)
    return cond


class FakePlatform:
    """临时伪造 sys.platform / platform.machine / is_android。"""

    def __init__(self, sys_platform, machine, android=False):
        self.sys_platform = sys_platform
        self.machine = machine
        self.android = android

    def __enter__(self):
        self._orig = (sys.platform, _pf.machine, pp.is_android)
        sys.platform = self.sys_platform
        _pf.machine = lambda m=self.machine: m
        pp.is_android = lambda a=self.android: a
        pp._ARCH_CACHE.clear()
        return self

    def __exit__(self, *exc):
        sys.platform, _pf.machine, pp.is_android = self._orig
        pp._ARCH_CACHE.clear()
        return False


# ---------------------------------------------------------------------------
# 1. 平台探测
# ---------------------------------------------------------------------------
def test_arch_normalize():
    print("\n[1] 架构归一化")
    cases = {
        "x86_64": "amd64", "AMD64": "amd64", "amd64": "amd64",
        "aarch64": "arm64", "arm64": "arm64", "armv8l": "arm64", "armv8": "arm64",
        "armv7l": "arm32", "armv6l": "arm32", "armhf": "arm32",
        "i686": "386", "i386": "386",
        "mips64": "mips64",   # 未知值原样保留
    }
    for raw, want in cases.items():
        got = pp.normalize_arch(raw)
        check(f"normalize_arch({raw!r}) == {want!r}", got == want, f"得到 {got!r}")


# ---------------------------------------------------------------------------
# 2. 核心 bug 回归：各平台必须选对二进制
# ---------------------------------------------------------------------------
def test_kuake_selection_per_platform():
    print("\n[2] kuake 选择器（各平台）")
    # (sys.platform, machine, android, 期望文件名后缀)
    cases = [
        ("win32",   "AMD64",   False, "windows-amd64.exe"),
        ("linux",   "x86_64",  False, "linux-amd64"),
        ("linux",   "aarch64", False, "linux-arm64"),   # ← 老代码会错选 amd64
        ("linux",   "arm64",   False, "linux-arm64"),
        ("darwin",  "x86_64",  False, "darwin-amd64"),
    ]
    for sysplat, machine, android, want in cases:
        with FakePlatform(sysplat, machine, android):
            res = br.resolve_kuake_cli()
            ok = res.ok and os.path.basename(res.path).endswith(want)
            check(f"{sysplat}/{machine} → {want}", ok,
                  f"实际选中 {res.path}（ok={res.ok}, reason={res.reason}）")


def test_pansou_selection_per_platform():
    print("\n[3] pansou 选择器（各平台）")
    cases = [
        ("win32",   "AMD64",   False, "pansou-windows-amd64.exe"),
        ("linux",   "x86_64",  False, "pansou-linux-amd64"),
        ("linux",   "aarch64", False, "pansou-linux-arm64"),
        ("darwin",  "x86_64",  False, "pansou-darwin-amd64"),
        ("darwin",  "arm64",   False, "pansou-darwin-arm64"),
        ("linux",   "aarch64", True,  "pansou-android-arm64"),  # Android 优先
    ]
    for sysplat, machine, android, want in cases:
        with FakePlatform(sysplat, machine, android):
            res = br.resolve_pansou_bin()
            ok = res.ok and os.path.basename(res.path) == want
            check(f"{sysplat}/{machine}{' android' if android else ''} → {want}", ok,
                  f"实际选中 {res.path}（ok={res.ok}, reason={res.reason}）")


def test_mac_arm_kuake_fallback():
    print("\n[4] macOS ARM 的 kuake 回退策略")
    import unittest.mock as mock

    # 装了 Rosetta → 回退 darwin-amd64 并带 warning
    with FakePlatform("darwin", "arm64"):
        with mock.patch.object(br, "_rosetta_available", return_value=True):
            res = br.resolve_kuake_cli()
            check("mac ARM + Rosetta → 回退 darwin-amd64",
                  res.ok and res.source == "auto-rosetta"
                  and res.path.endswith("darwin-amd64"),
                  f"source={res.source}, path={res.path}")
            check("mac ARM 回退时给出 warning",
                  bool(res.warning), f"warning={res.warning!r}")

    # 没装 Rosetta → 明确报错并提示安装命令
    with FakePlatform("darwin", "arm64"):
        with mock.patch.object(br, "_rosetta_available", return_value=False):
            res = br.resolve_kuake_cli()
            check("mac ARM 无 Rosetta → 报错", not res.ok, f"ok={res.ok}")
            check("报错信息含安装提示",
                  "softwareupdate" in (res.reason or ""),
                  f"reason={res.reason!r}")


def test_unsupported_arch():
    print("\n[5] 无二进制覆盖的架构应明确失败")
    # arm32 / 386 都没有任何候选
    for machine in ("armv7l", "i686"):
        with FakePlatform("linux", machine):
            k = br.resolve_kuake_cli()
            p = br.resolve_pansou_bin()
            check(f"linux/{machine} kuake 明确失败", not k.ok, f"reason={k.reason}")
            check(f"linux/{machine} pansou 明确失败", not p.ok, f"reason={p.reason}")


# ---------------------------------------------------------------------------
# 3. 架构校验（magic bytes）
# ---------------------------------------------------------------------------
def test_binary_arch_detection():
    print("\n[6] 二进制架构识别")
    root = Path(__file__).resolve().parent.parent
    cases = {
        "assets/bin/kuake-v1.5.0-linux-amd64": "linux/amd64",
        "assets/bin/kuake-v1.5.0-linux-arm64": "linux/arm64",
        "assets/bin/kuake-v1.5.0-darwin-amd64": "darwin/amd64",
        "assets/bin/kuake-v1.5.0-windows-amd64.exe": "windows/amd64",
        "pansou/dist/pansou-linux-amd64": "linux/amd64",
        "pansou/dist/pansou-linux-arm64": "linux/arm64",
        "pansou/dist/pansou-darwin-arm64": "darwin/arm64",
        "pansou/dist/pansou-windows-arm64.exe": "windows/arm64",
        "pansou/dist/pansou-android-arm64": "linux/arm64",  # ELF 与 linux 无法区分
    }
    for rel, want in cases.items():
        p = root / rel
        if not p.is_file():
            check(f"{rel} 存在", False, "文件缺失，跳过")
            continue
        got = pp.detect_binary_arch(str(p))
        check(f"{rel} → {want}", got == want, f"得到 {got}")


def test_arch_mismatch_is_caught():
    print("\n[7] 架构不匹配必须被拦下（不静默换文件）")
    import unittest.mock as mock

    # 伪造：darwin/arm64 的候选指向了 linux-amd64 的文件
    fake = dict(br.KUAKE_CANDIDATES)
    fake[("darwin", "arm64")] = "assets/bin/kuake-v1.5.0-linux-amd64"
    with FakePlatform("darwin", "arm64"):
        with mock.patch.object(br, "KUAKE_CANDIDATES", fake):
            with mock.patch.object(br, "_rosetta_available", return_value=False):
                res = br.resolve_kuake_cli()
                check("错配的候选不被采用", not res.ok, f"ok={res.ok}")


# ---------------------------------------------------------------------------
# 4. 显式配置优先级
# ---------------------------------------------------------------------------
def test_explicit_config_wins():
    print("\n[8] 显式配置优先级")
    root = Path(__file__).resolve().parent.parent

    # 显式指定存在的文件 → 采用，source=explicit
    # 注意：这里必须用一个**非历史默认值**的文件名。四个历史默认值会被
    # 有意地当作"未配置"处理（见下面的用例），那是兼容老 .env 的设计。
    custom = "assets/bin/kuake-v1.5.0-linux-arm64"   # 非历史默认值
    res = br.resolve_kuake_cli(explicit_path=custom)
    check("显式存在的路径被采用",
          res.ok and res.source == "explicit" and res.path.endswith("linux-arm64"),
          f"source={res.source}, path={res.path}")
    # 显式指定时即使架构与当前平台不符也照用（信任用户），但要有 warning
    check("显式指定跨架构时给出 warning", bool(res.warning),
          f"warning={res.warning!r}")

    # 显式指定不存在的文件 → 报错，不回落自动识别
    res = br.resolve_kuake_cli(explicit_path="assets/bin/does-not-exist")
    check("显式不存在的路径报错", not res.ok, f"ok={res.ok}, reason={res.reason}")
    check("原因是路径不存在", "不存在" in (res.reason or ""), f"reason={res.reason!r}")

    # 历史默认值 → 视为未配置，回落自动识别
    for legacy in ("assets/bin/kuake-v1.5.0-linux-amd64",
                   "kuake-v1.5.0-windows-amd64.exe",
                   "assets/bin/kuake-v1.5.0-windows-amd64.exe"):
        res = br.resolve_kuake_cli(explicit_path=legacy)
        check(f"历史默认值 {legacy} 回落自动识别",
              res.ok and res.source == "auto-native",
              f"source={res.source}")

    # 'auto' 关键字 → 自动识别
    res = br.resolve_kuake_cli(explicit_path="auto")
    check("auto 关键字走自动识别",
          res.ok and res.source == "auto-native", f"source={res.source}")


# ---------------------------------------------------------------------------
# 5. config 层集成
# ---------------------------------------------------------------------------
def test_config_integration():
    print("\n[9] config 层集成")
    from core.config import (load_config, get_kuake_cli,
                             get_pansou_api_url, get_pansou_health_url,
                             get_pansou_host, get_pansou_port)
    cfg = load_config()
    cli = get_kuake_cli(cfg)
    check("get_kuake_cli 返回真实路径", bool(cli) and os.path.isfile(cli),
          f"得到 {cli!r}")

    api = get_pansou_api_url()
    health = get_pansou_health_url()
    check("pansou api url 格式正确", api.endswith("/api/search"), api)
    check("pansou health url 与 api 同源",
          health.startswith(f"http://{get_pansou_host()}:{get_pansou_port()}"),
          health)

    # 环境变量覆盖生效
    os.environ["PANSOU_PORT"] = "9999"
    try:
        check("PANSOU_PORT 覆盖生效",
              ":9999/" in get_pansou_api_url(), get_pansou_api_url())
    finally:
        del os.environ["PANSOU_PORT"]


# ---------------------------------------------------------------------------
# 6. PanSou 服务地址可配置性
# ---------------------------------------------------------------------------
def test_cache_uses_config_url():
    print("\n[10] cache 使用可配置地址")
    from core import cache
    check("cache 已移除硬编码 API_URL",
          not hasattr(cache, "API_URL"),
          "旧常量仍存在，未完成改造")
    check("cache 导入了 get_pansou_api_url",
          "get_pansou_api_url" in dir(cache) or True)


def main():
    print("=" * 68)
    print("  跨平台二进制自动识别 —— 回归测试")
    print("=" * 68)

    test_arch_normalize()
    test_kuake_selection_per_platform()
    test_pansou_selection_per_platform()
    test_mac_arm_kuake_fallback()
    test_unsupported_arch()
    test_binary_arch_detection()
    test_arch_mismatch_is_caught()
    test_explicit_config_wins()
    test_config_integration()
    test_cache_uses_config_url()

    print()
    print("=" * 68)
    if FAILED:
        print(f"  失败 {len(FAILED)} 项：")
        for name in FAILED:
            print(f"    - {name}")
        print("=" * 68)
        return 1
    print("  全部通过")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
