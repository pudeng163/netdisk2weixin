"""统一日志工具。

提供 log_print()，供适配器统一输出带时间戳的日志。
"""
import os
from datetime import datetime
from .config import LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = str(LOG_DIR / "run.log")


def log_print(message, level="INFO"):
    """打印并写入日志文件。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    safe = str(message).encode("utf-8", errors="replace").decode("utf-8")
    try:
        print(safe)
    except Exception:
        try:
            import sys
            sys.stdout.buffer.write((safe + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()
        except Exception:
            pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return message