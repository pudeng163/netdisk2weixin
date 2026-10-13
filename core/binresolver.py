"""二进制选择器：为 kuake CLI 与 pansou 自动挑选当前平台可用的可执行文件。

统一降级链（两个二进制共用）：

    ① 显式配置     .env 里填了具体路径 → 直接用（信任用户，不校验架构）
    ② 平台精确     按 (os, arch) 查候选表 → 存在 + magic 校验通过 → 用
    ③ 特例回退     仅 kuake 在 macOS ARM 上回退 darwin-amd64（靠 Rosetta）
    ④ 失败         返回 ok=False 与明确原因

设计原则：**架构校验失败不静默换文件**。
若在 Linux ARM 上选中了 x86-64 的 ELF，执行时会报 `Exec format error`，
这类错误排查成本极高。所以宁可明确报「这个候选架构不对」，也不自作主张
跳到另一个架构（那样只会造成更难发现的隐蔽错配）。
"""
import os
from pathlib import Path

from core import platform_probe as pp
from core.logger import log_print

# ---------------------------------------------------------------------------
# 候选表
# ---------------------------------------------------------------------------
# kuake CLI：注意**没有 macOS ARM 原生版**，需要走 Rosetta 回退。
KUAKE_CANDIDATES = {
    ("windows", "amd64"): "assets/bin/kuake-v1.5.0-windows-amd64.exe",
    ("linux", "amd64"): "assets/bin/kuake-v1.5.0-linux-amd64",
    ("linux", "arm64"): "assets/bin/kuake-v1.5.0-linux-arm64",
    ("darwin", "amd64"): "assets/bin/kuake-v1.5.0-darwin-amd64",
}

# pansou：7 个平台齐全，含 Android。
PANSOU_CANDIDATES = {
    ("windows", "amd64"): "pansou/dist/pansou-windows-amd64.exe",
    ("windows", "arm64"): "pansou/dist/pansou-windows-arm64.exe",
    ("linux", "amd64"): "pansou/dist/pansou-linux-amd64",
    ("linux", "arm64"): "pansou/dist/pansou-linux-arm64",
    ("darwin", "amd64"): "pansou/dist/pansou-darwin-amd64",
    ("darwin", "arm64"): "pansou/dist/pansou-darwin-arm64",
    ("android", "arm64"): "pansou/dist/pansou-android-arm64",
}

# 历史默认值：老版本 .env 里可能残留这些值，命中时视为「未显式配置」，
# 自动切换到新的识别逻辑，避免用户以为配置生效了其实被写死。
_LEGACY_KUAKE_DEFAULTS = {
    "kuake-v1.5.0-windows-amd64.exe",
    "kuake-v1.5.0-linux-amd64",
    "assets/bin/kuake-v1.5.0-windows-amd64.exe",
    "assets/bin/kuake-v1.5.0-linux-amd64",
}


class BinaryResolution:
    """一次二进制选择的结果。"""

    __slots__ = ("path", "source", "arch", "ok", "warning", "reason", "expected")

    def __init__(self, path=None, source="missing", arch=None, ok=False,
                 warning=None, reason=None, expected=None):
        self.path = path            # 选中的绝对路径，失败时为 None
        self.source = source        # explicit / auto-native / auto-rosetta / missing
        self.arch = arch            # 该文件的真实架构，如 'linux/amd64'
        self.ok = ok                # 是否可用
        self.warning = warning      # 非致命提示（如走了 Rosetta）
        self.reason = reason        # 失败原因
        self.expected = expected    # 期望的 'os/arch'

    def __repr__(self):
        return (f"<BinaryResolution {self.source} ok={self.ok} "
                f"path={self.path} arch={self.arch}>")

    def describe(self):
        if self.ok:
            s = f"选中 {self.path}（来源={self.source}"
            if self.arch:
                s += f", 架构={self.arch}"
            if self.source == "auto-rosetta":
                s += ", 依赖 Rosetta 2"
            return s + "）"
        return f"不可用：{self.reason}"


def _resolve_root():
    from core.config import PROJECT_ROOT
    return PROJECT_ROOT


def _rosetta_available():
    """macOS ARM 上检测 Rosetta 2 是否已安装。

    优先查 oahd（Rosetta 的守护进程），其次用 sysctl 问内核
    「当前进程是否被转译运行」——后者需要当前进程本身就是 x86 二进制才有意义，
    因此只作为辅助判据。
    """
    if Path("/usr/libexec/rosetta/oahd").exists():
        return True
    try:
        import subprocess
        r = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"],
                           capture_output=True, timeout=5)
        # 返回 0/1 说明 Rosetta 机制存在（能返回就说明支持）
        if r.returncode == 0 and r.stdout.strip() in (b"0", b"1"):
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _pick(candidates, root, platform_info, table_name="binary"):
    """在候选表里按 (os, arch) 精确取一个，并做存在性 + 架构校验。

    返回 (path_or_None, arch_or_None, reason_or_None)。
    """
    pos, parch = platform_info["os"], platform_info["arch"]

    # Android 优先：pansou 有专门的 android-arm64 构建，
    # 它比 linux-arm64 更适配（针对 bionic libc 编译）。
    if platform_info.get("is_android") and ("android", parch) in candidates:
        pos = "android"

    rel = candidates.get((pos, parch))
    if not rel:
        avail = ", ".join(f"{o}/{a}" for o, a in sorted(candidates)) or "无"
        return None, None, (
            f"{table_name} 没有适配 {pos}/{parch} 的版本（现有：{avail}）"
        )

    path = str(root / rel)
    if not os.path.isfile(path):
        return None, None, f"文件不存在：{path}"

    real_arch = pp.detect_binary_arch(path)
    if real_arch is None:
        return None, None, f"无法识别 {path} 的文件头，可能已损坏"

    if not pp.arch_matches(real_arch, platform_info):
        return None, None, (
            f"架构不匹配：{path} 实际是 {real_arch}，"
            f"当前平台需要 {pos}/{parch}"
        )

    return path, real_arch, None


def resolve_kuake_cli(config=None, explicit_path=None):
    """为当前平台选择 kuake CLI。

    explicit_path 优先于 config；config 里 quark.kuake_cli 为 'auto' 或空
    表示自动识别。
    """
    root = _resolve_root()
    info = pp.probe_platform()
    expected = f"{info['os']}/{info['arch']}"

    # ① 显式配置：信任用户，不校验架构（但记录实际情况供排障）
    raw = explicit_path
    if raw is None and config:
        raw = (config.get("quark", {}) or {}).get("kuake_cli", "")
    raw = (raw or "").strip()

    if raw and raw.lower() != "auto":
        if Path(raw).name in _LEGACY_KUAKE_DEFAULTS:
            log_print(
                f"检测到 QUARK_KUAKE_CLI 是历史默认值 '{raw}'，已切换为自动识别",
                "INFO",
            )
        else:
            path = raw if os.path.isabs(raw) else str(root / raw)
            if not os.path.isfile(path):
                return BinaryResolution(
                    source="explicit", expected=expected,
                    reason=f"显式指定的路径不存在：{path}",
                )
            real = pp.detect_binary_arch(path)
            log_print(f"kuake CLI 使用显式配置：{path}（架构={real or '未知'}）", "INFO")
            warn = None
            if real and not pp.arch_matches(real, info):
                warn = (f"显式指定的 kuake 架构为 {real}，与当前平台 "
                        f"{expected} 不一致，可能无法执行")
                log_print(warn, "WARNING")
            return BinaryResolution(path=path, source="explicit",
                                    arch=real, ok=True, warning=warn,
                                    expected=expected)

    # ② 平台精确匹配
    path, arch, reason = _pick(KUAKE_CANDIDATES, root, info, "kuake CLI")
    if path:
        return BinaryResolution(path=path, source="auto-native", arch=arch,
                                ok=True, expected=expected,
                                warning=("pansou 有 android 专用构建但 kuake 没有，"
                                         "在 Android 上将使用 linux 版") 
                                        if info.get("is_android") else None)

    # ③ macOS ARM 特例：回退 darwin-amd64 走 Rosetta
    if info["os"] == "darwin" and info["arch"] == "arm64":
        fallback = root / KUAKE_CANDIDATES[("darwin", "amd64")]
        if fallback.is_file():
            if _rosetta_available():
                log_print(
                    "kuake 没有 macOS ARM 原生版，已回退 darwin-amd64（依赖 Rosetta 2）",
                    "WARNING",
                )
                return BinaryResolution(
                    path=str(fallback), source="auto-rosetta", arch="darwin/amd64",
                    ok=True, expected=expected,
                    warning="无 ARM 原生版，依赖 Rosetta 2 转译运行",
                )
            return BinaryResolution(
                source="missing", expected=expected,
                reason=("macOS ARM 上没有 kuake ARM 原生版，且未安装 Rosetta 2。"
                        "请执行：softwareupdate --install-rosetta"),
            )

    return BinaryResolution(source="missing", expected=expected, reason=reason)


def resolve_pansou_bin(explicit_path=None):
    """为当前平台选择 pansou 可执行文件。"""
    root = _resolve_root()
    info = pp.probe_platform()
    expected = f"{info['os']}/{info['arch']}"

    raw = (explicit_path or "").strip()
    if raw and raw.lower() != "auto":
        path = raw if os.path.isabs(raw) else str(root / raw)
        if not os.path.isfile(path):
            return BinaryResolution(source="explicit", expected=expected,
                                    reason=f"显式指定的路径不存在：{path}")
        real = pp.detect_binary_arch(path)
        return BinaryResolution(path=path, source="explicit", arch=real,
                                ok=True, expected=expected)

    path, arch, reason = _pick(PANSOU_CANDIDATES, root, info, "pansou")
    if path:
        return BinaryResolution(path=path, source="auto-native", arch=arch,
                                ok=True, expected=expected)
    return BinaryResolution(source="missing", expected=expected, reason=reason)


def is_legacy_default(value):
    """供 config 层复用：判断某配置值是否为历史默认值。"""
    return Path((value or "").strip()).name in _LEGACY_KUAKE_DEFAULTS
