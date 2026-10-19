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
    # ── 2026-10-14 追加：实测漏网的高频引流词 ──────────────────────
    # 案例：转存「王朝霞」教辅时目录里残留
    #   「夸克资料分享群E.png」「资料导航库在线表格（每日更新）.jpg」
    # 原关键词库只认「QQ群/加群/网盘群/资源群」，漏掉了这些变体。
    "分享群", "资料群", "导航库", "资料导航", "在线表格",
    "资料库", "资源库", "更多资料", "更多资源", "获取资料",
    "群文件", "进群", "入群", "交流群", "学习群", "粉丝群",
    "客服", "小编", "站长", "整理不易", "整理分享",
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

# ── 自己的联系方式（2026-10-18 追加）──────────────────────────────
# 场景：转存来的资料，文件名末尾常挂原作者的引流尾巴，例如
#     43 《风筝》【更多好课加微信z13492263】.pdf
# 这些是正课，不能删。处理方式是「把别人的号改成自己的号」：
#     43 《风筝》【更多好课加微信kds3566】.pdf
# 既保住内容，又把流量导到自己这里。
DEFAULT_MY_CONTACT = "kds3566"

# 匹配「微信/vx/QQ + 账号」。(?:号|群)? 用于吃掉「微信号」「QQ群」里的字；
# 账号部分限定为字母数字串，因此「微信公众号」「微信支付」这类中文词组不会误匹配。
_CONTACT_RE = re.compile(
    r"((?:微信|wechat|vxin|v信|vx|wx|扣扣|qq)\s*(?:号|群)?\s*[:：=]?\s*)"
    r"([A-Za-z0-9][A-Za-z0-9_-]{4,19})",
    re.IGNORECASE)

# ── 广告尾巴剥离 ────────────────────────────────────────────────
# 事故背景：转存「邵鑫合集」时一次误删 173 个正课文件，原因就是整个文件名
# 直接做关键词匹配，命中「微信」后把整节课删掉了。
# 新逻辑：先摘掉被【】[]（）包裹的片段，只在剥离后的主体上判定引流。
# 只认【】[]（）() 四类包裹符 —— 书名号《》是正课标题常用符号，
# 若当包裹符会把《风筝》这类标题本身摘掉。
_AD_WRAPPER_RE = re.compile(r"[【\[（(]([^【\[\]（）()]{1,60})[】\]）)]")

# 包裹片段里出现这些词 → 认为该片段是广告尾巴
_AD_WRAPPER_HINT_RE = re.compile(
    r"(微信|wechat|vx|v信|vxin|qq|加群|群号|二维码|扫码|关注|公众号|"
    r"领取|福利|免费|更多好课|更多资源|更多资料|加好友|添加好友|联系|咨询|"
    r"私信|密码|进群|入群|客服|小编|站长|资料|资源)",
    re.IGNORECASE)


def _compile_patterns(keywords):
    escaped = [re.escape(k) for k in keywords]
    return re.compile("|".join(escaped), re.IGNORECASE)


def strip_ad_wrappers(name):
    """剥离文件名里被【】[]（）包裹的片段。

    Returns:
        (主体名, 被剥离的片段列表)
    """
    if not name:
        return "", []
    segs = _AD_WRAPPER_RE.findall(name)
    main = _AD_WRAPPER_RE.sub("", name)
    return main, segs


def rewrite_contact(name, my_contact=None):
    """把文件名里的他人联系方式替换成自己的号。

    只改「微信/vx/QQ 后面那个账号串」，其余文字原样保留，
    因此正课标题（如「43 《风筝》」）不会被动到。

    Args:
        name: 文件名或路径
        my_contact: 自己的号，None 则用 DEFAULT_MY_CONTACT

    Returns:
        改写后的 basename；无需改写（没匹配到 / 已经是自己的号）则返回 None
    """
    if not name:
        return None
    my = (my_contact or DEFAULT_MY_CONTACT or "").strip()
    if not my:
        return None
    base = os.path.basename(name)

    def _sub(m):
        prefix, account = m.group(1), m.group(2)
        if account == my:
            return m.group(0)
        return prefix + my

    new_base, n = _CONTACT_RE.subn(_sub, base)
    if n == 0 or new_base == base:
        return None
    return new_base


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

    # ② 关键词匹配：先在「剥离广告包裹后的主体」上判定。
    #    主体干净、只有包裹片段像广告的（43 《风筝》【更多好课加微信xxx】），
    #    判为「正课 + 广告尾巴」→ 不删，交给 rename 把号改成自己的。
    kw_pattern = _compile_patterns(keywords)
    # 先去掉扩展名再剥离包裹片段：
    # 【加微信xxx领资料】.png 若带着扩展名一起剥离，主体会剩 ".png"
    # （splitext 对 ".png" 返回 (".png","")，判不出空），纯广告文件就漏网了。
    base_root, _ = os.path.splitext(base_name)
    main_name, segs = strip_ad_wrappers(base_root)
    kw_match_main = kw_pattern.search(main_name or "")

    if kw_match_main:
        return True, f"关键词命中 '{kw_match_main.group(0)}'"

    if segs and main_name.strip():
        if any(_AD_WRAPPER_HINT_RE.search(s) for s in segs):
            return False, ""

    # ③ 兜底：对完整文件名匹配（无包裹的、或剥完为空的纯广告名）
    kw_match = kw_pattern.search(base_name)

    if kw_match:
        reason = f"关键词命中 '{kw_match.group(0)}'"
        return True, reason

    # ④ 激进模式：小体积 + 后缀
    size_val = size or 0
    if aggressive_ext_check:
        ext = os.path.splitext(base_name)[1].lower()
        if ext in extensions and size_val < max_small_file_bytes:
            return True, f"激进模式: 小体积 {ext}（{size_val} bytes）"

    return False, ""
