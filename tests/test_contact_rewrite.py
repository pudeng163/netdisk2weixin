"""测试「他人联系方式改写」与「广告尾巴不误删」逻辑。

背景（2026-10-18 事故）：
  转存「邵鑫合集」时，文件名 43 《风筝》【更多好课加微信z13492263】.pdf
  被判定为引流文件直接删除，一次误删 173 个正课文件。

根因：
  match_yinliu 对整个文件名做关键词匹配，「微信」命中后整节课被删。
  这些其实是正课，只是文件名末尾挂了原作者的广告尾巴。

修复后的正确行为：
  ① 名字里带他人联系方式 → 改名换成自己的号，**不删**
  ② 关键词判定前先剥离【】[]（）包裹片段，只在主体上判定
  ③ 真正的独立引流文件（分享群二维码等）照常删除

Mock 层级说明：
  本文件只测纯函数（rewrite_contact / match_yinliu），不 mock 网络。
  改名动作（_kuake_rename）的集成测试在 test_yinliu_clean.py 里。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.yinliu_filter import match_yinliu, rewrite_contact, strip_ad_wrappers

MY = "kds3566"


# ---------------------------------------------------------------------------
# rewrite_contact：把别人的号换成自己的
# ---------------------------------------------------------------------------

def test_rewrite_basic():
    """最典型的正课 + 广告尾巴。"""
    src = '43 《风筝》【更多好课加微信z13492263】.pdf'
    assert rewrite_contact(src, MY) == '43 《风筝》【更多好课加微信kds3566】.pdf'


def test_rewrite_keeps_title_intact():
    """只改账号，正课标题一字不动。"""
    src = '00  发刊词：攻破语文"重难点"【更多好课加微信z13492263】.m4a'
    out = rewrite_contact(src, MY)
    assert out is not None
    assert '发刊词' in out
    assert 'z13492263' not in out
    assert 'kds3566' in out


def test_rewrite_is_idempotent():
    """已经是自己的号时返回 None，避免每次跑都重复 rename。"""
    src = '43 《风筝》【更多好课加微信kds3566】.pdf'
    assert rewrite_contact(src, MY) is None


def test_rewrite_no_contact_returns_none():
    """文件名里没有联系方式 → 不改。"""
    for name in ['史记名篇123期', '【完结】邵鑫【红楼梦】', '99999.pdf',
                 '邵鑫精讲《孙子兵法》']:
        assert rewrite_contact(name, MY) is None


def test_rewrite_not_fooled_by_chinese_words():
    """「微信公众号」「微信支付」这类中文词组不该被当成账号。"""
    for name in ['微信公众号介绍.pdf', '微信支付说明.png', '课程目录.docx']:
        assert rewrite_contact(name, MY) is None


def test_rewrite_qq_number():
    """QQ 号同样替换。"""
    src = '资料目录【QQ群123456789】.pdf'
    out = rewrite_contact(src, MY)
    assert out is not None
    assert 'kds3566' in out
    assert '123456789' not in out


def test_rewrite_vx_variant():
    """vx / VX 变体。"""
    src = '课件【加vx：ABCdef123】.pdf'
    out = rewrite_contact(src, MY)
    assert out is not None
    assert 'ABCdef123' not in out
    assert 'kds3566' in out


def test_rewrite_default_contact():
    """不传 my_contact 时用 DEFAULT_MY_CONTACT。"""
    src = '01【更多好课加微信z13492263】.pdf'
    assert 'kds3566' in (rewrite_contact(src) or '')


# ---------------------------------------------------------------------------
# strip_ad_wrappers：剥离包裹片段
# ---------------------------------------------------------------------------

def test_strip_removes_bracket_segments():
    main, segs = strip_ad_wrappers('43 《风筝》【更多好课加微信z13492263】.pdf')
    assert 'z13492263' not in main
    assert '《风筝》' in main
    assert any('微信' in s for s in segs)


def test_strip_keeps_book_title_marks():
    """《》是书名号不是包裹符，不能被当成广告片段摘掉。"""
    main, _ = strip_ad_wrappers('43 《风筝》【更多好课加微信z13492263】.pdf')
    assert '《风筝》' in main

    # 对照：【】包裹的片段才会被剥离
    main2, _ = strip_ad_wrappers('【完结】邵鑫【红楼梦】')
    assert main2 == '邵鑫'


def test_strip_no_wrapper():
    main, segs = strip_ad_wrappers('夸克资料分享群E.png')
    assert main == '夸克资料分享群E.png'
    assert segs == []


# ---------------------------------------------------------------------------
# match_yinliu：正课不误删、真引流照删
# ---------------------------------------------------------------------------

def test_course_with_ad_tail_not_deleted():
    """带广告尾巴的正课必须保留（这是事故的修复点）。"""
    for name in ['43 《风筝》【更多好课加微信z13492263】.pdf',
                 '01 写作课【更多好课加微信z13492263】.m4a',
                 '00 发刊词【加微信z13492263领取】.pdf']:
        is_yl, _ = match_yinliu(name)
        assert is_yl is False, f"正课被误判为引流: {name}"


def test_real_yinliu_still_deleted():
    """独立的引流文件照常删除。"""
    for name in ['夸克资料分享群E.png', '学习资料QQ群4.jpg',
                 '资料导航库在线表格（每日更新）.jpg']:
        is_yl, _ = match_yinliu(name)
        assert is_yl is True, f"真引流文件漏判: {name}"


def test_pure_ad_name_deleted():
    """整个文件名就是广告（剥完主体为空）→ 删。"""
    is_yl, _ = match_yinliu('【加微信z13492263领资料】.png')
    assert is_yl is True


def test_own_pdf_never_deleted():
    """自己的引流 PDF 不删。"""
    is_yl, _ = match_yinliu('99999.pdf', extra_excludes=['99999'])
    assert is_yl is False


def test_normal_course_untouched():
    """普通课程名不受影响。"""
    for name in ['史记名篇123期', '【完结】邵鑫【红楼梦】',
                 '邵鑫名家散文精读-第4季', '语文六大版块学习方法全解【邵鑫】']:
        is_yl, _ = match_yinliu(name)
        assert is_yl is False, f"正常课程被误判: {name}"


# ---------------------------------------------------------------------------
# 组合：改名后的文件名不会被下一轮判定成引流
# ---------------------------------------------------------------------------

def test_rewritten_name_survives_next_scan():
    """改成自己的号后再跑一次清理，不能被删掉。"""
    src = '43 《风筝》【更多好课加微信z13492263】.pdf'
    new = rewrite_contact(src, MY)
    is_yl, _ = match_yinliu(new)
    assert is_yl is False
    # 且幂等：第二次不再改写
    assert rewrite_contact(new, MY) is None
