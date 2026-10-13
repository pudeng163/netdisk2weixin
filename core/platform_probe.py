"""跨平台探测：判定当前操作系统、CPU 架构，并校验二进制文件的真实架构。

本模块只依赖标准库，不做任何网络或配置读取，便于被任何层引用。

两个核心能力：
1. probe_platform()      —— 当前跑在什么系统/架构上（含 Android 识别）
2. detect_binary_arch()  —— 给定二进制文件，读出它真实的 目标平台/架构

设计要点：**架构校验必须靠 magic bytes 而不是文件名**。
文件名是人为约定的，可能被改错；而 ELF/Mach-O/PE 的文件头不会说谎。
在 Linux ARM 上误选 x86-64 二进制时，执行会报 `Exec format error`，
这类错误极难排查，因此在选择阶段就拦下来。
"""
import os
import sys
import struct
from pathlib import Path

# ---------------------------------------------------------------------------
# 架构归一化
# ---------------------------------------------------------------------------
# platform.machine() 在不同系统上返回五花八门的值，统一归一到
# Go 风格的命名（amd64 / arm64 / arm32 / 386），因为二进制文件名用的就是这套。
_ARCH_ALIASES = {
    # x86-64
    "x86_64": "amd64",
    "x86-64": "amd64",
    "amd64": "amd64",
    "x64": "amd64",
    "em64t": "amd64",
    # ARM 64
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv8": "arm64",
    "armv8l": "arm64",   # 常见于 32 位内核跑 64 位用户态，按 arm64 处理
    "armv9": "arm64",
    # ARM 32
    "armv7l": "arm32",
    "armv6l": "arm32",
    "armv5tel": "arm32",
    "armhf": "arm32",
    "arm32": "arm32",
    # x86 32
    "i386": "386",
    "i486": "386",
    "i586": "386",
    "i686": "386",
    "x86": "386",
}

# ELF e_machine（offset 18，小端 u16）
_ELF_MACHINE = {0x3E: "amd64", 0xB7: "arm64", 0x28: "arm32", 0x03: "386"}
# Mach-O cputype（offset 4，小端 u32）
_MACHO_CPUTYPE = {0x01000007: "amd64", 0x0100000C: "arm64"}
# PE COFF machine
_PE_MACHINE = {0x8664: "amd64", 0xAA64: "arm64", 0x014C: "386", 0x01C4: "arm32"}

_ARCH_CACHE = {}


def normalize_arch(raw):
    """把 platform.machine() 的原始值归一为 amd64/arm64/arm32/386 等。

    无法识别的值原样返回（小写），交由上层在选择候选时判定失败。
    """
    if not raw:
        return ""
    return _ARCH_ALIASES.get(raw.strip().lower(), raw.strip().lower())


def probe_platform():
    """探测当前运行环境。

    返回 dict：
        os           'windows' | 'linux' | 'darwin' | 其他 sys.platform 值
        arch         'amd64' | 'arm64' | 'arm32' | '386' | 原始值
        is_android   bool，是否跑在 Android 上（Termux / proot / 真机）
        raw_machine  platform.machine() 原始返回，排障用
        raw_platform sys.platform 原始返回，排障用
    """
    import platform as _pf

    raw_platform = sys.platform
    raw_machine = _pf.machine() or ""

    if raw_platform.startswith(("win", "cygwin", "msys")):
        os_name = "windows"
    elif raw_platform == "darwin":
        os_name = "darwin"
    elif raw_platform.startswith("linux"):
        os_name = "linux"
    else:
        os_name = raw_platform

    return {
        "os": os_name,
        "arch": normalize_arch(raw_machine),
        "is_android": is_android(),
        "raw_machine": raw_machine,
        "raw_platform": raw_platform,
    }


def is_android():
    """判定是否运行在 Android 环境（Termux 原生、proot 容器、或真机）。

    多个判据任一命中即为真：
    1. Termux 的 PREFIX 环境变量
    2. ANDROID_ROOT / ANDROID_DATA 环境变量
    3. /system/build.prop 存在
    4. /proc/version 里含 'android'（proot 容器可能丢环境变量，靠这个兜底）
    """
    prefix = os.environ.get("PREFIX", "")
    if prefix.startswith("/data/data/com.termux"):
        return True
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA"):
        return True
    if Path("/system/build.prop").exists():
        return True
    # proot/Termux 兜底：内核版本串里通常带 android
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as f:
            if "android" in f.read(512).lower():
                return True
    except OSError:
        pass
    return False


def is_executable(path):
    """文件存在且当前用户可执行。

    Windows 上没有 POSIX 执行位概念，退化为「文件存在」。
    """
    if not path or not os.path.isfile(path):
        return False
    if os.name == "nt":
        return True
    return os.access(path, os.X_OK)


def detect_binary_arch(path):
    """读取二进制文件头，返回它的真实 'os/arch' 描述。

    返回形如 'linux/amd64'、'darwin/arm64'、'windows/amd64'。
    架构无法识别时返回 None（调用方应视为校验不通过）。

    注意：Android 的 ELF 与 Linux 的 ELF 在文件头上**完全相同**，
    无法从 magic bytes 区分。本函数对 android-arm64 会返回 'linux/arm64'，
    Android 的区分只能靠 probe_platform()['is_android'] 在选文件时进行。
    """
    if not path or not os.path.isfile(path):
        return None
    if path in _ARCH_CACHE:
        return _ARCH_CACHE[path]

    result = None
    try:
        with open(path, "rb") as f:
            head = f.read(64)

        if len(head) < 20:
            result = None
        elif head[:4] == b"\x7fELF":
            # ELF：e_machine 在 offset 18
            machine = struct.unpack_from("<H", head, 18)[0]
            arch = _ELF_MACHINE.get(machine)
            result = f"linux/{arch}" if arch else None
        elif head[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            # Mach-O 64（LE / BE）
            cputype = struct.unpack_from("<I", head, 4)[0]
            arch = _MACHO_CPUTYPE.get(cputype)
            result = f"darwin/{arch}" if arch else None
        elif head[:2] == b"MZ":
            # PE/EXE：解析 e_lfanew 找到 COFF 头
            e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
            with open(path, "rb") as f:
                f.seek(e_lfanew)
                sig = f.read(6)
                if len(sig) >= 6 and sig[:4] == b"PE\0\0":
                    machine = struct.unpack_from("<H", sig, 4)[0]
                    arch = _PE_MACHINE.get(machine)
                    result = f"windows/{arch}" if arch else None
    except OSError:
        result = None

    _ARCH_CACHE[path] = result
    return result


def arch_matches(binary_arch, platform_info):
    """判断二进制的 'os/arch' 是否匹配当前平台。

    Android 特例：android-arm64 的 ELF 被 detect_binary_arch 报为
    'linux/arm64'，所以在 Android 上允许 linux/* 的 ELF 通过校验。
    """
    if not binary_arch:
        return False
    bos, _, barch = binary_arch.partition("/")
    pos = platform_info["os"]
    parch = platform_info["arch"]

    if bos != pos:
        # Android 上跑的是 Linux ELF，允许
        if not (platform_info.get("is_android") and bos == "linux"):
            return False
    return barch == parch


def describe_platform(info=None):
    """生成一行人类可读的平台描述，用于日志和诊断输出。"""
    info = info or probe_platform()
    s = f"{info['os']}/{info['arch']}"
    if info.get("is_android"):
        s += " (android)"
    if info["raw_machine"] and normalize_arch(info["raw_machine"]) != info["raw_machine"]:
        s += f" [machine={info['raw_machine']}]"
    return s
