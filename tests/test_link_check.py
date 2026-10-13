"""链接有效性预检的单元测试。

覆盖：
- 三态语义（ok/bad/uncertain），其中 uncertain 必须放行
- 缓存命中不重复请求
- PanSou 不可用时 fail-open（不抛异常、全部放行）
- 死链文件读写与过滤
- _collect_valid_items 的跳过/顺延/绕回/扩容行为

注意 mock 层级
--------------
`check_links` 内部含三层：HTTP(`_post_check`) → 状态归一化 → 缓存。
测试必须 mock **最底层的 `_post_check`**，否则会绕过缓存与兜底逻辑，
测出来的行为跟线上不一致（这一点最初就踩了坑）。
"""
import os
import sys
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import pansou_check as pc
from adapters import quark as Q


def _url(i):
    return f"https://pan.quark.cn/s/{i:012x}"


def _item(i):
    return {"note": f"资源{i}", "url": _url(i)}


def _idx_of(url):
    return int(url.rsplit("/", 1)[1], 16)


class FakeCheck:
    """在 **_post_check 层**拦截，保留上层的缓存与状态归一化逻辑。

    Args:
        bad_fn: 给定 url 返回 True 表示被判为失效
        raw_fn: 直接返回原始 results 列表（用于测异常状态/结构）
    """

    def __init__(self, bad_fn=None, raw_fn=None):
        self.calls = []
        self.bad_fn = bad_fn or (lambda u: False)
        self.raw_fn = raw_fn
        self._patcher = None

    def __enter__(self):
        def fake_post(urls, disk_type="quark"):
            self.calls.append(list(urls))
            if self.raw_fn is not None:
                return self.raw_fn(urls)
            return [
                {"url": u,
                 "state": "bad" if self.bad_fn(u) else "ok",
                 "summary": "分享不存在" if self.bad_fn(u) else "链接有效"}
                for u in urls if u
            ]

        self._patcher = mock.patch.object(pc, "_post_check", side_effect=fake_post)
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


class TestStates(unittest.TestCase):
    def test_only_bad_is_unusable(self):
        self.assertTrue(pc.is_usable(pc.STATE_OK))
        self.assertTrue(pc.is_usable(pc.STATE_UNCERTAIN),
                        "uncertain 往往是限流，必须放行")
        self.assertFalse(pc.is_usable(pc.STATE_BAD))


class TestCheckLinks(unittest.TestCase):
    def setUp(self):
        pc._MEM_CACHE.clear()
        # 隔离磁盘缓存，避免用例相互污染
        self._p1 = mock.patch.object(pc, "_load_disk_cache", return_value={})
        self._p2 = mock.patch.object(pc, "_save_disk_cache")
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        pc._MEM_CACHE.clear()

    def test_basic_ok_and_bad(self):
        with FakeCheck(bad_fn=lambda u: _idx_of(u) == 2) as f:
            r = pc.check_links([_url(1), _url(2)], use_cache=False)
        self.assertEqual(r[_url(1)][0], pc.STATE_OK)
        self.assertEqual(r[_url(2)][0], pc.STATE_BAD)
        self.assertEqual(len(f.calls), 1)

    def test_cache_avoids_second_request(self):
        with FakeCheck() as f:
            pc.check_links([_url(1)], use_cache=True)
            self.assertEqual(len(f.calls), 1)
            f.calls.clear()
            r2 = pc.check_links([_url(1)], use_cache=True)
            self.assertEqual(len(f.calls), 0, "第二次应命中内存缓存，不再发请求")
        self.assertEqual(r2[_url(1)][0], pc.STATE_OK)

    def test_unavailable_is_fail_open(self):
        with mock.patch.object(pc, "_post_check", return_value=None):
            r = pc.check_links([_url(1), _url(2)], use_cache=False)
        self.assertEqual(r, {}, "接口不可用时返回空 → 调用方全部放行")

    def test_timeout_does_not_raise(self):
        with mock.patch.object(pc, "_post_check",
                               side_effect=Exception("boom")):
            # _post_check 内部已吞掉异常返回 None，这里再兜一层
            try:
                r = pc.check_links([_url(1)], use_cache=False)
            except Exception as e:  # pragma: no cover
                self.fail(f"预检不应向上抛异常: {e}")
        self.assertEqual(r, {})

    def test_unknown_state_treated_as_uncertain(self):
        def raw(urls):
            return [{"url": urls[0], "state": "weird-state", "summary": "???"}]
        with FakeCheck(raw_fn=raw):
            r = pc.check_links([_url(1)], use_cache=False)
        self.assertEqual(r[_url(1)][0], pc.STATE_UNCERTAIN)
        self.assertTrue(pc.is_usable(r[_url(1)][0]))

    def test_normalized_url_fallback(self):
        """PanSou 可能只回 normalized_url，要能兜底匹配上。"""
        def raw(urls):
            return [{"normalized_url": urls[0], "state": "ok", "summary": "链接有效"}]
        with FakeCheck(raw_fn=raw):
            r = pc.check_links([_url(3)], use_cache=False)
        self.assertEqual(r[_url(3)][0], pc.STATE_OK)

    def test_batch_size_is_respected(self):
        with FakeCheck() as f:
            with mock.patch.object(pc, "get_check_batch_size", return_value=50):
                pc.check_links([_url(i) for i in range(120)], use_cache=False)
        self.assertEqual([len(c) for c in f.calls], [50, 50, 20])

    def test_missing_result_is_not_in_output(self):
        """上游没回某条 → 该条不出现在结果里 → 调用方按放行处理。"""
        def raw(urls):
            return [{"url": urls[0], "state": "ok", "summary": ""}]
        with FakeCheck(raw_fn=raw):
            r = pc.check_links([_url(1), _url(2)], use_cache=False)
        self.assertIn(_url(1), r)
        self.assertNotIn(_url(2), r)


class TestDeadLinks(unittest.TestCase):
    def test_roundtrip_parses_url_not_note(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "dead_links.txt"
            with mock.patch.object(pc, "dead_links_file", return_value=fake):
                pc.record_dead_links([_item(1), _item(2)])
                got = pc.load_dead_links()
            self.assertEqual(got, {_url(1), _url(2)},
                             "必须解析出 URL 而非 note")

    def test_filter_removes_dead(self):
        items = [_item(1), _item(2), _item(3)]
        alive = pc.filter_dead(items, {_url(2)})
        self.assertEqual([i["url"] for i in alive], [_url(1), _url(3)])

    def test_plain_url_file_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "dead_links.txt"
            fake.write_text(f"# 注释\n{_url(9)}\n", encoding="utf-8")
            with mock.patch.object(pc, "dead_links_file", return_value=fake):
                got = pc.load_dead_links()
            self.assertEqual(got, {_url(9)})


class TestCollectValidItems(unittest.TestCase):
    def setUp(self):
        pc._MEM_CACHE.clear()
        self._p1 = mock.patch.object(pc, "_load_disk_cache", return_value={})
        self._p2 = mock.patch.object(pc, "_save_disk_cache")
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        pc._MEM_CACHE.clear()

    def test_no_check_returns_sequential(self):
        pool = [_item(i) for i in range(10)]
        sel, dead = Q._collect_valid_items(pool, 0, 3, check_enabled=False)
        self.assertEqual([x["note"] for x in sel], ["资源0", "资源1", "资源2"])
        self.assertEqual(dead, [])

    def test_wraps_around(self):
        pool = [_item(i) for i in range(10)]
        sel, _ = Q._collect_valid_items(pool, 8, 3, check_enabled=False)
        self.assertEqual([x["note"] for x in sel], ["资源8", "资源9", "资源0"])

    def test_skips_dead_and_extends(self):
        pool = [_item(i) for i in range(20)]
        with FakeCheck(bad_fn=lambda u: _idx_of(u) % 2 == 0):
            sel, dead = Q._collect_valid_items(pool, 0, 3, check_enabled=True)
        self.assertEqual([x["note"] for x in sel], ["资源1", "资源3", "资源5"])
        self.assertGreater(len(dead), 0)

    def test_uncertain_items_are_kept(self):
        pool = [_item(i) for i in range(10)]

        def raw(urls):
            return [{"url": u, "state": "uncertain", "summary": "请求过于频繁"}
                    for u in urls]

        with FakeCheck(raw_fn=raw):
            sel, dead = Q._collect_valid_items(pool, 0, 3, check_enabled=True)
        self.assertEqual(len(sel), 3, "uncertain 不应被跳过")
        self.assertEqual(dead, [])

    def test_window_doubles_when_needed(self):
        pool = [_item(i) for i in range(200)]
        with FakeCheck(bad_fn=lambda u: _idx_of(u) % 5 != 0) as f:
            sel, _ = Q._collect_valid_items(pool, 0, 4, check_enabled=True)
        self.assertEqual(len(sel), 4)
        self.assertGreaterEqual(len(f.calls), 2, "死链率高时应分批续取")
        self.assertGreater(f.calls[1], f.calls[0], "第二批窗口应比第一批大")

    def test_returns_fewer_when_pool_exhausted(self):
        pool = [_item(i) for i in range(5)]
        with FakeCheck(bad_fn=lambda u: True):
            sel, dead = Q._collect_valid_items(pool, 0, 3, check_enabled=True)
        self.assertEqual(sel, [])
        self.assertEqual(len(dead), 5)

    def test_never_returns_duplicates(self):
        pool = [_item(i) for i in range(5)]
        sel, _ = Q._collect_valid_items(pool, 0, 5, check_enabled=False)
        urls = [x["url"] for x in sel]
        self.assertEqual(len(urls), len(set(urls)))

    def test_all_dead_across_whole_pool_terminates(self):
        """全池死链时必须能终止，不能无限循环。"""
        pool = [_item(i) for i in range(30)]
        with FakeCheck(bad_fn=lambda u: True) as f:
            sel, dead = Q._collect_valid_items(pool, 0, 2, check_enabled=True)
        self.assertEqual(sel, [])
        self.assertEqual(len(dead), 30)
        self.assertLessEqual(sum(len(c) for c in f.calls), 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
