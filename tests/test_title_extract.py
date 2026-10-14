"""文本替换模式的标题提取测试。

背景（线上真实 bug）
--------------------
用户文案常见「两行式」排版：

    🔥2027《王朝霞•培优100分》（1-6年级上）（数学）（苏教版）
    链接：https://pan.quark.cn/s/2fd51e74aeff

原实现**只看链接所在行**，剥离 URL 后得到「链接」二字，又因为
`origin_title or title` 的优先级把分享接口返回的正确标题覆盖掉，
导致写入飞书的「资源名称」列是「链接」/空。

本文件锁定修复后的两条行为：
1. extract_origin_title 支持向上回溯，能取到上一行的真正标题
2. _pick_display_title **以转存成功显示的名称（api title）为最高优先**，
   且原文字段必须先过噪音判断才允许参与兜底
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.text_replacer import (extract_links, extract_origin_title,
                                _pick_display_title, _is_noise_title,
                                _clean_title_line)

# 用户实际用的两行式文案
TWO_LINE = (
    "🔥2027《王朝霞•培优100分》（1-6年级上）（数学）（苏教版）\n"
    "链接：https://pan.quark.cn/s/2fd51e74aeff\n"
    "\n"
    "🔥2027《王朝霞•创维新课堂》（3-6年级上）（英语）（外研版）\n"
    "链接：https://pan.quark.cn/s/f1a293409286\n"
)

INLINE = "小学资料合集 https://pan.quark.cn/s/aaaaaaaaaaaa"
BLANK_BETWEEN = (
    "标题在上面\n"
    "\n"
    "链接：https://pan.quark.cn/s/bbbbbbbbbbbb\n"
)


def _first_origin(text):
    links = extract_links(text)
    assert links, "应至少识别出一个链接"
    lk = links[0]
    return extract_origin_title(text, lk["span"], lk["url"])


class TestOriginTitle(unittest.TestCase):
    def test_two_line_layout_looks_up(self):
        """核心场景：标题在上一行、链接独占一行。"""
        self.assertEqual(
            _first_origin(TWO_LINE),
            "2027《王朝霞•培优100分》（1-6年级上）（数学）（苏教版）")

    def test_second_link_gets_its_own_title(self):
        """多个链接各自取到对应标题，不能串行。"""
        links = extract_links(TWO_LINE)
        t2 = extract_origin_title(TWO_LINE, links[1]["span"], links[1]["url"])
        self.assertIn("创维新课堂", t2)
        self.assertIn("外研版", t2)

    def test_inline_layout(self):
        self.assertEqual(_first_origin(INLINE), "小学资料合集")

    def test_blank_line_in_between_still_found(self):
        self.assertEqual(_first_origin(BLANK_BETWEEN), "标题在上面")

    def test_never_returns_link_noise(self):
        """绝不允许把「链接」这类引导词当成标题返回。"""
        for text in (TWO_LINE, INLINE, BLANK_BETWEEN):
            got = _first_origin(text)
            self.assertNotIn("链接", got, f"不应含引导词: {got!r}")
            self.assertNotEqual(got, "链接")

    def test_emoji_stripped(self):
        got = _first_origin(TWO_LINE)
        self.assertFalse(got.startswith("🔥"))

    def test_no_title_at_all_returns_empty(self):
        text = "链接：https://pan.quark.cn/s/cccccccccccc"
        self.assertEqual(_first_origin(text), "")


class TestNoiseDetection(unittest.TestCase):
    def test_known_noise_words(self):
        for w in ("链接", "下载链接", "地址：", "提取码", "点击", "url", "link"):
            self.assertTrue(_is_noise_title(_clean_title_line(w)),
                            f"{w!r} 应判为噪音")

    def test_real_titles_pass(self):
        for w in ("2027《王朝霞•培优100分》", "小学英语资料", "Movies 2024"):
            self.assertFalse(_is_noise_title(_clean_title_line(w)),
                             f"{w!r} 不应判为噪音")


class TestDisplayTitlePriority(unittest.TestCase):
    """写入飞书的资源名称优先级。"""

    def test_api_title_wins(self):
        """转存成功显示的名称优先——这是用户明确要求的。"""
        self.assertEqual(_pick_display_title("原文标题A", "分享接口标题B", 1),
                         "分享接口标题B")

    def test_origin_used_when_api_empty(self):
        self.assertEqual(_pick_display_title("原文标题A", "", 1), "原文标题A")
        self.assertEqual(_pick_display_title("原文标题A", "unknown", 1),
                         "原文标题A")

    def test_noise_origin_does_not_shadow_api(self):
        """原 bug 复现点：origin=「链接」时必须让位给 api title。"""
        self.assertEqual(_pick_display_title("链接", "分享标题B", 1),
                         "分享标题B")
        self.assertEqual(_pick_display_title("地址：", "分享标题B", 1),
                         "分享标题B")

    def test_fallback_when_both_missing(self):
        self.assertEqual(_pick_display_title("", "", 7), "资源7")
        self.assertEqual(_pick_display_title("链接", None, 3), "资源3")

    def test_never_empty(self):
        for a, b, i in [("", "", 1), ("链接", "", 2), (None, None, 5)]:
            self.assertTrue(_pick_display_title(a, b, i).strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
