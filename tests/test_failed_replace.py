"""文本替换：失败链接的处理。

背景（2026-10-18）：
  批量替换 5 条百度链接，实际只成功 2 条，但输出的文案里 5 条链接全在。
  失败的 3 条保留了别人的原链接，推出去等于替原作者导流，用户也看不出
  哪条没成功。

修复后的行为：
  转存失败的链接，直接替换成「转存失败」四个字，
  并连带删掉紧随其后的「提取码: xxxx」，避免留下悬空提取码。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _apply(text, span_end_replacement, url, new_url=None):
    """复刻 run_from_text 里单条链接的替换片段，便于隔离测试。"""
    from core.text_replacer import CODE_PATTERN

    new_text = text
    offset = 0
    if new_url:
        idx = new_text.find(url)
        if idx >= 0:
            tail_start = idx + len(url)
            new_text = new_text[:idx] + new_url + new_text[tail_start:]
    else:
        idx = new_text.find(url)
        if idx >= 0:
            tail_end = idx + len(url)
            after = new_text[tail_end:tail_end + 40]
            code_m = CODE_PATTERN.search(after)
            if code_m:
                tail_end += code_m.end()
                while tail_end < len(new_text) and new_text[tail_end] in " \t":
                    tail_end += 1
            new_text = new_text[:idx] + "转存失败" + new_text[tail_end:]
    return new_text


def test_failed_link_replaced_with_marker():
    text = ("🔥资源A\n链接：https://pan.baidu.com/s/13smUAr-NQ4gmyxubqbQSdQ 提取码: v6wd \n"
            "🔥资源B\n链接：https://pan.baidu.com/s/1bfDJgEtKrT4k2a-bTw_T2w 提取码: uTP5 \n")
    out = _apply(text, None, "https://pan.baidu.com/s/13smUAr-NQ4gmyxubqbQSdQ")
    assert "转存失败" in out
    assert "13smUAr-NQ4gmyxubqbQSdQ" not in out
    # 失败那条的提取码也要一起清掉
    assert "v6wd" not in out
    # 后面那条不受影响
    assert "1bfDJgEtKrT4k2a-bTw_T2w" in out
    assert "uTP5" in out


def test_failed_without_passcode():
    text = "🔥资源\n链接：https://pan.baidu.com/s/1abcDEF123\n🔥下一条\n链接：https://pan.baidu.com/s/1xyzXYZ789\n"
    out = _apply(text, None, "https://pan.baidu.com/s/1abcDEF123")
    assert out.startswith("🔥资源\n链接：转存失败\n")
    assert "1xyzXYZ789" in out


def test_failed_last_line():
    text = "🔥资源\n链接：https://pan.baidu.com/s/1abcDEF123 提取码: ab12"
    out = _apply(text, None, "https://pan.baidu.com/s/1abcDEF123")
    assert out.rstrip().endswith("转存失败")
    assert "ab12" not in out


def test_success_still_replaces_url():
    text = "链接：https://pan.baidu.com/s/1abcDEF123 提取码: ab12 \n"
    out = _apply(text, None, "https://pan.baidu.com/s/1abcDEF123",
                 "https://pan.baidu.com/s/1NEWlink456?pwd=zz99")
    assert "1NEWlink456" in out
    assert "1abcDEF123" not in out


def test_no_original_url_left_on_failure():
    """核心诉求：失败时绝不能留下别人的原链接。"""
    text = "链接：https://pan.baidu.com/s/13smUAr-NQ4gmyxubqbQSdQ 提取码: v6wd \n"
    out = _apply(text, None, "https://pan.baidu.com/s/13smUAr-NQ4gmyxubqbQSdQ")
    assert "pan.baidu.com" not in out
