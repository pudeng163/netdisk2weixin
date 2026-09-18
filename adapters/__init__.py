"""网盘适配器统一接口。

每个适配器应实现 run(**kwargs)，返回统一结构：
{
    "code": 200 / 500 / ...,
    "message": str,
    "total_selected": int,
    "save_success_count": int,
    "share_success_count": int,
    "share_results": [{"note": ..., "share_url": ...}, ...],
    "wechat_result": {"success": ..., "message": ...} | None,
    "preview": bool,        # 是否仅预览
    "items": list | None,   # 预览时的资源列表
}
"""
from .baidu import run as run_baidu
from .quark import run as run_quark

ADAPTERS = {
    "baidu": run_baidu,
    "quark": run_quark,
}


def get_adapter(platform: str):
    """根据平台名返回适配器的 run 函数。"""
    if platform not in ADAPTERS:
        raise ValueError(f"未知平台: {platform}，可用: {list(ADAPTERS)}")
    return ADAPTERS[platform]