"""引流文件清理测试。

两块内容：
1. 关键词规则（yinliu_filter）—— 重点锁定 2026-10-14 追加的高频漏网词
2. 文本替换模式的清理接入（text_replacer）—— 确保转存后会调用清理，
   且失败时不阻断主流程
"""
import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.yinliu_filter import match_yinliu, DEFAULT_KEYWORDS
from core import text_replacer as tr


class TestKeywordRules(unittest.TestCase):
    """实测漏网的真实文件名，必须判为引流。"""

    YINLIU_SAMPLES = [
        ("学习资料QQ群4.jpg", "QQ群"),
        ("夸克资料分享群E.png", "分享群"),
        ("资料导航库在线表格（每日更新）.jpg", "资料导航"),
        ("更多资源.txt", "更多资源"),
        ("进群领取.png", "进群"),
        ("客服微信.jpg", "客服"),
    ]

    MUST_KEEP = [
        "99999.pdf",
        "数学1年级上册SJ版.pdf",
        "数学6年级上册SJ版.pdf",
        "2027《王朝霞•培优100分》（1-6年级上）（数学）（苏教版）",
        "Just Dance 2021.nsp",
    ]

    def test_yinliu_detected(self):
        for name, _ in self.YINLIU_SAMPLES:
            ok, why = match_yinliu(name, 100 * 1024)
            self.assertTrue(ok, f"{name!r} 应判为引流（未命中）")

    def test_normal_files_kept(self):
        """不能误伤正常资料。"""
        for name in self.MUST_KEEP:
            ok, why = match_yinliu(name, 100 * 1024)
            self.assertFalse(ok, f"{name!r} 被误判为引流: {why}")

    def test_added_keywords_present(self):
        for kw in ("分享群", "资料群", "导航库", "资料导航", "在线表格",
                   "资料库", "资源库", "进群", "客服"):
            self.assertIn(kw, DEFAULT_KEYWORDS, f"缺少关键词 {kw!r}")

    def test_small_math_pdf_not_flagged(self):
        """小体积 PDF 在保守模式下不应仅因后缀被删。"""
        ok, _ = match_yinliu("数学3年级上册SJ版.pdf", 200 * 1024)
        self.assertFalse(ok)


class TestCleanupIntegration(unittest.TestCase):
    """文本替换模式的清理接入。"""

    def setUp(self):
        self.src = inspect.getsource(tr._save_and_share_quark)

    def test_cleanup_is_called(self):
        self.assertIn("clean_yinliu_after_save", self.src)

    def test_paths_resolved_before_cleanup(self):
        self.assertIn("_resolve_top_fids_to_paths", self.src)

    def test_own_pdf_excluded(self):
        """必须排除自己的引流 PDF，否则 99999.pdf 会被删掉。"""
        self.assertIn("cleanup_excludes", self.src)
        self.assertIn("splitext", self.src)

    def test_respects_enabled_flag(self):
        self.assertIn('filter_cfg.get("enabled")', self.src)

    def test_cleanup_failure_does_not_raise(self):
        """清理异常必须被吞掉，不能阻断转存/分享。"""
        self.assertIn("引流清理异常", self.src)

    def test_order_cleanup_before_share(self):
        """清理要在 share_run 之前——否则分享出去的还是带引流的目录。"""
        i_clean = self.src.find("clean_yinliu_after_save")
        i_share = self.src.find("manager.share_run")
        self.assertGreater(i_clean, 0)
        self.assertGreater(i_share, 0)
        self.assertLess(i_clean, i_share, "清理应早于分享")

    def test_pdf_upload_after_cleanup(self):
        """PDF 上传（调用点）在清理之后。

        注意：函数名第一次出现是在 import 语句里，必须找**调用点**而非
        任意出现位置——所以从 `if pdf_path` 之后开始搜。
        """
        i_clean = self.src.find("clean_yinliu_after_save(")
        i_upload = self.src.find("_upload_yinliu_pdf(", self.src.find("if pdf_path"))
        self.assertGreater(i_clean, 0)
        self.assertGreater(i_upload, 0)
        self.assertLess(i_clean, i_upload, "PDF 上传应在清理之后")


if __name__ == "__main__":
    unittest.main(verbosity=2)
