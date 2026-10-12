"""引流文件识别。

区分策略：
  ① 精确匹配（最高优先级）：文件名与 EXACT_YINLIU_NAMES 完全相等即判为引流。
                            不走正则、不看体积，用于兜底指定的固定引流文件。
  ② 保守模式（默认）：文件名命中引流关键词（正则包含匹配）才判为引流。
                      体积和后缀仅用于"置信度"提示，不单独触发删除。
  ③ 激进模式：无关键词但"小体积 + PDF/图片后缀"也判为引流。
              可能误伤正经资料里的小文档，谨慎开启。

这样设计的原因：
  - 正经资料里也有 PDF/图片，但文件名不会叫 "微信加我.pdf"
  - 引流文件的核心特征是文件名带引流关键词（微信/QQ/关注/密码/加群…）
  - 部分固定引流文件名太宽泛，只能用精确匹配，避免误伤同名资料
"""
import os
import re


DEFAULT_KEYWORDS = [
    "微信", "vx:", "vx：", "v信", "vxin", "wx:", "wechat",
    "qq群", "加群", "群号", "qq:", "qq：", "qq号",
    "关注", "公众号", "扫码", "二维码", "加好友", "添加好友", "加个好友",
    "福利", "免费领取", "领取", "解压密码", "获取密码", "资源密码",
    "联系", "咨询", "私信", "+v", "+V", "加v", "加V",
    "引流", "推广", "官方店", "官方号",
    "免责声明", "仅供学习", "版权归", "作者微信", "作者qq",
    "点击获取", "点击领取", "网盘群", "资源群",
    "分享后", "关注后", "私信发", "发送", "回复关键词",
    "永久更新", "每天更新", "持续更新",
    "不要白嫖", "白嫖", "支持一下",
]

# 精确匹配名单（2026-10-11 追加）：只有文件名与下列名称「完全相等」时才判为引流。
# 注意：不走正则/关键词包含匹配，避免误伤同名资料
# （如「OPC创作平台使用教程.pdf」不会命中，因为文件名不等于「OPC创作平台.jpg」）。
EXACT_YINLIU_NAMES = [
    "更多资源.txt",
    "流量变现平台.jpg",
    "OPC创作平台.jpg",
    "tg频道.png",
]

DEFAULT_EXTENSIONS = [
    ".txt", ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".bmp", ".doc", ".docx",
]

DEFAULT_MAX_SMALL_FILE_BYTES = 5 * 1024 * 1024


def _compile_patterns(keywords):
    escaped = [re.escape(k) for k in keywords]
    return re.compile("|".join(escaped), re.IGNORECASE)


def match_yinliu(name, size=0,
                 keywords=None, extensions=None,
                 max_small_file_bytes=DEFAULT_MAX_SMALL_FILE_BYTES,
                 extra_excludes=None,
                 aggressive_ext_check=False):
    """判断一个文件是否为引流文件。

    默认保守：只有关键词命中才判引流，后缀+体积单独不触发。

    Args:
        name: 文件名（含或不含路径均可）
        size: 文件大小（字节），0 或 None 视为未知
        keywords: 自定义关键词列表，None 用默认
        extensions: 自定义后缀列表（仅激进模式生效）
        max_small_file_bytes: 小文件阈值
        extra_excludes: 额外排除关键词（如自己的引流 PDF 名）
        aggressive_ext_check: True 启用激进模式（小体积+后缀也删），默认 False

    Returns:
        (is_yinliu, matched_reason)
    """
    if not name:
        return False, ""

    base_name = os.path.basename(name)

    if extra_excludes:
        for excl in extra_excludes:
            if excl and excl in base_name:
                return False, ""

    if keywords is None:
        keywords = DEFAULT_KEYWORDS
    if extensions is None:
        extensions = DEFAULT_EXTENSIONS

    # ① 精确匹配名单：文件名与 EXACT_YINLIU_NAMES 完全相等才判为引流
    #    不走正则、不看体积，精确命中即删。
    if base_name in EXACT_YINLIU_NAMES:
        return True, f"精确匹配 '{base_name}'"

    # ② 关键词包含匹配（正则）
    kw_pattern = _compile_patterns(keywords)
    kw_match = kw_pattern.search(base_name)

    if kw_match:
        reason = f"关键词命中 '{kw_match.group(0)}'"
        return True, reason

    # ③ 激进模式：小体积 + 后缀
    size_val = size or 0
    if aggressive_ext_check:
        ext = os.path.splitext(base_name)[1].lower()
        if ext in extensions and size_val < max_small_file_bytes:
            return True, f"激进模式: 小体积 {ext}（{size_val} bytes）"

    return False, ""
